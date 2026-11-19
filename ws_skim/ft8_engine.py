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
#: jt9 sections ("regions"): the dial (audio 0 Hz) is a whole kHz, so a
#: region is named by its kHz within the band (7074 -> "74") and the WSJT-X
#: UDP dial matches what operators announce.  Every Costas candidate's whole
#: signal sits in SECTION_AUDIO_LOW_HZ..SECTION_AUDIO_HIGH_HZ audio, inside
#: jt9's 200..3200 Hz search (ft8_decode.JT9_LOW_HZ / JT9_HIGH_HZ).
SECTION_SNAP_HZ = 1000.0
SECTION_AUDIO_LOW_HZ = 200.0
SECTION_AUDIO_HIGH_HZ = 3100.0


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
    #: Sync candidates set aside by the decode-history filter (no recent
    #: decodes nearby and not strong enough); not sent to jt9.
    dropped_candidates: list = field(default_factory=list)
    #: True when this period searched everything (start-up, or a periodic
    #: probe for new activity), False when the decode-history filter applied.
    full_scan: bool = True


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


# ── Decode-history candidate filter ─────────────────────────────────────────
#
# On a busy HF band the Costas search also scores RTTY, SSB and other
# digital traffic (its sync score is a ratio, so any strong narrow energy
# can look Costas-like): on 40 m about 80% of candidates sit far from any
# FT8 activity, open 8+ extra jt9 sections, and push jt9 down to depth 2.
# Real FT8 lives where FT8 has recently been decoded, so:
#
# * a candidate within ``ACTIVE_NEAR_HZ`` of a decode in the last
#   ``HISTORY_PERIODS`` periods (same mode, same centre) is used as before;
# * any other candidate needs ``COLD_SCORE`` (a strong signal) to open or
#   join a section;
# * a section that would hold only one weak candidate (score below
#   ``SINGLE_MIN_FACTOR`` x the mode threshold) away from activity is
#   dropped;
# * every ``PROBE_EVERY`` periods (4 min of FT8), two consecutive periods (both T/R
#   parities) search everything, as does start-up until ``WARM_PERIODS``
#   periods have been decoded, so new activity is still found.
#
# jt9 searches 200-3200 Hz audio in every kept section regardless of the
# candidates, so weak stations near activity are still decoded, and the
# band's dial section is always kept.  Without jt9 (no decodes) the filter
# never engages.

#: Periods of decode history kept per (mode, centre): 2 min of FT8.
HISTORY_PERIODS = 8
#: A candidate this close (tone 0, Hz) to a recent decode is "active".
ACTIVE_NEAR_HZ = 500.0
#: Score a candidate away from recent activity needs (noise ~ 1).  Real
#: 40 m captures put the 95th percentile of non-FT8 candidates near 25.
COLD_SCORE = {"FT8": 30.0, "FT4": 20.0}
#: A one-candidate section away from activity needs this x threshold.
SINGLE_MIN_FACTOR = 2.0
#: Decoded periods needed before the filter engages.
WARM_PERIODS = 2
#: Full-scan probe: periods ``k % PROBE_EVERY in (0, 1)`` search everything
#: (FT8: 30 s of every 4 min).
PROBE_EVERY = 16


class DecodeHistory:
    """Recent decode frequencies (RF Hz) per period, for one mode and centre.

    A change of mode or centre frequency (band change, re-tune) clears it,
    which restarts the warm-up full scans.
    """

    def __init__(self, periods: int = HISTORY_PERIODS):
        self.periods = int(periods)
        self.key: tuple | None = None
        self._by_period: dict[int, np.ndarray] = {}

    @staticmethod
    def _key(mode: str, center_mhz: float) -> tuple:
        return (mode, round(float(center_mhz), 4))       # 100 Hz re-tune tolerance

    def _sync_key(self, mode: str, center_mhz: float) -> None:
        k = self._key(mode, center_mhz)
        if k != self.key:
            self.key = k
            self._by_period.clear()

    def observe(self, mode: str, center_mhz: float, period_index: int, decodes) -> None:
        """Record one decoded period's decodes (an empty list counts too)."""
        self._sync_key(mode, center_mhz)
        self._by_period[int(period_index)] = np.sort(np.asarray(
            [float(d.rf_hz) for d in decodes], dtype=float))
        newest = max(self._by_period)
        for k in [k for k in self._by_period if k <= newest - self.periods]:
            del self._by_period[k]

    def decoded_periods(self, mode: str, center_mhz: float) -> int:
        self._sync_key(mode, center_mhz)
        return len(self._by_period)

    def active_rf_hz(self, mode: str, center_mhz: float) -> np.ndarray:
        """Sorted RF (Hz) of every decode in the history window."""
        self._sync_key(mode, center_mhz)
        if not self._by_period:
            return np.zeros(0)
        return np.sort(np.concatenate(list(self._by_period.values())))

    def is_warm(self, mode: str, center_mhz: float) -> bool:
        return self.decoded_periods(mode, center_mhz) >= WARM_PERIODS


