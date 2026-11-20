# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Status-only main window, the Roster window, and MSK144 band limits."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ws_skim import status_panel as sp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
T0 = 1_700_000_010.0


class _Settings(dict):
    def value(self, key, default=None, type=None):
        v = self.get(key, default)
        if type is bool and isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return v

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ── Status rows (pure) ─────────────────────────────────────────────────────

def test_iq_health_levels():
    eng = SimpleNamespace(source_mode="idle", sample_rate=48000.0, _iq_abs_sample=0)
    assert sp.iq_health(eng, 0.0) == ("no source", sp.OFF)
    eng.source_mode = "radio"
    assert sp.iq_health(eng, 100.0)[1] == sp.WARN            # just started
    eng._iq_abs_sample = 48000 * 2
    text, level = sp.iq_health(eng, 102.0)
    assert level == sp.OK and "48.0 kS/s" in text and "100%" in text
    eng._iq_abs_sample += int(48000 * 0.7 * 3)                # 70% for 3 s
    assert sp.iq_health(eng, 105.0)[1] == sp.WARN
    eng._iq_abs_sample += 0                                   # stalled
    for t in (106.0, 107.0, 108.0, 109.0):
        text, level = sp.iq_health(eng, t)
    assert level == sp.ERR and "no data" in text


def test_reporting_rows():
    rpt = SimpleNamespace(
        wsjtx_enabled=True, wsjtx_host="224.0.0.73", wsjtx_port=2237, stat_udp_sent=1234,
        instance_ids=lambda: ["SKIM-FLEX-20m-FT8-74"], pskreporter_enabled=False,
        dx_enabled=True, stat_dx_status="connected: dxc.ve7cc.net:7373", stat_dx_sent=7,
        stat_psk_uploaded=0, stat_psk_queued=0, stat_last_psk_time="", stat_last_error="")
    rows = sp.reporting_rows(rpt)
    assert rows["udp"].value == "on → 224.0.0.73:2237 · 1,234 sent"
    assert rows["udp"].level == sp.OK and "SKIM-FLEX-20m-FT8-74" in rows["udp"].tip
    assert rows["psk"].value == "off" and rows["psk"].level == sp.OFF
    assert rows["dx"].level == sp.OK and "7 spots" in rows["dx"].value
    assert rows["error"].value == "none"
    rpt.stat_last_error = "UDP send: network unreachable"
    rows = sp.reporting_rows(rpt)
    assert rows["udp"].level == sp.ERR and rows["error"].level == sp.ERR
    assert sp.reporting_rows(None)["udp"].level == sp.OFF


def test_status_rows_modes_and_filtered(monkeypatch):
    from ws_skim import ft8_engine
    eng = SimpleNamespace(source_mode="radio", sample_rate=48000.0, _iq_abs_sample=0,
                          mode="FT8", period_modes=("FT8", "FT4"),
                          _ft8_filtered={"ap": 3, "call": 1}, reporter=None)
    monkeypatch.setattr(ft8_engine, "decode_rates", lambda e: {"FT8": 312.0, "FT4": 48.0})
    rows = sp.status_rows(eng, now=1.0)
    assert rows["band"].value == "FT8 + FT4"
    assert rows["decodes"].value == "FT8 312/min · FT4 48/min"
    assert rows["filtered"].value == "4 since start" and "ap 3" in rows["filtered"].tip
    assert rows["channels"].level == sp.OFF                  # not MSK144
    assert set(rows) == {k for k, _t, _tip in sp.ROWS}
    eng.source_mode = "idle"
    assert sp.status_rows(eng)["receiver"].value.startswith("none")


