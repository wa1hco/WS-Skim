# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8 decode-history section filter, and IQ gaps while transmitting."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ws_skim import costas_search as cs  # noqa: E402
from ws_skim import ft8_engine as fe  # noqa: E402
from ws_skim.ft8_decode import Decode  # noqa: E402

FS = 48000.0
CENTER = 7.0783


def _dec(rf_hz, msg="CQ K1ABC FN42"):
    return Decode(mode="FT8", period_start_utc=0.0, snr_db=-5, dt_s=0.1, audio_hz=1000.0,
                  rf_hz=rf_hz, dial_hz=rf_hz - 1000.0, message=msg)


def _cand(off_hz, score):
    return cs.Candidate(-score, freq_hz=off_hz, dt_s=0.1, score=score)


# ── history ─────────────────────────────────────────────────────────────

def test_history_window_warmup_and_retune_reset():
    h = fe.DecodeHistory(periods=4)
    assert not h.is_warm("FT8", CENTER)
    for k in range(10):
        h.observe("FT8", CENTER, k, [_dec(7_075_000.0 + k)])
    assert h.decoded_periods("FT8", CENTER) == 4                 # k = 6..9 kept
    assert h.active_rf_hz("FT8", CENTER).tolist() == [7_075_006.0, 7_075_007.0,
                                                      7_075_008.0, 7_075_009.0]
    assert h.is_warm("FT8", CENTER)
    assert h.decoded_periods("FT8", 14.0783) == 0                # band change clears
    assert not h.is_warm("FT8", 14.0783)
    h.observe("FT8", CENTER, 20, [])                             # empty periods count
    assert h.decoded_periods("FT4", CENTER) == 0                 # mode change clears


def test_probe_periods_cover_both_parities():
    probes = [k for k in range(32) if fe.is_probe_period(k)]
    assert probes == [k for k in range(32) if k % fe.PROBE_EVERY in (0, 1)]
    assert {k % 2 for k in probes} == {0, 1}


def test_near_activity():
    act = np.array([7_074_500.0, 7_076_000.0])
    got = fe.near_activity([7_074_100.0, 7_075_300.0, 7_076_499.0, 7_090_000.0], act)
    assert got.tolist() == [True, False, True, False]
    assert fe.near_activity([1.0], np.zeros(0)).tolist() == [False]


def test_filter_candidates_keeps_active_and_strong():
    active = np.array([CENTER * 1e6 - 3000.0])
    near = _cand(-3200.0, 2.0)                  # 200 Hz from a decode
    weak_far = _cand(+15000.0, 8.0)             # RTTY-ish junk far away
    strong_far = _cand(+12000.0, fe.COLD_SCORE["FT8"] + 1)
    used, dropped = fe.filter_candidates([near, weak_far, strong_far], cs.FT8, CENTER,
                                         active, full_scan=False)
    assert used == [near, strong_far] and dropped == [weak_far]
    used, dropped = fe.filter_candidates([near, weak_far], cs.FT8, CENTER, active,
                                         full_scan=True)
    assert len(used) == 2 and dropped == []


def test_single_weak_section_dropped_unless_active():
    weak = _cand(+15000.0, 2.0)
    pair = [_cand(-9000.0, 2.0), _cand(-8800.0, 2.1)]
    strong = _cand(+5000.0, 9.0)
    secs = cs.group_into_sections([weak, strong] + pair, cs.FT8)
    kept, dropped = fe.drop_weak_single_sections(secs, cs.FT8, CENTER, np.zeros(0))
    assert dropped == [weak] and len(kept) == 2
    act = np.array([CENTER * 1e6 + 15000.0])
    kept, dropped = fe.drop_weak_single_sections(secs, cs.FT8, CENTER, act)
    assert dropped == [] and len(kept) == 3


# ── search_period with history (synthetic band) ─────────────────────────

def _busy_band(seed=3):
    """FT8 cluster near one dial plus FT8-like junk spread across the band.

    The junk stands in for RTTY / SSB / other digital signals that score as
    Costas candidates on a real band: Costas-synced bursts, but at
    frequencies where nothing has been decoded.
    """
    spec = cs.FT8
    rng = np.random.default_rng(seed)
    n = int((fe.HEAD_S + spec.period_s + fe.TAIL_S) * FS)
    x = cs.synth_noise(FS, n, rng=rng)
    cluster = [-4000.0 + 230.0 * i for i in range(10)]          # ~2.3 kHz of FT8
    junk = [6000.0 + 2900.0 * i for i in range(5)] + [-19000.0, -15000.0]
    for f in cluster:
        x += cs.synth_signal(spec, FS, n, freq_hz=f, dt_s=0.2, snr_db=-8, t0_s=-fe.HEAD_S, rng=rng)
    for f in junk:
        x += cs.synth_signal(spec, FS, n, freq_hz=f, dt_s=0.1, snr_db=-16, t0_s=-fe.HEAD_S, rng=rng)
    return x, cluster, junk