def is_probe_period(period_index: int) -> bool:
    """Full-scan probe periods: two in a row (both parities) every PROBE_EVERY."""
    return int(period_index) % PROBE_EVERY in (0, 1)


def near_activity(rf_hz, active_rf_hz: np.ndarray, near_hz: float = ACTIVE_NEAR_HZ) -> np.ndarray:
    """Bool per ``rf_hz``: within ``near_hz`` of any ``active_rf_hz``."""
    rf = np.atleast_1d(np.asarray(rf_hz, dtype=float))
    if active_rf_hz.size == 0 or rf.size == 0:
        return np.zeros(rf.shape, dtype=bool)
    i = np.searchsorted(active_rf_hz, rf)
    lo = np.abs(rf - active_rf_hz[np.clip(i - 1, 0, active_rf_hz.size - 1)])
    hi = np.abs(active_rf_hz[np.clip(i, 0, active_rf_hz.size - 1)] - rf)
    return np.minimum(lo, hi) <= near_hz


def filter_candidates(candidates, spec, center_mhz: float, active_rf_hz: np.ndarray,
                      *, full_scan: bool) -> tuple[list, list]:
    """Split candidates into (used, set aside) by recent decode activity.

    ``full_scan`` (start-up / probe periods) keeps every candidate.  Otherwise
    a candidate near a recent decode is kept at the normal threshold and any
    other needs ``COLD_SCORE``.
    """
    cands = list(candidates)
    if full_scan or not cands:
        return cands, []
    rf = np.array([center_mhz * 1e6 + c.freq_hz for c in cands])
    near = near_activity(rf, active_rf_hz)
    cold = COLD_SCORE.get(spec.name, 30.0)
    used, dropped = [], []
    for c, n in zip(cands, near):
        (used if n or c.score >= cold else dropped).append(c)
    return used, dropped


def drop_weak_single_sections(sections, spec, center_mhz: float,
                              active_rf_hz: np.ndarray) -> tuple[list, list]:
    """Drop sections holding one weak candidate away from recent activity.

    Returns ``(kept sections, candidates of the dropped sections)``.
    """
    kept, dropped = [], []
    weak = SINGLE_MIN_FACTOR * spec.threshold
    for sec in sections:
        c = sec.candidates
        if len(c) == 1 and c[0].score < weak and not near_activity(
                center_mhz * 1e6 + c[0].freq_hz, active_rf_hz)[0]:
            dropped.extend(c)
            continue
        kept.append(sec)
    return kept, dropped


