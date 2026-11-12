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


def dial_section(dial_offset_hz: float | None, fs: float, spec) -> cs.Section | None:
    """A section with audio 0 Hz at the band's dial frequency (IQ offset), or
    None if the dial's 200..3200 Hz jt9 range is not inside the search band."""
    if dial_offset_hz is None:
        return None
    lo = dial_offset_hz + ft8_decode.JT9_LOW_HZ
    hi = dial_offset_hz + ft8_decode.JT9_HIGH_HZ
    if lo < -EDGE_FRACTION * fs or hi > EDGE_FRACTION * fs:
        return None
    return cs.Section(base_hz=float(dial_offset_hz), low_audio_hz=float(ft8_decode.JT9_LOW_HZ),
                      high_audio_hz=float(ft8_decode.JT9_HIGH_HZ) - spec.bandwidth_hz,
                      candidates=[])


def with_dial_section(sections: list, dial: cs.Section | None,
                      same_hz: float = 300.0) -> list:
    """``sections`` with ``dial`` first, unless one already starts within
    ``same_hz`` of it.  jt9 searches 200..3200 Hz audio in every section, so
    the dial section decodes the whole FT8 sub-band even when the Costas
    candidates there are crowded out by stronger ones elsewhere."""
    if dial is None or any(abs(s.base_hz - dial.base_hz) <= same_hz for s in sections):
        return list(sections)
    return [dial] + list(sections)


def standard_dials_mhz(mode: str) -> list[float]:
    """The band pulldown's standard dial frequencies for ``mode`` (MHz)."""
    try:
        from .ui import BANDS_BY_MODE
        return [f for _label, f, _src in BANDS_BY_MODE.get(mode, [])]
    except Exception:                       # no Qt: fall back to none
        return []


def pick_dial_mhz(mode: str, center_mhz: float, fs: float,
                  setting_mhz: float | None) -> float | None:
    """Dial frequency for the always-decoded section.

    The band setting wins when it is inside the IQ band; otherwise the
    standard ``mode`` dial nearest the IQ centre that is inside (the band
    setting can lag the radio, e.g. still on 6 m MSK144 50.260 while the
    pan is on 40 m).  None if no dial fits.
    """
    def inside(f):
        off = (f - center_mhz) * 1e6
        return (off + ft8_decode.JT9_LOW_HZ >= -EDGE_FRACTION * fs
                and off + ft8_decode.JT9_HIGH_HZ <= EDGE_FRACTION * fs)
    if setting_mhz and inside(float(setting_mhz)):
        return float(setting_mhz)
    fits = [f for f in standard_dials_mhz(mode) if inside(f)]
    return min(fits, key=lambda f: abs(f - center_mhz)) if fits else None


