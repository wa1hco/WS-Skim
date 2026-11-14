# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""WSJT-X-faithful UDP export: field layout, instance ids, and WS Hub ingest.

The parser in ``ws_skim.wsjtx_udp.parse`` is an independent decoder written
from NetworkMessage.hpp (not a copy of WS Hub).  When the WS-Hub tree is
available, a second test round-trips the same datagrams through
``wims.udp.messages.parse``.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import pytest

from ws_skim.wsjtx_udp import (
    HEARTBEAT_INTERVAL_S,
    MAX_SCHEMA,
    MSG_CLEAR,
    MSG_CLOSE,
    MSG_DECODE,
    MSG_HEARTBEAT,
    MSG_STATUS,
    QUINT32_MAX,
    SCHEMA,
    band_label,
    build_clear,
    build_close,
    build_decode,
    build_heartbeat,
    build_status,
    choose_dial,
    instance_id,
    is_low_confidence,
    is_multicast_ip,
    mode_char,
    pack_utf8,
    parse,
    qtime_ms,
    source_tag,
)

# ── Packing / header ────────────────────────────────────────────────────


def test_empty_utf8_is_length_zero_not_null():
    # NetworkMessage.hpp: empty QByteArray is length 0; null is 0xFFFFFFFF.
    # WSJT-X writes QString.toUtf8(), which is empty (not null) for "".
    assert pack_utf8("") == b"\x00\x00\x00\x00"
    assert pack_utf8(None) == b"\xff\xff\xff\xff"
    assert pack_utf8("FT8") == b"\x00\x00\x00\x03FT8"


def test_heartbeat_schema_version_is_ws_skim_not_fake_wsjtx():
    from ws_skim import __version__
    pkt = build_heartbeat(id="SKIM-FLEX-2m-FT8")
    msg = parse(pkt)
    assert msg["type"] == MSG_HEARTBEAT
    assert msg["schema"] == SCHEMA
    assert msg["max_schema"] == MAX_SCHEMA
    assert msg["id"] == "SKIM-FLEX-2m-FT8"
    assert msg["version"] == __version__
    assert msg["version"] != "2.7.0"
    assert HEARTBEAT_INTERVAL_S == 15


def test_status_uses_mode_name_and_is_receive_only():
    pkt = build_status(144_174_000, "WA1HCO", "FN42", mode="FT8",
                       id="SKIM-FLEX-2m-FT8")
    msg = parse(pkt)
    assert msg["type"] == MSG_STATUS
    assert msg["id"] == "SKIM-FLEX-2m-FT8"
    assert msg["dial_frequency"] == 144_174_000
    assert msg["mode"] == "FT8"
    assert msg["tx_mode"] == "FT8"
    assert msg["de_call"] == "WA1HCO" and msg["de_grid"] == "FN42"
    assert msg["tx_enabled"] is False
    assert msg["transmitting"] is False
    assert msg["fast_mode"] is False
    # FT8 hides the T/R and FTol spinners; WSJT-X sends 0xFFFFFFFF.
    assert msg["tr_period"] is None
    assert msg["frequency_tolerance"] is None
    # Empty strings, not null, matching QString.toUtf8().
    assert msg["dx_call"] == ""
    assert msg["tx_message"] == ""


def test_status_msk144_is_fast_with_tr_period():
    pkt = build_status(50_260_000, "WA1HCO", "FN42", mode="MSK144",
                       id="SKIM-B210-6m-MSK144")
    msg = parse(pkt)
    assert msg["mode"] == "MSK144"
    assert msg["fast_mode"] is True
    assert msg["tr_period"] == 15
    assert msg["frequency_tolerance"] == 100


@pytest.mark.parametrize("name,char", [
    ("FT8", "~"), ("FT4", "+"), ("MSK144", "&"), ("Q65", ":"),
    ("JT65", "#"), ("JT9", "@"), ("JT4", "$"), ("FST4", "`"),
])
def test_decode_mode_is_wsjtx_character(name, char):
    assert mode_char(name) == char
    pkt = build_decode({
        "message": "CQ WA1HCO FN42",
        "mode": name,
        "jt9_snr": -10,
        "t_sec": 0.2,
        "utc_time": "12:21:00",
        "audio_hz": 1500,
        "dial_khz": 144174.0,
        "radio_khz": 144175.5,
    }, "WA1HCO", 144_174_000, id="SKIM-FLEX-2m-" + name)
    msg = parse(pkt)
    assert msg["type"] == MSG_DECODE
    assert msg["mode"] == char
    assert msg["message"] == "CQ WA1HCO FN42"
    assert msg["snr"] == -10
    assert msg["new"] is True
    assert msg["off_air"] is False
    # The Decode mode field is the character, not the word.  The id may
    # still contain the mode name (SKIM-FLEX-2m-FT8).
    i = pkt.find(msg["message"].encode()) 
    # mode is the utf8 immediately before the message: u32 len + 1 char.
    # Confirm the character itself is on the wire as a 1-byte utf8 string.
    assert bytes([0, 0, 0, 1]) + char.encode() in pkt