def test_history_filter_cuts_sections_but_keeps_the_cluster():
    x, cluster, junk = _busy_band()
    kw = dict(t0_s=-fe.HEAD_S, center_freq_mhz=CENTER)
    p_hist = 15.0 * 1003                      # k = 1003: not a probe period
    assert not fe.is_probe_period(1003)
    before = fe.search_period(x, FS, "FT8", period_start_utc=p_hist, **kw)
    h = fe.DecodeHistory()
    for k in (1001, 1002):
        h.observe("FT8", CENTER, k, [_dec(CENTER * 1e6 + f) for f in cluster])
    after = fe.search_period(x, FS, "FT8", period_start_utc=p_hist, history=h, **kw)
    assert not after.full_scan
    assert len(after.sections) < len(before.sections)
    assert len(after.sections) <= 2
    # Every cluster signal still sits inside a kept section's jt9 range.
    def covered(f):
        return any(s.base_hz + 200 <= f and f + cs.FT8.bandwidth_hz <= s.base_hz + 3200
                   for s in after.sections)
    assert all(covered(f) for f in cluster)
    assert after.dropped_candidates
    assert {id(c) for c in after.candidates}.isdisjoint({id(c) for c in after.dropped_candidates})
    # A probe period searches everything again.
    probe = fe.search_period(x, FS, "FT8", period_start_utc=15.0 * 1008, history=h, **kw)
    assert fe.is_probe_period(1008)
    assert probe.full_scan and len(probe.sections) == len(before.sections)


def test_cold_history_is_a_full_scan():
    x, _cluster, _junk = _busy_band()
    h = fe.DecodeHistory()
    r = fe.search_period(x, FS, "FT8", t0_s=-fe.HEAD_S, period_start_utc=15.0 * 1003,
                         center_freq_mhz=CENTER, history=h)
    assert r.full_scan and not [c for c in r.dropped_candidates if c.score >= 2 * cs.FT8.threshold]


# ── IQ gaps (radio transmitting) ────────────────────────────────────────

T = 1_700_000_010.0          # a period boundary (divisible by 15)
F_SIG = 5000.0


def _engine():
    from ws_skim.engine import Engine
    e = Engine(center_freq_mhz=28.074, sample_rate=int(FS))
    e._ft8_jt9 = None
    e.set_blanker("Bypass")
    e.set_mode("FT8")
    return e


def _signal_track(rng, w0, w_end):
    """Continuous IQ from wall w0 to w_end, FT8 at DT 0.3 in periods T and T+30."""
    spec = cs.FT8
    n = int(round((w_end - w0) * FS))
    x = cs.synth_noise(FS, n, rng=rng)
    for p in (T, T + 30.0):
        x += cs.synth_signal(spec, FS, n, freq_hz=F_SIG, dt_s=0.3, snr_db=-6,
                             t0_s=w0 - p, rng=rng)
    return x


def _run_with_tx(e, x, w0, tx0, tx1, *, silence: bool, chunk=4096):
    """Feed ``x`` as Flex would: arrival-stamped chunks, nothing while transmitting."""
    results = {}
    for s in range(0, len(x), chunk):
        wall = w0 + s / FS
        n = min(chunk, len(x) - s)
        if tx0 <= wall < tx1:
            if silence:
                e.advance_iq_silence(n, wall)
        else:
            e._chunk_rx_wall = wall
            e.process_iq_data(x[s:s + n], 0, 0)
        t = getattr(e, "_ft8_thread", None)
        if t is not None:
            t.join(timeout=60)
        r = e._ft8_result
        if r is not None and r.period_start_utc not in results:
            results[r.period_start_utc] = (r, wall)
    return results


@pytest.mark.parametrize("silence", [False, True])
def test_transmit_gap_keeps_dt_and_skips_tx_period(silence):
    e = _engine()
    rng = np.random.default_rng(9)
    w0, w_end = T - 2.0, T + 47.0
    x = _signal_track(rng, w0, w_end)
    tx0, tx1 = T + 15.2, T + 29.1          # odd-period transmission
    res = _run_with_tx(e, x, w0, tx0, tx1, silence=silence)
    assert T + 15.0 not in res                       # the transmit period is skipped
    for p in (T, T + 30.0):
        assert p in res, sorted(res)
        r, _wall = res[p]
        hit = [c for c in r.candidates if abs(c.freq_hz - F_SIG) <= 12.5]
        assert hit, (p, r.candidates[:3])
        assert hit[0].dt_s == pytest.approx(0.3, abs=0.1)
    tb = e.timebase
    assert tb.utc_at(e._iq_abs_sample) == pytest.approx(w_end, abs=0.2)
    assert e._iq_gaps
    if silence:
        # Clock kept running through the transmission: the period before it
        # is searched about a second after it ends, not after the TX.
        assert res[T][1] < T + 18.0


def test_gap_samples_counts_overlap():
    gaps = [(100, 200), (300, 400)]
    assert fe.gap_samples(gaps, 0, 1000) == 200
    assert fe.gap_samples(gaps, 150, 350) == 100
    assert fe.gap_samples([], 0, 10) == 0