def search_period(iq: np.ndarray, fs: float, mode: str, *, t0_s: float,
                  period_start_utc: float, center_freq_mhz: float,
                  data_fraction: float = 1.0,
                  dial_offset_hz: float | None = None) -> PeriodResult:
    """Run the Costas search on one period of complex IQ (pure DSP, no engine).

    ``dial_offset_hz``: the band's FT8/FT4 dial frequency relative to the IQ
    centre; when inside the band, a jt9 section starting there is always
    included (see :func:`with_dial_section`).
    """
    spec = cs.MODES[mode]
    lo = -EDGE_FRACTION * fs
    hi = EDGE_FRACTION * fs - spec.bandwidth_hz
    t = time.perf_counter()
    res = cs.search(iq, fs, spec, t0_s=t0_s, freq_range_hz=(lo, hi))
    sections = with_dial_section(cs.group_into_sections(res.candidates, spec),
                                 dial_section(dial_offset_hz, fs, spec))
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
    dial = pick_dial_mhz(mode, center, fs, getattr(eng, "calling_freq_mhz", None))
    dial_off = (dial - center) * 1e6 if dial else None
    timing = timing_snapshot(eng, tb)
    if getattr(eng, "_ft8_save_iq", False):
        save_period_iq(iq, fs=fs, t0_s=t0_s, period_start_utc=p0, mode=mode,
                       center_freq_mhz=center, timing=timing)

    jt9 = _jt9_path(eng)
    hms = time.strftime("%H:%M:%S", time.gmtime(p0))

    def work():
        try:
            r = search_period(iq, fs, mode, t0_s=t0_s, period_start_utc=p0,
                              center_freq_mhz=center, data_fraction=frac,
                              dial_offset_hz=dial_off)
            eng._ft8_result = r
            log.info("[ft8] %s period %s: %d candidates, %d sections, %.2f s; "
                     "data %.0f%%, backlog %.2f s, queue %s pkts, drops %s, missed %s",
                     mode, hms, len(r.candidates), len(r.sections), r.compute_s,
                     100 * frac, timing["backlog_s"], timing["queue_pkts"],
                     timing["drops"], timing["missed"])
            log.info("[ft8] %s period %s: sections at %s kHz", mode, hms,
                     " ".join(f"{(center * 1e6 + s.base_hz) / 1e3:.1f}" for s in r.sections))
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
                dts = sorted(d.dt_s for d in ds)
                log.info("[ft8] %s period %s: %d decodes, jt9 %.2f s, DT median %s",
                         mode, hms, len(ds), time.perf_counter() - t,
                         f"{dts[len(dts) // 2]:+.2f} s" if dts else "-")
                publish_decodes(eng, ds)
        except Exception:
            log.exception("[ft8] search/decode failed")
        finally:
            eng._ft8_busy = False

    eng._ft8_busy = True
    th = threading.Thread(target=work, daemon=True, name="ft8-search")
    eng._ft8_thread = th
    th.start()


#: Diagnostics → Save FT8 period IQ keeps at most this many files (~7 MB each
#: at 48 kHz FT8), oldest deleted first.
SAVE_IQ_KEEP = 40


def timing_snapshot(eng, tb) -> dict:
    """How far the radio thread runs behind the radio, for the period log.

    ``backlog_s`` = now minus the UTC stamped on the newest processed sample.
    With arrival-stamped sources (Flex) that is the processing backlog; with
    "now"-stamped sources it reads ~0 by construction.  ``queue_pkts`` is the
    Flex packet queue depth; ``drops`` / ``missed`` the VITA receiver's
    queue-full drops and sequence gaps since connect (None for other sources).
    """
    out = {"backlog_s": float("nan"), "queue_pkts": None, "drops": None, "missed": None}
    try:
        out["backlog_s"] = time.time() - tb.utc_at(eng._iq_abs_sample)
    except Exception:
        pass
    rc = getattr(eng, "radio_client", None) if getattr(eng, "source_mode", "") == "radio" else None
    if rc is not None:
        q = getattr(rc, "sample_queue", None)
        out["queue_pkts"] = q.qsize() if q is not None else None
        vita = getattr(rc, "_vita", None)
        out["drops"] = getattr(vita, "drop_count", None)
        out["missed"] = getattr(vita, "missed_count", None)
    return out


def save_period_iq(iq, *, fs, t0_s, period_start_utc, mode, center_freq_mhz, timing,
                   out_dir=None, keep: int = SAVE_IQ_KEEP):
    """Write one period's search input to ``<output>/ft8_iq/<UTC>.npz`` for
    offline replay (``search_period`` / ``decode_period`` take the same
    arguments).  Never raises: a full disk must not stop decoding."""
    try:
        if out_dir is None:
            from .detection import production_output_dir
            out_dir = production_output_dir() / "ft8_iq"
        from pathlib import Path
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d_%H%M%S", time.gmtime(period_start_utc))
        np.savez(out_dir / f"{name}_{mode}.npz", iq=np.asarray(iq, np.complex64),
                 fs=fs, t0_s=t0_s, period_start_utc=period_start_utc, mode=mode,
                 center_freq_mhz=center_freq_mhz,
                 backlog_s=timing.get("backlog_s", float("nan")))
        files = sorted(out_dir.glob("*.npz"))
        for f in files[:max(0, len(files) - keep)]:
            f.unlink(missing_ok=True)
    except Exception as e:
        log.warning("[ft8] could not save period IQ: %s", e)


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