def test_decode_delta_frequency_is_rf_minus_dial():
    # Section dial 7.074, tone at 1200 Hz audio → RF 7.075200 MHz.
    pkt = build_decode({
        "message": "CQ K1ABC FN42",
        "mode": "FT8",
        "jt9_snr": -12,
        "t_sec": 0.3,
        "utc_time": "22:13:30",
        "audio_hz": 1200,
        "dial_hz": 7_074_000,
        "radio_khz": 7075.2,
    }, id="SKIM-FLEX-40m-FT8")
    msg = parse(pkt)
    assert msg["delta_frequency"] == 1200
    # WS Hub computes RF as Status.dial + Decode.delta_frequency.
    assert 7_074_000 + msg["delta_frequency"] == 7_075_200


def test_msk144_qtime_keeps_milliseconds():
    # Period starts at 12:21:00, ping 3.250 s in → 12:21:03.250.
    pkt = build_decode({
        "message": "W1AW WA1HCO FN42",
        "mode": "MSK144",
        "jt9_snr": 5,
        "t_sec": 3.250,
        "utc_time": "12:21:00",
        "radio_khz": 50261.5,   # 50.260 dial + 1500 Hz
    }, id="SKIM-B210-6m-MSK144")
    msg = parse(pkt)
    expected = (12 * 3600 + 21 * 60 + 3) * 1000 + 250
    assert msg["time_ms"] == expected
    assert msg["delta_frequency"] == 1500
    assert msg["mode"] == "&"


def test_ft8_qtime_is_period_start_and_dt_is_separate():
    pkt = build_decode({
        "message": "CQ K1ABC FN42",
        "mode": "FT8",
        "t_sec": 0.4,
        "utc_time": "22:13:30",
        "audio_hz": 800,
        "dial_hz": 14_074_000,
    })
    msg = parse(pkt)
    assert msg["time_ms"] == (22 * 3600 + 13 * 60 + 30) * 1000
    assert msg["delta_time"] == pytest.approx(0.4)
    assert qtime_ms("22:13:30") == msg["time_ms"]


def test_low_confidence_follows_wsjtx_question_mark():
    assert is_low_confidence({"message": "CQ K1ABC FN42"}) is False
    assert is_low_confidence({"message": "CQ K1ABC FN42?"}) is True
    assert is_low_confidence({"message": "CQ K1ABC FN42", "low_confidence": True}) is True
    # a2 alone is AP, not low confidence.
    assert is_low_confidence({"message": "W9XYZ K1ABC RR73", "ap": "a2"}) is False
    pkt = build_decode({
        "message": "CQ DX JA1XYZ PM95?",
        "mode": "FT8",
        "utc_time": "00:00:00",
        "audio_hz": 2010,
        "dial_hz": 14_074_000,
        "low_confidence": True,
    })
    assert parse(pkt)["low_confidence"] is True
    clean = build_decode({
        "message": "CQ K1ABC FN42",
        "mode": "FT8",
        "utc_time": "00:00:00",
        "audio_hz": 1200,
        "dial_hz": 14_074_000,
    })
    assert parse(clean)["low_confidence"] is False


def test_clear_and_close():
    mid = "SKIM-FLEX-2m-FT8"
    c = parse(build_clear(id=mid))
    assert c["type"] == MSG_CLEAR and c["id"] == mid
    k = parse(build_close(id=mid))
    assert k["type"] == MSG_CLOSE and k["id"] == mid


# ── Identity ────────────────────────────────────────────────────────────


def test_source_and_band_tags():
    assert source_tag("radio") == "FLEX"
    assert source_tag("usrp") == "B210"
    assert source_tag("USRP B210") == "B210"
    assert source_tag("airspy") == "AIRSPY"
    assert band_label(144_174_000) == "2m"
    assert band_label(50_313_000) == "6m"
    assert band_label(14_074_000) == "20m"
    assert band_label(432_000_000) == "70cm"


def test_instance_id_primary_vs_alternate_dial():
    assert instance_id("radio", 144_174_000, "FT8") == "SKIM-FLEX-2m-FT8"
    assert instance_id("usrp", 50_260_000, "MSK144") == "SKIM-B210-6m-MSK144"
    # 50.323 is the 6m FT8 DX dial, not the primary 50.313.
    assert instance_id("radio", 50_323_000, "FT8") == "SKIM-FLEX-6m-FT8-50323"
    # Same inputs always produce the same id (stable even if another
    # dial later appears on the same band).
    assert instance_id("radio", 50_313_000, "FT8") == "SKIM-FLEX-6m-FT8"


def test_choose_dial_prefers_standard_in_audio_range():
    # 50.2615 MHz is 1500 Hz above the 6m MSK144 calling frequency.
    assert choose_dial(50_261_500, "MSK144") == 50_260_000
    # Explicit section dial wins.
    assert choose_dial(14_075_200, "FT8", explicit_dial_hz=14_074_000) == 14_074_000


