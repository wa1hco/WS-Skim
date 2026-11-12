# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""FT8 / FT4 mode: run the Costas sync search on each finished UTC period.

Signal flow
-----------
Input: the engine's wideband IQ ring (``_iq_ring``, complex64, the radio's
sample rate, centred on ``center_freq_mhz``; post-blanker, which is Bypass
by default in these modes).  Nothing upstream changes.

Once a period has ended plus ``TAIL_S`` (so late starters are complete),
:func:`maybe_dispatch_period` copies ``[period start - HEAD_S, period end +
TAIL_S)`` out of the ring on the radio thread and hands it to a worker
thread that runs :func:`costas_search.search` with ``t0_s = -HEAD_S``.

Output: :class:`PeriodResult` on ``engine._ft8_result`` — the sync score
map (downsampled for display), candidates, and ~2.8 kHz jt9 sections — is
published as soon as the search ends.  The same worker then runs jt9 on each
section (:mod:`ft8_decode`) and publishes a second ``PeriodResult`` with the
decodes.  Each decode also goes to ``engine._decode_queue`` (the main decode
panel, WSJT-X UDP and PSKReporter, as for MSK144) and to ``decodes.jsonl``.

No change to timing, phase or scaling of the MSK144 path: in FT8/FT4 mode
that path is skipped entirely (see ``processing.process_iq_data``).
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import numpy as np

from . import costas_search as cs
from . import ft8_decode

log = logging.getLogger(__name__)

#: Modes the app can run in.  MSK144 is the original burst decoder.
MODES = ("MSK144", "FT8", "FT4")
DEFAULT_MODE = "MSK144"

#: IQ before the period start handed to the search (covers DT down to -2.5 s).
HEAD_S = 2.0
#: Wait this long after the period ends, and include it (late DT up to +2.5 s
#: means a signal can run ~0.6 s past the period end).
TAIL_S = 1.0
#: Display map: at most this many frequency columns (max-pooled).
DISPLAY_COLUMNS = 2400
#: Keep this far inside the IQ band edges (fraction of the sample rate).
EDGE_FRACTION = 0.45


@dataclass
class PeriodResult:
    mode: str
    period_start_utc: float
    center_freq_mhz: float
    sample_rate: float
    compute_s: float
    candidates: list
    sections: list
    #: Display map: score per (lag, column), noise ~ 1; rows = start times.
    map_score: np.ndarray = field(repr=False)
    map_freqs_hz: np.ndarray = field(repr=False)   # column centre, IQ offset
    map_start_s: np.ndarray = field(repr=False)    # row start time vs period start
    data_fraction: float = 1.0                     # share of the period present
    #: jt9 decodes (``ft8_decode.Decode``), filled in after the search.
    decodes: list = field(default_factory=list)
    #: False when jt9 was not run (not found, or the search failed).
    decoded: bool = False
    #: Why there is no decode, for the window ("" while decoding or done).
    decode_note: str = ""


def is_period_mode(mode: str) -> bool:
    return mode in ("FT8", "FT4")


def downsample_map(sync: np.ndarray, freqs: np.ndarray, baseline: float,
                   columns: int = DISPLAY_COLUMNS) -> tuple[np.ndarray, np.ndarray]:
    """Max-pool the sync map across frequency to ≤ ``columns`` and normalise.

    Max (not mean) so a narrow signal keeps its peak value in the picture.
    Returns ``(map, column_freqs)``.
    """
    n_lags, n_f = sync.shape
    step = max(1, math.ceil(n_f / columns))
    n_cols = n_f // step
    if n_cols == 0:
        return np.zeros((n_lags, 0), np.float32), np.zeros(0)
    trimmed = sync[:, :n_cols * step].reshape(n_lags, n_cols, step)
    out = (trimmed.max(axis=2) / baseline).astype(np.float32)
    col_f = freqs[:n_cols * step].reshape(n_cols, step).mean(axis=1)
    return out, col_f


def search_period(iq: np.ndarray, fs: float, mode: str, *, t0_s: float,
                  period_start_utc: float, center_freq_mhz: float,
                  data_fraction: float = 1.0) -> PeriodResult:
    """Run the Costas search on one period of complex IQ (pure DSP, no engine)."""
    spec = cs.MODES[mode]
    lo = -EDGE_FRACTION * fs
    hi = EDGE_FRACTION * fs - spec.bandwidth_hz
    t = time.perf_counter()
    res = cs.search(iq, fs, spec, t0_s=t0_s, freq_range_hz=(lo, hi))
    sections = cs.group_into_sections(res.candidates, spec)
    m, mf = downsample_map(res.sync, res.freqs_hz, res.baseline)
    return PeriodResult(
        mode=mode, period_start_utc=period_start_utc,
        center_freq_mhz=center_freq_mhz, sample_rate=fs,
        compute_s=time.perf_counter() - t,
        candidates=res.candidates, sections=sections,
        map_score=m, map_freqs_hz=mf, map_start_s=np.asarray(res.start_times_s),
        data_fraction=data_fraction,
    )


