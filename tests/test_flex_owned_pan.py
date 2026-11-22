"""WS Skim's own Flex panadapter (flexclient/owned_pan.py), against a fake radio.

The rules that matter most: only ids from our own create commands are ever
changed or removed, nothing is created on an empty snapshot, tx is never
set, the rate follows MSK144, and the centre is steered back when moved.
"""
from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest

from flexclient.owned_pan import (
    OwnedPan, OwnedPanError, RadioState, choose_center_mhz, pick_dax_channel,
    rate_from_class_id,
)
from ws_skim import flex_pan, rates

OP_PAN = 0x40000000      # the operator's pan
OP_STREAM = 0x20000000   # the operator's DAX IQ stream (channel 1)
OP_SLICE = 1


class FakeRadio:
    """Just enough SmartSDR v4 behaviour (as measured on an 8600)."""

    def __init__(self, with_operator=True):
        self.state = RadioState()
        self.cmds = []
        self.pans = {}
        self.slices = {}
        self.streams = {}
        self.avail = 12
        self.next_pan = 0x40000001
        self.next_stream = 0x20000001
        self.timeout_on = set()
        if with_operator:
            self.pans[OP_PAN] = {"center": 7.074, "daxiq_channel": 1, "rxant": "RX_A"}
            self.slices[OP_SLICE] = {"pan": OP_PAN, "freq": 7.074, "tx": "1", "audio_mute": "0"}
            self.streams[OP_STREAM] = {"ch": 1, "pan": OP_PAN, "rate": 48000}
        self.dump()

    def emit(self, payload):
        self.state.feed(f"S1234|{payload}")

    def dump(self):
        for pid, p in self.pans.items():
            self.emit(f"display pan 0x{pid:08X} center={p['center']:.6f} bandwidth=0.192 "
                      f"rxant={p['rxant']} daxiq_channel={p['daxiq_channel']}")
        for n, sl in self.slices.items():
            self.emit(f"slice {n} in_use=1 RF_frequency={sl['freq']:.6f} pan=0x{sl['pan']:08X} "
                      f"tx={sl['tx']} audio_mute={sl['audio_mute']}")
        for sid, st in self.streams.items():
            self.emit(f"stream 0x{sid:08X} type=dax_iq daxiq_channel={st['ch']} "
                      f"pan=0x{st['pan']:08X} daxiq_rate={st['rate']}")
        self.emit(f"radio daxiq_capacity=16 daxiq_available={self.avail}")

    def send(self, cmd, timeout=5.0):
        self.cmds.append(cmd)
        if any(cmd.startswith(t) for t in self.timeout_on):
            raise RuntimeError(f"Command timeout: {cmd}")
        t = cmd.split()
        if cmd.startswith("sub "):
            self.dump()
            return ""
        if cmd.startswith("display pan c "):
            pid = self.next_pan; self.next_pan += 1
            freq = float(re.search(r"freq=([\d.]+)", cmd).group(1))
            self.pans[pid] = {"center": 7.030, "daxiq_channel": 0, "rxant": "ANT1"}
            n = max(list(self.slices) + [-1]) + 1
            self.slices[n] = {"pan": pid, "freq": freq, "tx": "0", "audio_mute": "0"}
            self.dump()
            return f"0x{pid:08X},0x{pid + 0x2000000:08X}"
        if cmd.startswith("display pan set "):
            pid = int(t[3], 16)
            if pid not in self.pans:
                raise RuntimeError("Radio rejected command: no such pan")
            kv = dict(x.split("=", 1) for x in t[4:])
            if "center" in kv:
                if any(s["pan"] == pid for s in self.slices.values()):
                    self.pans[pid]["center"] = float(kv["center"])
                return f"{float(kv['center']):g}"     # no status line (as measured)
            if "rxant" in kv:
                self.pans[pid]["rxant"] = kv["rxant"]
            return ""
        if cmd.startswith("display pan r "):
            pid = int(t[3], 16)
            self.pans.pop(pid)
            for n in [n for n, s in self.slices.items() if s["pan"] == pid]:
                self.slices.pop(n)
                self.emit(f"slice {n} in_use=0")
            self.emit(f"display pan 0x{pid:08X} removed")
            return ""
        if cmd.startswith("slice tune "):
            self.slices[int(t[2])]["freq"] = float(t[3]); return ""
        if cmd.startswith("slice set "):
            n = int(t[2])
            kv = dict(x.split("=", 1) for x in t[3:])
            self.slices[n].update(kv); return ""
        if cmd.startswith("dax iq set "):
            self.pans[int(t[4].split("=")[1], 16)]["daxiq_channel"] = int(t[3]); return "ok"
        if cmd.startswith("stream create "):
            sid = self.next_stream; self.next_stream += 1
            ch = int(re.search(r"daxiq=(\d+)", cmd).group(1))
            pan = next(p for p, v in self.pans.items() if v["daxiq_channel"] == ch)
            self.streams[sid] = {"ch": ch, "pan": pan, "rate": 48000}
            self.avail -= 2
            self.dump()
            return f"{sid:08X}"
        if cmd.startswith("stream set "):
            sid = int(t[2], 16); r = int(t[3].split("=")[1])
            self.avail -= (r - self.streams[sid]["rate"]) // 24000
            self.streams[sid]["rate"] = r; self.dump(); return "ok"
        if cmd.startswith("stream remove "):
            sid = int(t[2], 16)
            st = self.streams.pop(sid)
            self.avail += st["rate"] // 24000
            self.emit(f"stream 0x{sid:08X} removed"); return ""
        raise RuntimeError(f"Radio rejected command: {cmd}")