# ── Reporter behaviour ──────────────────────────────────────────────────


def test_reporter_emits_one_instance_per_mode_and_sends_close(tmp_path):
    from ws_skim.reporting import Reporter

    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.bind(("127.0.0.1", 0))
    recv.settimeout(2.0)
    port = recv.getsockname()[1]

    rpt = Reporter()
    rpt.apply_settings("WA1HCO", "FN42", True, "127.0.0.1", port, False)
    rpt.set_context(source="radio", center_hz=144_160_000, bandwidth_hz=48_000)
    rpt.start()
    try:
        # Two modes in the 2m 48 kHz passband around 144.160.
        rpt.report_decode({
            "message": "CQ K1ABC FN42",
            "mode": "FT8",
            "jt9_snr": -8,
            "t_sec": 0.2,
            "utc_time": "18:00:00",
            "audio_hz": 1200,
            "dial_hz": 144_174_000,
            "radio_khz": 144175.2,
        })
        rpt.report_decode({
            "message": "W1AW WA1HCO FN42",
            "mode": "MSK144",
            "jt9_snr": 4,
            "t_sec": 1.5,
            "utc_time": "18:00:00",
            "radio_khz": 144151.5,
        })
        deadline = time.time() + 2.0
        got = []
        while time.time() < deadline:
            try:
                data, _ = recv.recvfrom(4096)
            except socket.timeout:
                break
            msg = parse(data)
            if msg:
                got.append(msg)
            decodes = [m for m in got if m["type"] == MSG_DECODE]
            if {m["mode"] for m in decodes} >= {"~", "&"}:
                break
        ids = {m["id"] for m in got if m}
        assert "SKIM-FLEX-2m-FT8" in ids
        assert "SKIM-FLEX-2m-MSK144" in ids or any(
            (m.get("id") or "").startswith("SKIM-FLEX-2m-MSK144") for m in got
        )
        decodes = [m for m in got if m and m["type"] == MSG_DECODE]
        modes = {m["mode"] for m in decodes}
        assert "~" in modes and "&" in modes
        # RF = dial + delta
        ft8 = next(m for m in decodes if m["mode"] == "~")
        assert 144_174_000 + ft8["delta_frequency"] == 144_175_200
    finally:
        rpt.stop()
        # Close / Clear after stop.
        recv.settimeout(0.5)
        tail = []
        try:
            while True:
                data, _ = recv.recvfrom(4096)
                tail.append(parse(data))
        except socket.timeout:
            pass
        recv.close()
    types = {m["type"] for m in tail if m}
    assert MSG_CLOSE in types
    assert MSG_CLEAR in types


def test_is_multicast_ip():
    assert is_multicast_ip("224.0.0.73")
    assert is_multicast_ip("239.255.0.0")
    assert not is_multicast_ip("127.0.0.1")
    assert not is_multicast_ip("192.168.1.10")


# ── Optional round-trip through WS Hub's parser ─────────────────────────


def _ws_hub_src() -> Path | None:
    env = os.environ.get("WS_HUB_SRC", "").strip()
    candidates = []
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    candidates.extend([
        here.parents[2] / "WS-Hub" / "src",
        here.parents[3] / "WS-Hub" / "src",
        Path("/tmp/ws-work/WS-Hub/src"),
    ])
    for c in candidates:
        if (c / "wims" / "udp" / "messages.py").is_file():
            return c
    return None


@pytest.mark.skipif(_ws_hub_src() is None, reason="WS-Hub source not available")
def test_roundtrip_through_ws_hub_parser():
    src = _ws_hub_src()
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from wims.udp.messages import parse as hub_parse, HEARTBEAT, STATUS, DECODE, CLOSE

    mid = "SKIM-FLEX-2m-FT8"
    hb = hub_parse(build_heartbeat(id=mid))
    assert hb.type == HEARTBEAT and hb.id == mid
    st = hub_parse(build_status(144_174_000, "WA1HCO", "FN42", mode="FT8", id=mid))
    assert st.type == STATUS
    assert st.dial_frequency == 144_174_000
    assert st.mode == "FT8"
    assert st.tx_enabled is False
    assert st.de_call == "WA1HCO"
    dec = hub_parse(build_decode({
        "message": "CQ K1ABC FN42",
        "mode": "FT8",
        "jt9_snr": -9,
        "t_sec": 0.1,
        "utc_time": "01:02:03",
        "audio_hz": 1350,
        "dial_hz": 144_174_000,
        "radio_khz": 144175.35,
    }, id=mid))
    assert dec.type == DECODE
    assert dec.id == mid
    assert dec.mode == "~"
    assert dec.message == "CQ K1ABC FN42"
    assert dec.delta_frequency == 1350
    assert dec.snr == -9
    assert dec.low_confidence is False
    # This is how WS Hub computes RF (server/state.py).
    assert st.dial_frequency + dec.delta_frequency == 144_175_350
    cl = hub_parse(build_close(id=mid))
    assert cl.type == CLOSE and cl.id == mid