def search_period(iq: np.ndarray, fs: float, mode: str, *, t0_s: float,
                  period_start_utc: float, center_freq_mhz: float,
                  data_fraction: float = 1.0,
                  dial_offset_hz: float | None = None,
                  history: DecodeHistory | None = None) -> PeriodResult:
    """Run the Costas search on one period of complex IQ (pure DSP, no engine).

    ``dial_offset_hz``: the band's FT8/FT4 dial frequency relative to the IQ
    centre; when inside the band, a jt9 section starting there is always
    included (see :func:`with_dial_section`).

    ``history``: recent decodes for the decode-history candidate filter
    (see :func:`filter_candidates`); None searches everything, as before.
    """
    spec = cs.MODES[mode]
    lo = -EDGE_FRACTION * fs
    hi = EDGE_FRACTION * fs - spec.bandwidth_hz
    t = time.perf_counter()
    res = cs.search(iq, fs, spec, t0_s=t0_s, freq_range_hz=(lo, hi))
    k = int(math.floor(period_start_utc / spec.period_s + 1e-9))
    if history is None:
        full, active = True, np.zeros(0)
    else:
        full = not history.is_warm(mode, center_freq_mhz) or is_probe_period(k)
        active = history.active_rf_hz(mode, center_freq_mhz)
    used, dropped = filter_candidates(res.candidates, spec, center_freq_mhz, active,
                                      full_scan=full)
    sections = cs.group_into_sections(
        used, spec, center_hz=center_freq_mhz * 1e6, snap_hz=SECTION_SNAP_HZ,
        audio_floor_hz=SECTION_AUDIO_LOW_HZ,
        width_hz=SECTION_AUDIO_HIGH_HZ - SECTION_AUDIO_LOW_HZ)
    sections, lone = drop_weak_single_sections(sections, spec, center_freq_mhz, active)
    if lone:
        ids = {id(c) for c in lone}
        used = [c for c in used if id(c) not in ids]
        dropped = dropped + lone
    sections = with_dial_section(sections, dial_section(dial_offset_hz, fs, spec))
    m, mf = downsample_map(res.sync, res.freqs_hz, res.baseline)
    return PeriodResult(
        mode=mode, period_start_utc=period_start_utc,
        center_freq_mhz=center_freq_mhz, sample_rate=fs,
        compute_s=time.perf_counter() - t,
        candidates=used, sections=sections,
        map_score=m, map_freqs_hz=mf, map_start_s=np.asarray(res.start_times_s),
        data_fraction=data_fraction,
        dropped_candidates=dropped, full_scan=full,
    )


#: Skip a period when less than this share of it has real IQ (the rest was
#: zero-filled because nothing arrived, e.g. while transmitting).
MIN_REAL_FRACTION = 0.6


def gap_samples(gaps, a: int, b: int) -> int:
    """Samples of ``[a, b)`` covered by the zero-filled ``gaps`` ((start, end) pairs)."""
    n = 0
    for g0, g1 in gaps:
        n += max(0, min(b, g1) - max(a, g0))
    return n


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

    # IQ that never arrived (the radio transmitting, a network outage) is
    # zero-filled in the ring by process_iq_data; a period that is mostly
    # gap has nothing to decode.
    gap_n = gap_samples(getattr(eng, "_iq_gaps", ()), tb.sample_at(p0),
                        tb.sample_at(p0 + period))
    real_frac = 1.0 - gap_n / max(1.0, period * fs)
    if real_frac < MIN_REAL_FRACTION:
        log.info("[ft8] %s period %s: skipped, %.0f%% of it had no IQ (transmitting?)",
                 mode, hms, 100 * (1.0 - real_frac))
        return
    frac = min(frac, real_frac)
    history = getattr(eng, "_ft8_history", None)
    if history is None:
        history = eng._ft8_history = DecodeHistory()

    def work():
        try:
            r = search_period(iq, fs, mode, t0_s=t0_s, period_start_utc=p0,
                              center_freq_mhz=center, data_fraction=frac,
                              dial_offset_hz=dial_off, history=history)
            eng._ft8_result = r
            log.info("[ft8] %s period %s: %d candidates (%d set aside, %s), %d sections, "
                     "%.2f s; data %.0f%%, backlog %.2f s, queue %s pkts, drops %s, missed %s",
                     mode, hms, len(r.candidates), len(r.dropped_candidates),
                     "full scan" if r.full_scan else "history filter",
                     len(r.sections), r.compute_s,
                     100 * frac, timing["backlog_s"], timing["queue_pkts"],
                     timing["drops"], timing["missed"])
            log.info("[ft8] %s period %s: sections at %s kHz", mode, hms,
                     " ".join(f"{(center * 1e6 + s.base_hz) / 1e3:.0f}" for s in r.sections))
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
                history.observe(mode, center, k, ds)
                dts = sorted(d.dt_s for d in ds)
                log.info("[ft8] %s period %s: %d decodes, jt9 depth %d %.2f s, DT median %s",
                         mode, hms, len(ds), ft8_decode.depth_for(len(r.sections)),
                         time.perf_counter() - t,
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
        "low_confidence": bool(getattr(d, "low_confidence", False)),
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