def maybe_dispatch_period(eng) -> None:
    """Called once per ``process_iq_data`` chunk (radio thread).  Cheap no-op
    unless the engine is in FT8/FT4 mode and a new period is ready."""
    mode = getattr(eng, "mode", DEFAULT_MODE)
    if not is_period_mode(mode):
        return
    tb = getattr(eng, "timebase", None)
    if tb is None or not tb.is_anchored():
        return
    if getattr(eng, "_ft8_busy", False) or getattr(eng, "_background_stopping", False):
        return
    spec = cs.MODES[mode]
    period = spec.period_s
    fs = float(eng.sample_rate)
    now = tb.utc_at(eng._iq_abs_sample)
    k = math.floor((now - TAIL_S) / period) - 1
    if k <= getattr(eng, "_ft8_last_period", -1):
        return
    eng._ft8_last_period = k

    p0 = k * period
    want0 = tb.sample_at(p0 - HEAD_S)
    want1 = tb.sample_at(p0 + period + TAIL_S)
    ring_n = eng._iq_ring.shape[0]
    have0 = max(want0, eng._iq_abs_sample - ring_n)
    have1 = min(want1, eng._iq_abs_sample)
    n = have1 - have0
    need = int(0.6 * spec.duration_s * fs)
    if n < need:
        return                                   # not enough of this period (cold start)
    from .detection import _read_ring
    iq = _read_ring(eng._iq_ring, eng._iq_ring_pos, eng._iq_abs_sample, have0, n)
    iq = np.ascontiguousarray(iq[:, 0])          # H; dual-pol V is not used yet
    t0_s = (have0 - tb.sample_at(p0)) / fs
    frac = n / max(1, want1 - want0)
    center = float(eng.center_freq_mhz)

    jt9 = _jt9_path(eng)

    def work():
        try:
            r = search_period(iq, fs, mode, t0_s=t0_s, period_start_utc=p0,
                              center_freq_mhz=center, data_fraction=frac)
            eng._ft8_result = r
            log.info("[ft8] %s period %s: %d candidates, %d sections, %.2f s",
                     mode, time.strftime("%H:%M:%S", time.gmtime(p0)),
                     len(r.candidates), len(r.sections), r.compute_s)
            if not jt9 and r.sections:
                eng._ft8_result = replace(
                    r, decode_note="jt9 not found: install WSJT-X to decode")
            elif jt9 and r.sections:
                t = time.perf_counter()
                ds = ft8_decode.decode_period(
                    iq, fs, mode, r.sections, t0_s=t0_s, period_start_utc=p0,
                    center_freq_mhz=center, jt9=jt9,
                    depth=ft8_decode.depth_for(len(r.sections)))
                eng._ft8_result = replace(r, decodes=ds, decoded=True)
                log.info("[ft8] %s period %s: %d decodes, jt9 %.2f s", mode,
                         time.strftime("%H:%M:%S", time.gmtime(p0)), len(ds),
                         time.perf_counter() - t)
                publish_decodes(eng, ds)
        except Exception:
            log.exception("[ft8] search/decode failed")
        finally:
            eng._ft8_busy = False

    eng._ft8_busy = True
    th = threading.Thread(target=work, daemon=True, name="ft8-search")
    eng._ft8_thread = th
    th.start()


def _jt9_path(eng) -> str | None:
    """jt9 to run (``eng._ft8_jt9`` overrides discovery, for tests)."""
    if hasattr(eng, "_ft8_jt9"):
        return eng._ft8_jt9
    from .detection import find_jt9
    return find_jt9()


def decode_record(d) -> dict:
    """The decode-queue / ``decodes.jsonl`` dict for one ``ft8_decode.Decode``."""
    when = datetime.fromtimestamp(d.period_start_utc, tz=timezone.utc)
    return {
        "outcome": "decoded", "decoded": True, "decoder": "jt9",
        "marker_id": -1, "mode": d.mode, "message": d.message,
        "radio_khz": d.rf_hz / 1000.0, "audio_hz": d.audio_hz,
        "dial_khz": d.dial_hz / 1000.0, "jt9_snr": d.snr_db, "t_sec": d.dt_s,
        "utc_time": when.strftime("%H:%M:%S"),
        "timestamp": when.strftime("%Y-%m-%d_%H:%M:%S"),
    }


def publish_decodes(eng, decodes) -> None:
    """Send decodes to the main decode panel / reporter and ``decodes.jsonl``."""
    q = getattr(eng, "_decode_queue", None)
    lines = []
    for d in decodes:
        rec = decode_record(d)
        if q is not None:
            q.put(rec)
        lines.append(json.dumps({
            "timestamp": rec["timestamp"], "period_ts": rec["timestamp"],
            "mode": d.mode, "radio_khz": round(rec["radio_khz"], 3),
            "audio_hz": d.audio_hz, "dt_s": d.dt_s, "message": d.message,
            "jt9_snr_db": d.snr_db,
        }))
    if not lines:
        return
    try:
        from .detection import production_output_dir
        out = production_output_dir()
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "decodes.jsonl", "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        log.warning("[ft8] could not write decodes.jsonl: %s", e)
