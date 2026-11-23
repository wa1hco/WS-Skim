"""WSJT-X for WS Skim: Set up WSJT-X, the UDP link and click-to-work.

Fake radio / fake WSJT-X only.  What matters most: the operator's own
WSJT-X settings are never changed, nothing is written while that WSJT-X
runs, WS Skim only ever tunes its own slice and never sets transmit, and a
Reply echoes one of WSJT-X's own decodes exactly.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ws_skim import click_to_work, skim_wsjtx as sw, wsjtx_udp as wu
from ws_skim.skim_link import SkimLink, decode_sender, reply_rank

from test_flex_owned_pan import FakeRadio, OP_SLICE, _owned, _touches_operator

# Jeff's desktop (Nov 23, 2026): nCAT and nDAX for slices A and B, plus
# nmap's ncat, which must not be mistaken for nCAT.
CMDLINES = [
    (10, ["/home/op/ham/nCAT/nCAT", "-station", "FLEX-8600M", "-slice", "A", "-listen", ":4532"]),
    (11, ["/home/op/ham/nCAT/nCAT", "-station", "FLEX-8600M", "-slice", "B", "-listen", ":4533"]),
    (12, ["/home/op/ham/nDAX/nDAX", "-station", "FLEX-8600M", "-slice", "A", "-daxch", "1",
          "-source", "flex.sliceA.rx", "-sink", "flex.tx"]),
    (13, ["/home/op/ham/nDAX/nDAX", "-station", "FLEX-8600M", "-slice", "B", "-daxch", "2",
          "-source", "flex.sliceB.rx", "-tx=false"]),
    (14, ["/usr/bin/ncat", "-l", "9999"]),
    (15, ["/usr/local/bin/wsjtx", "--rig-name", "flex"]),
]

FLEX_INI = r"""[General]
RxFreq_old=1500
[Common]
DialFreq=7062350
Mode=FT8
[Configuration]
AcceptUDPRequests=true
AzElDir=/home/op/.local/share/WSJT-X - flex/save
CATNetworkPort=localhost:4532
DecodeHighlighting=@Variant(\0\0\0\x7f\0\0\0(DecodeHighlightingModel::HighlightItems\0\0\0\0\x10)
MyCall=WA1HCO
MyGrid=FN42EV
PTTMethod=@Variant(\0\0\0\x7f\0\0\0\x1eTransceiverFactory::PTTMethod\0\0\0\0\xfPTT_method_CAT\0)
Rig=Hamlib NET rigctl
SoundInName=flex.sliceA.rx
SoundOutName=flex.tx
UDPInterface=enp13s0f1, br0
UDPServer=224.0.0.73
UDPServerPort=2237
UDPTTL=3
"""

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="nCAT/nDAX setup is Linux only")


# ── finding the helpers ─────────────────────────────────────────────────────

def test_find_helpers_for_slice_b():
    h = sw.find_helpers("B", CMDLINES)
    assert h.cat_port == 4533
    assert h.rx_source == "flex.sliceB.rx" and h.dax_channel == 2
    assert h.tx_sink == "flex.tx"            # B has no TX sink; A's shared one


def test_nmap_ncat_is_not_ncat():
    h = sw.find_helpers("A", [(14, ["/usr/bin/ncat", "-l", "9999"])])
    assert h.cat_port is None


def test_wsjtx_running_by_rig_name():
    assert sw.wsjtx_running("flex", CMDLINES)
    assert not sw.wsjtx_running("Skim", CMDLINES)
    assert sw.wsjtx_running("Skim", [(1, ["/usr/local/bin/wsjtx", "--rig-name=Skim"])])


@linux_only
def test_plan_needs_a_slice_and_helpers():
    with pytest.raises(sw.SetupError, match="no slice"):
        sw.plan_setup(None, 24_915_000, cmdlines=CMDLINES)
    with pytest.raises(sw.SetupError, match="nCAT for slice C"):
        sw.plan_setup("C", 24_915_000, cmdlines=CMDLINES)


# ── the settings file ───────────────────────────────────────────────────────

def test_udp_follows_a_multicast_template_else_default():
    assert sw.udp_from_template(FLEX_INI) == ("224.0.0.73", 2237, "enp13s0f1, br0", "3")
    uni = FLEX_INI.replace("UDPServer=224.0.0.73", "UDPServer=127.0.0.1")
    assert sw.udp_from_template(uni) == ("224.0.0.73", 2237, "lo", "1")
    assert sw.udp_from_template("") == ("224.0.0.73", 2237, "lo", "1")


def test_ini_set_replaces_keeps_and_adds():
    out = sw.ini_set("[A]\nx=1\ny=2\n[B]\nz=3\n", "A", {"y": "9", "n": "new"})
    assert out == "[A]\nx=1\ny=9\nn=new\n[B]\nz=3\n"
    out = sw.ini_set("[A]\nx=1\n", "C", {"k": "v"})
    assert out.endswith("[C]\nk=v\n")


@linux_only
def test_build_ini_points_at_skim_slice_and_keeps_the_rest(tmp_path):
    plan = sw.plan_setup("B", 24_915_000, cmdlines=CMDLINES, template=None)
    plan.udp_group, plan.udp_port, plan.udp_interface, plan.udp_ttl = sw.udp_from_template(FLEX_INI)
    text = sw.build_ini(FLEX_INI, plan, tmp_path / "data")
    g = lambda k, sec="Configuration": sw.ini_get(text, sec, k)
    assert g("CATNetworkPort") == "localhost:4533"
    assert g("SoundInName") == "flex.sliceB.rx" and g("SoundOutName") == "flex.tx"
    assert g("PTTMethod") == sw.PTT_CAT and g("DataMode") == sw.DATA_MODE_DATA
    assert g("AcceptUDPRequests") == "true" and g("UDPServer") == "224.0.0.73"
    assert g("AzElDir") == str(tmp_path / "data")
    assert g("MyCall") == "WA1HCO"                                  # kept
    assert g("DecodeHighlighting").startswith(r"@Variant(\0\0\0\x7f")  # kept byte for byte
    assert g("DialFreq", "Common") == "24915000" and g("Mode", "Common") == "FT8"


def _home(tmp_path):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / "Desktop").mkdir()
    flex = home / ".config" / "WSJT-X - flex.ini"
    flex.write_text(FLEX_INI)
    data = home / ".local" / "share" / "WSJT-X - flex"
    data.mkdir(parents=True)
    os.symlink("/media/share/wrig/wsjtx_log.adi", data / "wsjtx_log.adi")
    return home, flex


@linux_only
def test_set_up_writes_its_own_file_launcher_record_and_shares_the_log(tmp_path):
    home, flex = _home(tmp_path)
    before = flex.read_bytes()
    plan = sw.plan_setup("B", 24_915_000, cmdlines=CMDLINES, template=flex)
    rec = tmp_path / "rec.json"
    done = sw.set_up(plan, home=home, cmdlines=CMDLINES, program="/usr/local/bin/wsjtx", record=rec)
    ini = home / ".config" / "WSJT-X - Skim.ini"
    assert done["ini"] == str(ini) and ini.is_file()
    assert flex.read_bytes() == before                     # the operator's copy untouched
    assert "CATNetworkPort=localhost:4533" in ini.read_text()
    menu = home / ".local" / "share" / "applications" / sw.LAUNCHER_FILE
    desk = home / "Desktop" / sw.LAUNCHER_FILE
    for p in (menu, desk):
        t = p.read_text()
        assert "Name=WSJT-X for WS Skim" in t
        assert 'Exec="/usr/local/bin/wsjtx" --rig-name Skim' in t
        assert os.access(p, os.X_OK)
    link = home / ".local" / "share" / "WSJT-X - Skim" / "wsjtx_log.adi"
    assert os.readlink(link) == "/media/share/wrig/wsjtx_log.adi"
    r = json.loads(rec.read_text())
    assert r["letter"] == "B" and r["dax_channel"] == 2 and r["udp_port"] == 2237
    assert "Skim" in sw.summary(plan, done)
    # Running again keeps the Skim copy's own later edits.
    ini.write_text(ini.read_text().replace("MyGrid=FN42EV", "MyGrid=FN42EW"))
    sw.set_up(plan, home=home, cmdlines=CMDLINES, program="/usr/local/bin/wsjtx", record=rec)
    assert "MyGrid=FN42EW" in ini.read_text()


@linux_only
def test_set_up_refuses_while_that_wsjtx_runs(tmp_path):
    home, flex = _home(tmp_path)
    plan = sw.plan_setup("B", 24_915_000, cmdlines=CMDLINES, template=flex)
    running = CMDLINES + [(20, ["/usr/local/bin/wsjtx", "--rig-name", "Skim"])]
    with pytest.raises(sw.SetupError, match="Close it first"):
        sw.set_up(plan, home=home, cmdlines=running, program="/usr/local/bin/wsjtx",
                  record=tmp_path / "rec.json")
    assert not (home / ".config" / "WSJT-X - Skim.ini").exists()


def test_user_text_has_no_command_lines():
    plan = sw.SetupPlan("B", 4533, 2, "flex.sliceB.rx", "flex.tx", 24_915_000, "FT8",
                        "224.0.0.73", 2237, "lo", "1")
    text = sw.summary(plan, {"launchers": ["/x/Desktop/a.desktop"], "log": None})
    assert "--" not in text and "python" not in text.lower()


# ── UDP messages ────────────────────────────────────────────────────────────

def _decode(msg="CQ K1ABC FN42", t=43215000, snr=-12, dt=0.3, df=1234, mode="~", mid=sw.APP_ID):
    return (wu.header(wu.MSG_DECODE, mid) + wu.pack_bool(True) + wu.pack_u32(t) + wu.pack_i32(snr)
            + wu.pack_f64(dt) + wu.pack_u32(df) + wu.pack_utf8(mode) + wu.pack_utf8(msg)
            + wu.pack_bool(False) + wu.pack_bool(False))


def _status(mode="FT8", tx=False, txen=False, dx="", mid=sw.APP_ID):
    return (wu.header(wu.MSG_STATUS, mid) + wu.pack_u64(24_915_000) + wu.pack_utf8(mode)
            + wu.pack_utf8(dx) + wu.pack_utf8("") + wu.pack_utf8(mode) + wu.pack_bool(txen)
            + wu.pack_bool(tx) + wu.pack_bool(False) + wu.pack_u32(1500) + wu.pack_u32(1500)
            + wu.pack_utf8("WA1HCO") + wu.pack_utf8("FN42") + wu.pack_utf8("") + wu.pack_bool(False)
            + wu.pack_utf8("") + wu.pack_bool(False) + struct_u8(0) + wu.pack_u32_opt(None)
            + wu.pack_u32_opt(None) + wu.pack_utf8("Skim") + wu.pack_utf8(""))


def struct_u8(v):
    import struct
    return struct.pack(">B", v)


def test_reply_echoes_the_decode_exactly():
    d = wu.parse(_decode(dt=0.30000001192092896))
    r = wu.parse(wu.build_reply(d, sw.APP_ID))
    assert r["type"] == wu.MSG_REPLY and r["id"] == sw.APP_ID
    for k in ("time_ms", "snr", "delta_time", "delta_frequency", "mode", "message", "low_confidence"):
        assert r[k] == d[k]
    assert r["modifiers"] == 0


def test_configure_changes_only_what_is_given():
    c = wu.parse(wu.build_configure(sw.APP_ID, mode="FT4", rx_df=1200))
    assert c["type"] == wu.MSG_CONFIGURE and c["mode"] == "FT4" and c["rx_df"] == 1200
    assert c["frequency_tolerance"] is None and c["tr_period"] is None
    assert c["dx_call"] is None and c["dx_grid"] is None and c["generate_messages"] is False


def test_sender_and_rank():
    assert decode_sender("CQ K1ABC FN42") == "K1ABC"
    assert decode_sender("W9XYZ K1ABC R-10") == "K1ABC"
    assert reply_rank("CQ K1ABC FN42") > reply_rank("W9XYZ K1ABC RR73") > reply_rank("W9XYZ K1ABC -10")


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _link():
    clk, sock = Clock(), FakeSock()
    return SkimLink(clock=clk, sock=sock), clk, sock


WSJTX = ("192.168.1.181", 50999)


def test_link_ignores_other_wsjtx_copies():
    lk, clk, sock = _link()
    lk.feed(_status(mid="WSJT-X - flex"), ("192.168.1.181", 50164))
    lk.feed(_decode(mid="WSJT-X - flex"), ("192.168.1.181", 50164))
    assert not lk.alive and not lk.decodes
    assert "not running" in lk.work("K1ABC", "FT8")
    assert sock.sent == []


def test_work_waits_for_wsjtx_decode_then_replies_to_its_port():
    lk, clk, sock = _link()
    lk.feed(_status(), WSJTX)
    msg = lk.work("K1ABC", "FT8", 1234)
    assert "Waiting" in msg
    configure = [wu.parse(d) for d, _a in sock.sent]
    assert all(m["type"] == wu.MSG_CONFIGURE and not m["mode"] for m in configure)
    sock.sent.clear()
    clk.t += 10
    lk.feed(_decode("W9XYZ N0CALL -10"), WSJTX)
    assert sock.sent == []
    raw = _decode("CQ K1ABC FN42", snr=-7, df=1240)
    lk.feed(raw, WSJTX)
    assert len(sock.sent) == 1
    data, addr = sock.sent[0]
    assert addr == WSJTX
    r, d = wu.parse(data), wu.parse(raw)
    assert r["type"] == wu.MSG_REPLY and r["message"] == "CQ K1ABC FN42"
    assert (r["time_ms"], r["snr"], r["delta_frequency"]) == (d["time_ms"], d["snr"], d["delta_frequency"])
    assert lk.pending is None and "calling K1ABC" in lk.last_event


def test_work_answers_a_fresh_decode_at_once_preferring_cq():
    lk, clk, sock = _link()
    lk.feed(_status(), WSJTX)
    lk.feed(_decode("W9XYZ K1ABC -10", t=1000), WSJTX)
    lk.feed(_decode("CQ K1ABC FN42", t=16000), WSJTX)
    lk.feed(_decode("W9XYZ K1ABC RR73", t=31000), WSJTX)
    lk.work("K1ABC", "FT8")
    replies = [wu.parse(d) for d, _a in sock.sent if wu.parse(d)["type"] == wu.MSG_REPLY]
    assert [r["message"] for r in replies] == ["CQ K1ABC FN42"]


def test_work_switches_mode_and_gives_up_after_a_while():
    lk, clk, sock = _link()
    lk.feed(_status(mode="FT8"), WSJTX)
    lk.work("K1ABC", "FT4", 900)
    first = wu.parse(sock.sent[0][0])
    assert first["type"] == wu.MSG_CONFIGURE and first["mode"] == "FT4" and first["rx_df"] == 900
    clk.t += 30
    lk.feed(_status(mode="FT4"), WSJTX)       # keep it alive
    clk.t += 200
    lk.tick()
    assert lk.pending is None and "gave up" in lk.last_event


def test_stale_decodes_are_not_answered():
    lk, clk, sock = _link()
    lk.feed(_status(), WSJTX)
    lk.feed(_decode("CQ K1ABC FN42"), WSJTX)
    clk.t += 35
    lk.feed(_status(), WSJTX)
    lk.work("K1ABC", "FT8")
    assert not any(wu.parse(d)["type"] == wu.MSG_REPLY for d, _a in sock.sent)


def test_link_status_text():
    lk, clk, sock = _link()
    assert "not running" in lk.status_text()
    lk.feed(_status(tx=True, txen=True, dx="K1ABC"), WSJTX)
    assert "transmitting to K1ABC" in lk.status_text()


# ── WS Skim's slice (fake radio) ────────────────────────────────────────────

def test_hold_slice_tunes_only_our_slice_and_never_tx():
    radio = FakeRadio()
    o = _owned(radio)
    o.create(24.912)
    assert o.hold_slice(24.915)
    assert radio.slices[o.slice_id]["freq"] == pytest.approx(24.915)
    o.steer(24.912, force=True)                   # re-steer keeps the hold
    assert radio.slices[o.slice_id]["freq"] == pytest.approx(24.915)
    assert not o.hold_slice(24.990)               # outside the received band
    o.steer(7.0624)                               # band change lets go
    assert o.slice_hold_mhz is None
    assert radio.slices[o.slice_id]["freq"] == pytest.approx(7.0624)
    assert o.set_dax_audio(2) and radio.slices[o.slice_id]["dax"] == "2"
    o.teardown()
    assert _touches_operator(radio.cmds) == []
    assert not any(re.search(r"\btx(=|\b)", c) for c in radio.cmds)


def test_slice_letter_and_tx_flag_come_from_the_radio():
    radio = FakeRadio()
    o = _owned(radio)
    o.create(24.912)
    assert o.slice_letter == chr(ord("A") + o.slice_id)
    radio.emit(f"slice {o.slice_id} index_letter=B tx=1")
    assert o.slice_letter == "B" and o.tx_on_our_slice
    radio.emit(f"slice {o.slice_id} tx=0")
    assert not o.tx_on_our_slice


def test_client_puts_dax_audio_only_on_the_set_up_letter():
    from flexclient.client import FlexDAXIQ
    radio = FakeRadio()
    o = _owned(radio)
    o.create(24.912)
    c = FlexDAXIQ(own_pan=True)
    c.owned = o
    c._dax_setup = SimpleNamespace(pan_frequency_mhz=None, pan_bandwidth_hz=None)
    t = threading.Thread(target=c._owned_loop, daemon=True)
    t.start()
    try:
        c.set_dax_audio(2, "B")                   # our slice is C here
        time.sleep(0.3)
        assert not any(cmd.endswith("dax=2") for cmd in radio.cmds)
        radio.emit(f"slice {o.slice_id} index_letter=B")
        c.set_dax_audio(2, "B")
        time.sleep(0.3)
        assert f"slice set {o.slice_id} dax=2" in radio.cmds
        c.hold_slice(24.915)
        time.sleep(0.3)
        assert radio.slices[o.slice_id]["freq"] == pytest.approx(24.915)
    finally:
        c._owned_stop.set(); c._owned_wake.set(); t.join(2)
    assert _touches_operator(radio.cmds) == []


# ── click-to-work glue ──────────────────────────────────────────────────────

def test_plan_dial_uses_the_standard_dial():
    assert click_to_work.plan_dial(24_916_234, "FT8") == (24_915_000, 1234)
    assert click_to_work.plan_dial(24_919_900, "FT4") == (24_919_000, 900)
    dial, df = click_to_work.plan_dial(24_960_500, "FT8")
    assert 100 <= df <= 3500 and dial % 1000 == 0


class FakeRC:
    own_pan = True

    def __init__(self, owned):
        self.owned = owned
        self.held = []
        self.own_slice_letter = "B"
        self.tx_on_own_slice = False

    def hold_slice(self, mhz):
        self.held.append(mhz)


def _app(tmp_path, monkeypatch, record=True, alive=True):
    rec = {"letter": "B", "dax_channel": 2, "udp_group": "224.0.0.73", "udp_port": 2237}
    monkeypatch.setattr(click_to_work, "load_record", lambda: rec if record else None)
    owned = SimpleNamespace(target_center_mhz=24.912, HOLD_MAX_OFFSET_KHZ=40.0)
    lk, clk, sock = _link()
    if alive:
        lk.feed(_status(), WSJTX)
    app = SimpleNamespace(radio_client=FakeRC(owned), _skim_link=lk)
    return app, lk, sock


def test_request_tunes_our_slice_and_asks_wsjtx(tmp_path, monkeypatch):
    app, lk, sock = _app(tmp_path, monkeypatch)
    res = click_to_work.request(app, {"call": "k1abc", "mode": "FT8", "freq_hz": 24_916_234})
    assert res["ok"] and app.radio_client.held == [pytest.approx(24.915)]
    assert lk.pending["call"] == "K1ABC"


def test_request_explains_what_is_missing(tmp_path, monkeypatch):
    app, lk, sock = _app(tmp_path, monkeypatch, record=False)
    assert "Set up WSJT-X" in click_to_work.request(app, {"call": "K1ABC", "mode": "FT8", "freq_hz": 24_916_000})["message"]
    app, lk, sock = _app(tmp_path, monkeypatch, alive=False)
    res = click_to_work.request(app, {"call": "K1ABC", "mode": "FT8", "freq_hz": 24_916_000})
    assert not res["ok"] and "not running" in res["message"] and app.radio_client.held == []
    app, lk, sock = _app(tmp_path, monkeypatch)
    app.radio_client.own_slice_letter = "C"
    assert "Press Set up WSJT-X again" in click_to_work.request(app, {"call": "K1ABC", "mode": "FT8", "freq_hz": 24_916_000})["message"]
    app, lk, sock = _app(tmp_path, monkeypatch)
    assert "MSK144" in click_to_work.request(app, {"call": "K1ABC", "mode": "MSK144", "freq_hz": 50_260_000})["message"]
    app.radio_client.own_pan = False
    assert "own panadapter" in click_to_work.request(app, {"call": "K1ABC", "mode": "FT8", "freq_hz": 24_916_000})["message"]


def test_state_warns_when_transmit_is_left_on_skim_slice(tmp_path, monkeypatch):
    app, lk, sock = _app(tmp_path, monkeypatch)
    app.radio_client.tx_on_own_slice = True
    st = click_to_work.state(app)
    assert st["set_up"] and st["tx_on_skim_slice"] and "Click TX on your own slice" in st["warning"]
    lk.feed(_status(tx=True, txen=True, dx="K1ABC"), WSJTX)
    assert click_to_work.state(app)["warning"] == ""


def test_roster_server_work_endpoint(tmp_path):
    import urllib.request
    from ws_skim.roster.model import RosterSession
    from ws_skim.roster.server import RosterServer
    got = []
    srv = RosterServer(RosterSession(log_path=str(tmp_path / "none.adi")), port=0,
                       work=lambda body: (got.append(body) or {"ok": True, "message": "calling"}),
                       work_state=lambda: {"set_up": True, "text": "WSJT-X for WS Skim: listening"})
    srv.start()
    try:
        req = urllib.request.Request(srv.url.rstrip("/") + "/api/work", method="POST",
                                     data=json.dumps({"call": "K1ABC"}).encode(),
                                     headers={"Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert res == {"ok": True, "message": "calling"} and got == [{"call": "K1ABC"}]
        state = json.loads(urllib.request.urlopen(srv.url.rstrip("/") + "/api/state", timeout=5).read())
        assert state["work"]["set_up"] is True
    finally:
        srv.stop()


# ── the Flex window button ──────────────────────────────────────────────────

@linux_only
def test_flex_window_has_set_up_button_that_explains_what_is_missing(monkeypatch, tmp_path):
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ws_skim import source_windows
    monkeypatch.setattr(click_to_work, "load_record", lambda: None)
    host = QtWidgets.QMainWindow()
    source_windows.setup_flex_window(host, QtWidgets.QAction("Flex", host))
    assert host._skim_wsjtx_btn.text() == "Set up WSJT-X"
    source_windows._refresh_skim_wsjtx(host)
    assert host._skim_wsjtx_val.text() == ""          # nothing set up: no line
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a: shown.append(a[2])))
    host.radio_client = None
    source_windows._on_set_up_wsjtx(host)
    assert shown and "own panadapter" in shown[0]
    host._skim_wsjtx_timer.stop()
    host.deleteLater()