def test_status_panel_widgets(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    from ws_skim import ft8_engine
    monkeypatch.setattr(ft8_engine, "decode_rates", lambda e: {})

    class Eng(QtWidgets.QMainWindow):
        source_mode = "idle"
        mode = "MSK144"
        sample_rate = 48000.0
        reporter = None

    eng = Eng()
    for name in ("_receiver_label", "tuned_freq_label", "_msk_monitor_label",
                 "utc_clock_label"):
        setattr(eng, name, QtWidgets.QLabel("—"))
    panel = sp.build_status_panel(eng)
    eng.setCentralWidget(panel)
    eng.show()
    sp.update_status_panel(eng)
    dot, name, value, _tip = eng._status_widgets["udp"]
    assert name.text() == "WSJT-X UDP" and value.text() == "not started"
    assert sp.LEVEL_COLORS[sp.OFF] in dot.text()
    assert "Status: off" in value.toolTip() and "WS Hub" in value.toolTip()
    assert eng._status_widgets["channels"][2].isVisible()       # MSK144: shown
    assert eng._status_widgets["receiver"][2] is eng._receiver_label
    eng.mode = "FT8"
    eng.period_modes = ("FT8",)
    sp.update_status_panel(eng)
    assert not eng._status_widgets["channels"][2].isVisible()
    eng.hide()


# ── Roster ─────────────────────────────────────────────────────────────────

def _table(qapp):
    from ws_skim.roster_window import RosterTable
    return RosterTable()


def _add(t, epoch, snr, msg="CQ K1ABC FN42", mode="FT8", khz=14075.0, df=1000):
    import time
    return t.add_decode(epoch=epoch, utc_time=time.strftime("%H:%M:%S", time.gmtime(epoch)),
                        mode=mode, radio_khz=khz, df_hz=df, message=msg, snr=snr,
                        now=T0 + 60)


def test_roster_sorts_newest_first_and_by_header(qapp):
    from PyQt5 import QtCore
    from ws_skim import roster_window as rw
    t = _table(qapp)
    assert [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())] == \
        list(rw.COLUMNS)
    assert t.isColumnHidden(rw.COL_POL)          # no dual-pol decodes yet
    _add(t, T0, -12, "CQ W1AW FN31")
    _add(t, T0 + 30, 3, "CQ K1ABC FN42")
    _add(t, T0 + 15, -20, "W1AW K9XYZ EN52")
    assert [t.item(r, rw.COL_CALL).text() for r in range(3)] == ["K1ABC", "K9XYZ", "W1AW"]
    t.horizontalHeader().sectionClicked.emit(rw.COL_SNR)     # header click
    t.sortItems(rw.COL_SNR, QtCore.Qt.AscendingOrder)
    assert [t.item(r, rw.COL_SNR).text() for r in range(3)] == ["-20", "-12", "+3"]
    t.sortItems(rw.COL_FREQ, QtCore.Qt.DescendingOrder)
    t.clear()
    assert t.rowCount() == 0 and t.horizontalHeaderItem(0).text() == "UTC"


def test_roster_age_colours_trim_and_counts(qapp, monkeypatch):
    from ws_skim import roster_window as rw
    t = _table(qapp)
    _add(t, T0, 0)
    assert t.item(0, 0).foreground().color().getRgb()[:3] == (0, 0, 0)   # 60 s old
    t.recolor(now=T0 + 600)
    assert t.item(0, 0).foreground().color().getRgb()[:3] == rw.age_rgb(600)
    assert rw.age_rgb(5000) == (140, 140, 140)
    monkeypatch.setattr(rw, "MAX_ROWS", 3)
    for k in range(1, 5):
        _add(t, T0 + 15 * k, 0)
    assert t.rowCount() == 3
    assert min(t.epoch_at(r) for r in range(3)) == T0 + 30     # oldest dropped
    assert t.recent_count(40, now=T0 + 60) == 3
    assert t.recent_count(40, mode="MSK144", now=T0 + 60) == 0


def test_roster_queue_records(qapp):
    from ws_skim import roster_window as rw
    t = _table(qapp)
    rw.add_queue_record(t, {"mode": "FT8", "message": "CQ K1ABC FN42", "radio_khz": 14075.5,
                            "audio_hz": 1500.0, "jt9_snr": -7, "utc_time": "22:13:30",
                            "timestamp": "2023-11-14_22:13:30"}, 14.074)
    rw.add_queue_record(t, {"mode": "MSK144", "message": "W1AW K2AA R-03",
                            "radio_khz": 50260.4, "jt9_snr": 2, "utc_time": "22:13:30",
                            "t_sec": 4.0, "theta_deg": 45.0, "wav_path": "/x/y.wav"}, 50.260)
    rows = {t.item(r, rw.COL_MODE).text(): r for r in range(2)}
    assert t.item(rows["FT8"], rw.COL_DF).text() == "1500"
    assert t.item(rows["FT8"], rw.COL_SNR).text() == "-7"
    assert t.item(rows["MSK144"], rw.COL_DF).text() == "+400"
    assert t.item(rows["MSK144"], rw.COL_POL).text() == "45°"
    assert not t.isColumnHidden(rw.COL_POL)
    assert t.wav_at(rows["MSK144"]) == "/x/y.wav" and t.wav_at(rows["FT8"]) is None


