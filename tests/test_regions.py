# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8/FT4 regions: whole-kHz section dials, kHz-in-band names, and only
active regions sent to WS Hub over WSJT-X UDP."""
from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ws_skim import costas_search as cs  # noqa: E402
from ws_skim import ft8_engine as fe  # noqa: E402
from ws_skim import ft8_decode  # noqa: E402
from ws_skim.wsjtx_udp import (  # noqa: E402
    MSG_CLEAR, MSG_CLOSE, MSG_DECODE, MSG_HEARTBEAT, MSG_STATUS, parse, region_name,
)

FS = 48000.0


def _cand(off_hz, score=5.0):
    return cs.Candidate(-score, freq_hz=off_hz, dt_s=0.1, score=score)


def _snapped(cands, spec, center_hz):
    return cs.group_into_sections(
        cands, spec, center_hz=center_hz, snap_hz=fe.SECTION_SNAP_HZ,
        audio_floor_hz=fe.SECTION_AUDIO_LOW_HZ,
        width_hz=fe.SECTION_AUDIO_HIGH_HZ - fe.SECTION_AUDIO_LOW_HZ)


# ── whole-kHz section dials ─────────────────────────────────────────────

@pytest.mark.parametrize("spec", [cs.FT8, cs.FT4])
@pytest.mark.parametrize("center_hz", [7_060_123.4, 14_074_000.0, 50_313_777.0])
def test_snapped_sections_have_whole_khz_dials_and_fit_jt9(spec, center_hz):
    rng = np.random.default_rng(int(center_hz) % 1000)
    offs = sorted(rng.uniform(-20_000, 20_000, 60))
    secs = _snapped([_cand(f) for f in offs], spec, center_hz)
    assert sum(len(s.candidates) for s in secs) == len(offs)
    for s in secs:
        dial = center_hz + s.base_hz
        assert abs(dial / 1000.0 - round(dial / 1000.0)) < 1e-6      # whole kHz
        audio = [c.freq_hz - s.base_hz for c in s.candidates]
        assert fe.SECTION_AUDIO_LOW_HZ <= min(audio) < fe.SECTION_AUDIO_LOW_HZ + 1000.0
        assert max(audio) + spec.bandwidth_hz <= fe.SECTION_AUDIO_HIGH_HZ + 1e-6
        # Inside the 200..3200 Hz jt9 search.
        assert min(audio) >= ft8_decode.JT9_LOW_HZ
        assert max(audio) + spec.bandwidth_hz <= ft8_decode.JT9_HIGH_HZ


def test_snap_floor_example():
    # Lowest signal at 7074.150 kHz: 7074.150 - 0.200 = 7073.950 -> dial 7073.
    center = 7_060_000.0
    secs = _snapped([_cand(14_150.0)], cs.FT8, center)
    assert center + secs[0].base_hz == pytest.approx(7_073_000.0)
    # At 7074.200 the signal's audio is exactly 200 Hz on dial 7074.
    secs = _snapped([_cand(14_200.0)], cs.FT8, center)
    assert center + secs[0].base_hz == pytest.approx(7_074_000.0)


def test_unsnapped_sections_unchanged():
    secs = cs.group_into_sections([_cand(100.0)], cs.FT8)
    assert secs[0].base_hz == pytest.approx(100.0 - 300.0)


def test_search_period_sections_are_whole_khz_and_dial_section_merges():
    spec = cs.FT8
    center_mhz = 7.0601234
    rng = np.random.default_rng(5)
    n = int((fe.HEAD_S + spec.period_s + fe.TAIL_S) * FS)
    x = cs.synth_noise(FS, n, rng=rng)
    sigs = [-9_000.0, -7_500.0, 14_300.0, 15_900.0]       # 15.9 kHz ~ 7076.0 kHz
    for f in sigs:
        x += cs.synth_signal(spec, FS, n, freq_hz=f, dt_s=0.2, snr_db=-6,
                             t0_s=-fe.HEAD_S, rng=rng)
    dial_off = (7.074 - center_mhz) * 1e6
    r = fe.search_period(x, FS, "FT8", t0_s=-fe.HEAD_S, period_start_utc=15.0 * 1000,
                         center_freq_mhz=center_mhz, dial_offset_hz=dial_off)
    dials = [center_mhz * 1e6 + s.base_hz for s in r.sections]
    assert dials, "no sections"
    for d in dials:
        assert abs(d / 1000.0 - round(d / 1000.0)) < 1e-6
    names = sorted(region_name(d) for d in dials)
    assert "74" in names and len(set(names)) == len(names)
    # Every signal is inside some section's jt9 window; RF stays exact.
    for f in sigs:
        assert any(s.base_hz + 200 <= f and f + spec.bandwidth_hz <= s.base_hz + 3200
                   for s in r.sections)


# ── Activity view labels ────────────────────────────────────────────────

def test_activity_sections_named_by_khz_in_band():
    from ws_skim import activity_window as aw
    sec = cs.Section(base_hz=7_074_000.0 - 7_060_000.0, low_audio_hz=200.0,
                     high_audio_hz=3000.0, candidates=[])
    sec2 = cs.Section(base_hz=7_059_000.0 - 7_060_000.0, low_audio_hz=200.0,
                      high_audio_hz=3000.0, candidates=[])
    res = SimpleNamespace(mode="FT8", period_start_utc=1_700_000_010.0,
                          center_freq_mhz=7.060, candidates=[], sections=[sec, sec2],
                          decodes=[], decoded=False)
    e = aw.entry_from_result(res)
    assert e.section_names == ["74", "59"]
    h = aw.ActivityHistory(n_periods=4)
    assert h.add(res)
    assert h.section_names() == ["74", "59"]


# ── UDP: only active regions ────────────────────────────────────────────

def _reporter():
    from ws_skim.reporting import Reporter
    rpt = Reporter()
    rpt.my_call, rpt.my_grid = "WA1HCO", "FN42"
    rpt.wsjtx_enabled = True
    rpt._sock = object()                          # never used: _udp_send is captured
    sent = []
    rpt._udp_send = lambda pkt: sent.append(parse(pkt))
    return rpt, sent


def _ft8(dial_khz, audio_hz=1200.0, mode="FT8"):
    return {"message": "CQ K1ABC FN42", "mode": mode, "jt9_snr": -8, "t_sec": 0.2,
            "utc_time": "18:00:00", "audio_hz": audio_hz, "dial_khz": dial_khz,
            "radio_khz": dial_khz + audio_hz / 1000.0}


def test_no_preannounce_only_decoding_regions():
    rpt, sent = _reporter()
    rpt.set_context(source="radio", center_hz=7_060_000, bandwidth_hz=48_000)
    rpt.report_freq(7_060_000)
    assert sent == [] and rpt.instance_ids() == []
    rpt._handle_decode(_ft8(7074.0))
    assert [m["type"] for m in sent] == [MSG_HEARTBEAT, MSG_STATUS, MSG_DECODE, MSG_STATUS]
    assert {m["id"] for m in sent} == {"SKIM-FLEX-40m-FT8-74"}
    dec = next(m for m in sent if m["type"] == MSG_DECODE)
    assert 7_074_000 + dec["delta_frequency"] == 7_075_200          # RF unchanged
    sent.clear()
    rpt._handle_decode(_ft8(7074.0, audio_hz=900.0))
    assert MSG_HEARTBEAT not in [m["type"] for m in sent]           # already live
    rpt._handle_decode(_ft8(7059.0))
    assert sorted(rpt.instance_ids()) == ["SKIM-FLEX-40m-FT8-59", "SKIM-FLEX-40m-FT8-74"]
    # Heartbeat loop only covers live regions.
    sent.clear()
    rpt._send_all_heartbeats()
    assert sorted(m["id"] for m in sent) == ["SKIM-FLEX-40m-FT8-59", "SKIM-FLEX-40m-FT8-74"]


@pytest.mark.parametrize("mode,limit", [("FT8", 120.0), ("FT4", 60.0), ("MSK144", 120.0)])
def test_idle_region_closed_after_8_periods(mode, limit):
    from ws_skim import reporting
    assert reporting.idle_limit_s(mode) == limit
    rpt, sent = _reporter()
    rpt.set_context(source="radio", center_hz=7_060_000, bandwidth_hz=48_000)
    mid = "SKIM-FLEX-40m-" + mode + "-74"
    rpt._register_instance(mid, 7_074_000, mode, now=1000.0)
    assert rpt._retire_idle(now=1000.0 + limit - 1) == []
    assert rpt.instance_ids() == [mid]
    assert rpt._retire_idle(now=1000.0 + limit) == [mid]
    assert rpt.instance_ids() == []
    assert [m["type"] for m in sent] == [MSG_CLEAR, MSG_CLOSE]
    assert all(m["id"] == mid for m in sent)


def test_new_decode_keeps_region_alive():
    rpt, _sent = _reporter()
    mid = "SKIM-FLEX-40m-FT8-74"
    rpt._register_instance(mid, 7_074_000, "FT8", now=1000.0)
    rpt._register_instance(mid, 7_074_000, "FT8", now=1100.0)
    assert rpt._retire_idle(now=1200.0) == []
    assert rpt._retire_idle(now=1220.0) == [mid]


def test_band_change_closes_regions():
    rpt, sent = _reporter()
    rpt.set_context(source="radio", center_hz=7_060_000, bandwidth_hz=48_000)
    rpt._handle_decode(_ft8(7074.0))
    sent.clear()
    rpt.set_context(center_hz=14_060_000)
    assert rpt.instance_ids() == []
    assert [m["type"] for m in sent] == [MSG_CLEAR, MSG_CLOSE]
    assert sent[0]["id"] == "SKIM-FLEX-40m-FT8-74"