def _owned(radio, tmp_path=None, **kw):
    return OwnedPan(radio.send, radio.state, client_ip="192.168.1.181", udp_port=54400,
                    store_path=str(tmp_path / "owned.json") if tmp_path else None,
                    radio_key="192.168.1.83", sleep=lambda s: None, **kw)


def _touches_operator(cmds):
    bad = []
    for c in cmds:
        if c.startswith("sub "):
            continue
        if (f"0x{OP_PAN:08X}" in c.upper().replace("0X", "0x") or f"{OP_STREAM:08X}" in c.upper()
                or re.match(rf"slice \w+ {OP_SLICE}\b", c) or " tx=" in c or c.endswith(" tx")
                or "daxiq=1 " in c or c.startswith("dax iq set 1 ")):
            bad.append(c)
    return bad


# ── safety ───────────────────────────────────────────────────────────────────

def test_empty_snapshot_aborts_without_creating_anything():
    radio = FakeRadio(with_operator=False)
    o = _owned(radio)
    with pytest.raises(OwnedPanError):
        o.create(7.0624)
    assert not any(c.startswith("display pan c") for c in radio.cmds)
    assert radio.pans == {}


def test_create_stream_teardown_touch_only_our_ids(tmp_path):
    radio = FakeRadio()
    o = _owned(radio, tmp_path)
    pan = o.create(7.0624, rxant="RX_A")
    sid = o.start_stream(96000)
    assert pan == 0x40000001 and o.slice_id == 2
    assert radio.slices[2]["audio_mute"] == "1"                # muted
    assert radio.pans[pan]["center"] == pytest.approx(7.0624)  # re-set after create
    assert radio.slices[2]["freq"] == pytest.approx(7.0624)    # parked at centre
    assert o.dax_channel == 2                                  # 1 is the operator's
    assert radio.streams[sid]["rate"] == 96000 and o.rate == 96000
    rec = json.loads((tmp_path / "owned.json").read_text())
    assert rec["pan"] == "0x40000001" and rec["dax_channel"] == 2
    o.teardown()
    assert set(radio.pans) == {OP_PAN}
    assert set(radio.slices) == {OP_SLICE}
    assert set(radio.streams) == {OP_STREAM}
    assert _touches_operator(radio.cmds) == []
    assert not (tmp_path / "owned.json").exists()