def test_roster_window_visibility_setting(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    from ws_skim import roster_window as rw
    for saved, shown in (({}, True), ({rw.VISIBLE_KEY: "false"}, False)):
        monkeypatch.setattr(vis, "_SETTINGS", _Settings(saved))
        eng = QtWidgets.QMainWindow()
        act = QtWidgets.QAction("Roster", eng, checkable=True)
        rw.setup_roster_window(eng, act)
        assert eng.decode_panel is eng._roster_win._roster_table
        assert eng._roster_win.isVisible() is shown and act.isChecked() is shown
        assert eng._roster_win.windowTitle() == "WS Skim — Roster"
        eng._roster_win._parent_win = None
        eng._roster_win.hide()


# ── MSK144 bands ───────────────────────────────────────────────────────────

def test_msk144_offered_on_6_2_and_1p25_m_only():
    from ws_skim import ui
    assert [f for _l, f, _s in ui.MSK144_BANDS] == [50.260, 144.150, 222.090]
    for f in (50.260, 50.313, 144.174, 222.090):
        assert ui.msk144_allowed(f), f
    for f in (28.145, 28.074, 14.074, 70.230, 432.360, None, "x"):
        assert not ui.msk144_allowed(f), f
    assert ui.msk144_band_for(50.313) == 50.260
    assert ui.msk144_band_for(144.174) == 144.150
    assert ui.msk144_band_for(14.074) is None


def _mode_eng(qapp, mode="FT8", freq=14.074):
    from PyQt5 import QtWidgets

    class Eng(QtWidgets.QMainWindow):
        _ft8_result = None

        def set_mode(self, m):
            self.mode = m
            self.period_modes = (m,) if m != "MSK144" else ()

        def set_period_modes(self, ms):
            self.period_modes = tuple(ms)
            self.mode = ms[0]

        def set_blanker(self, b):
            pass

    eng = Eng()
    eng.mode = mode
    eng.period_modes = ()
    eng.calling_freq_mhz = freq
    eng.sample_rate = 48000.0
    return eng


def _cleanup(eng):
    for name in ("_band_activity_win", "_ft8_win"):
        w = getattr(eng, name, None)
        for t in ("_ba_timer", "_ft8_timer"):
            timer = getattr(w, t, None)
            if timer is not None:
                timer.stop()
        if w is not None:
            w._parent_win = None
            w.hide()
    eng._ft8_rate_timer.stop()


def test_msk144_menu_disabled_off_vhf_and_saved_msk144_on_70cm_falls_back(qapp, monkeypatch):
    import ws_skim.visualizer as vis
    from ws_skim import ft8_window
    settings = _Settings(mode="MSK144", calling_freq_mhz=432.360)
    monkeypatch.setattr(vis, "_SETTINGS", settings)
    eng = _mode_eng(qapp, mode="MSK144", freq=14.074)
    try:
        bar = eng.menuBar()
        view = bar.addMenu("&View")
        bar.addMenu("&Diagnostics")
        ft8_window.setup_ft8(eng, bar, view)
        assert eng.period_modes == ("FT8", "FT4")              # fell back
        act = eng._mode_actions["MSK144"]
        assert not act.isEnabled() and "6 m, 2 m and 1.25 m" in act.toolTip()
        # On 6 m (FT8 50.313) MSK144 is offered; choosing it stays on 6 m.
        eng.calling_freq_mhz = 50.313
        ft8_window.update_msk144_action(eng)
        assert act.isEnabled()
        act.trigger()
        assert eng.mode == "MSK144" and settings["calling_freq_mhz"] == 50.260
        assert act.isEnabled()
    finally:
        _cleanup(eng)


def test_saved_msk144_on_2m_is_kept(qapp, monkeypatch):
    import ws_skim.visualizer as vis
    from ws_skim import ft8_window
    monkeypatch.setattr(vis, "_SETTINGS", _Settings(mode="MSK144", calling_freq_mhz=144.150))
    eng = _mode_eng(qapp, mode="MSK144", freq=144.150)
    try:
        bar = eng.menuBar()
        ft8_window.setup_ft8(eng, bar, bar.addMenu("&View"))
        assert eng.mode == "MSK144" and eng._mode_actions["MSK144"].isChecked()
    finally:
        _cleanup(eng)


# ── The real main window, in a separate process (own settings) ────────────

_SMOKE = textwrap.dedent('''
    import os, sys, time, json
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, sys.argv[1])
    from PyQt5 import QtWidgets, QtCore
    app = QtWidgets.QApplication(sys.argv)
    from ws_skim.visualizer import _SETTINGS
    w0 = QtWidgets.QWidget(); w0.setGeometry(40, 60, 420, 800)
    _SETTINGS.setValue("window_geometry", w0.saveGeometry())     # old main window
    from ws_skim.visualizer import WSSkimVisualizer
    w = WSSkimVisualizer()
    w.show()
    for _ in range(10):
        app.processEvents(); time.sleep(0.02)
    w.update_displays()
    menus = {a.text().replace("&", ""): [x.text() for x in a.menu().actions()]
             for a in w.menuBar().actions() if a.menu()}
    out = {
        "central": sorted(w._status_widgets),
        "view": menus["View"],
        "roster": type(w.decode_panel).__name__,
        "roster_visible": w._roster_win.isVisible(),
        "size": [w.width(), w.height()],
        "band": w._status_widgets["band"][2].text(),
    }
    print("RESULT " + json.dumps(out)); sys.stdout.flush()
    os._exit(0)
''')


def test_real_main_window_is_status_only(tmp_path):
    script = tmp_path / "smoke.py"
    script.write_text(_SMOKE)
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / ".config"),
               QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, str(script), str(REPO)], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=180)
    line = next((x for x in p.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line is not None, p.stdout[-2000:] + p.stderr[-2000:]
    import json
    r = json.loads(line[7:])
    assert r["central"] == sorted(k for k, _t, _tip in sp.ROWS)
    assert r["view"][:2] == ["Band Activity", "Roster"]
    assert "Waterfall" not in r["view"]
    assert r["roster"] == "RosterTable" and r["roster_visible"]
    assert r["size"][1] < 800                          # old tall geometry: position only
    assert "MSK144" in r["band"]