def test_never_sets_tx_even_when_radio_makes_our_slice_tx():
    radio = FakeRadio()
    radio.slices.pop(OP_SLICE)          # operator has no slice
    o = _owned(radio)
    o.create(7.0624)
    radio.slices[o.slice_id]["tx"] = "1"
    o.start_stream(96000); o.check(force=True); o.teardown()
    assert not any(re.search(r"\btx(=|\b)", c) for c in radio.cmds)


def test_dax_channel_skips_every_channel_in_use():
    assert pick_dax_channel({1}) == 2
    assert pick_dax_channel({1, 2}) == 3
    assert pick_dax_channel({2, 3, 4}) == 1
    assert pick_dax_channel({1, 2, 3, 4}) is None


def test_create_timeout_does_not_remove_anything():
    radio = FakeRadio()
    radio.timeout_on = {"display pan c"}
    o = _owned(radio)
    with pytest.raises(RuntimeError):
        o.create(7.0624)
    o.teardown()
    assert not any(c.startswith(("display pan r", "stream remove", "slice r")) for c in radio.cmds)


def test_teardown_timeouts_never_raise_and_keep_the_record(tmp_path):
    radio = FakeRadio()
    o = _owned(radio, tmp_path)
    o.create(7.0624); o.start_stream(96000)
    radio.timeout_on = {"stream remove", "display pan r"}
    o.teardown()                                     # must not raise
    assert (tmp_path / "owned.json").exists()        # removed at the next start


def test_leftover_pan_removed_only_when_it_still_matches(tmp_path):
    radio = FakeRadio()
    o = _owned(radio, tmp_path)
    o.create(7.0624); o.start_stream(96000)
    # Crash: our TCP connection drops; the stream goes, pan + slice stay.
    radio.streams.pop(o.stream_id)
    radio.state = RadioState(); radio.dump()     # new session: fresh status
    o2 = _owned(radio, tmp_path)
    assert o2.remove_leftover() is True
    assert set(radio.pans) == {OP_PAN} and set(radio.slices) == {OP_SLICE}
    assert not (tmp_path / "owned.json").exists()


def test_leftover_record_that_no_longer_matches_is_forgotten(tmp_path):
    radio = FakeRadio()
    # A record pointing at the operator's pan with another DAX channel.
    (tmp_path / "owned.json").write_text(json.dumps(
        {"radio": "192.168.1.83", "pan": f"0x{OP_PAN:08X}", "slice": OP_SLICE,
         "stream": None, "dax_channel": 3}))
    o = _owned(radio, tmp_path)
    assert o.remove_leftover() is False
    assert not any(c.startswith("display pan r") for c in radio.cmds)
    assert OP_PAN in radio.pans
    assert not (tmp_path / "owned.json").exists()


# ── steering ─────────────────────────────────────────────────────────────────

def test_center_is_steered_back_when_someone_moves_it():
    clock = [0.0]
    radio = FakeRadio()
    o = _owned(radio, clock=lambda: clock[0])
    pan = o.create(7.0624)
    radio.pans[pan]["center"] = 7.100           # dragged in SmartSDR
    clock[0] += 11
    o.check()
    assert o.resteer_count == 1
    assert radio.pans[pan]["center"] == pytest.approx(7.0624)
    clock[0] += 11
    o.check()
    assert o.resteer_count == 1                 # where it belongs: nothing more


def test_band_change_steers_our_pan_only():
    radio = FakeRadio()
    o = _owned(radio)
    pan = o.create(7.0624)
    o.steer(14.071)
    assert radio.pans[pan]["center"] == pytest.approx(14.071)
    assert radio.pans[OP_PAN]["center"] == pytest.approx(7.074)


# ── rate ─────────────────────────────────────────────────────────────────────

def _eng(mode="FT8", pm=("FT8",), own=True, calling=7.074):
    return SimpleNamespace(mode=mode, period_modes=pm, source_mode="radio",
                           _flex_own_pan=own, calling_freq_mhz=calling)


def test_rate_rule_flex_own_pan_96k_unless_msk144():
    assert rates.wanted_rate(_eng()) == 96000
    assert rates.wanted_rate(_eng(pm=("FT8", "FT4"))) == 96000
    assert rates.wanted_rate(_eng("MSK144", (), calling=50.260)) == 48000
    assert rates.wanted_rate(_eng(pm=("FT8", "MSK144"))) == 48000
    assert rates.wanted_rate(_eng(own=False)) == 48000      # following SmartSDR's pan


def test_rate_change_and_capacity_fallback():
    radio = FakeRadio()
    o = _owned(radio)
    o.create(7.0624); sid = o.start_stream(96000)
    assert o.set_rate(48000) == 48000 and radio.streams[sid]["rate"] == 48000
    radio.avail = 0; radio.dump()               # someone took the rest
    assert o.set_rate(96000) == 48000           # only our own 2 units left


def test_packet_class_code_gives_rate():
    assert rate_from_class_id(0x1C2D534C02E4) == 48000
    assert rate_from_class_id(0x1C2D534C02E5) == 96000
    assert rate_from_class_id(None) is None


# ── where the centre goes ────────────────────────────────────────────────────

def _subbands_clear(c, dials, min_khz):
    return all(min(abs(c - d), abs(c - d - 0.0032)) * 1000 >= min_khz
               and not (d <= c <= d + 0.0032) for d in dials)


def test_center_40m_holds_ft8_and_ft4_with_dc_between():
    c = choose_center_mhz([7.074, 7.0475], 96000, 7.074)
    assert 7.0507 < c < 7.074 and _subbands_clear(c, [7.074, 7.0475], 5.0)
    for d in (7.074, 7.0475):
        assert abs(d - c) * 1000 + 3.2 <= 0.45 * 96


def test_center_20m_puts_dc_outside_close_subbands():
    c = choose_center_mhz([14.074, 14.080], 96000, 14.074)
    assert _subbands_clear(c, [14.074, 14.080], 2.0)


def test_center_drops_far_dial_when_it_cannot_fit():
    # 6 m FT8 50.313 and FT4 50.318 fit; a dial 60 kHz away does not at 48k.
    c = choose_center_mhz([50.313, 50.373], 48000, 50.313)
    assert abs(c - 50.313) * 1000 < 0.45 * 48


def test_flex_pan_center_follows_modes():
    e = _eng(pm=("FT8", "FT4"))
    c = flex_pan.wanted_center_mhz(e, 96000)
    assert 7.0507 < c < 7.074
    e1 = _eng(pm=("FT8",))
    c1 = flex_pan.wanted_center_mhz(e1, 96000)
    assert _subbands_clear(c1, [7.074], 2.0)
    assert flex_pan.wanted_center_mhz(_eng("MSK144", (), calling=50.260), 48000) == 50.260


def test_reconcile_pushes_rate_and_center_to_the_client():
    class RC:
        own_pan = True
        wanted_rate = 48000
        wanted_center_mhz = None
        def set_target_rate(self, r): self.wanted_rate = r
        def retune(self, c): self.wanted_center_mhz = c
    e = _eng(pm=("FT8", "FT4")); e.radio_client = RC()
    flex_pan.reconcile(e)
    assert e.radio_client.wanted_rate == 96000
    assert 7.0507 < e.radio_client.wanted_center_mhz < 7.074


# ── the TCP reader survives a bad line ───────────────────────────────────────

def test_tcp_reader_survives_a_failing_status_callback():
    from flexclient.models import FlexRadio
    from flexclient.tcp_client import FlexTCPClient

    class Sock:
        def __init__(self):
            self.chunks = [b"S1|bad line\n", b"R1|0|ok\n"]
        def recv(self, n):
            return self.chunks.pop(0) if self.chunks else b""
        def sendall(self, b):
            pass
    c = FlexTCPClient(FlexRadio(ip="x", port=4992))
    c._sock = Sock()
    def boom(line):
        raise ValueError("bad")
    c._status_cb = boom
    ev = threading.Event()
    c._responses[1] = (ev, None)
    c._running = True
    c._recv_loop()
    assert ev.is_set() and c._responses[1][1] == (0, "ok")
