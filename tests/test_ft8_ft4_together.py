# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8 and FT4 decoded together from one receiver.

Each enabled mode runs its own period clock, decode history and jt9 run
(FT4 with jt9's FT4 flag), sends its own UDP regions, and the Mode menu
keeps FT8 / FT4 as independent check marks with MSK144 on its own.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ws_skim import costas_search as cs  # noqa: E402
from ws_skim import ft8_engine  # noqa: E402

FS = 48000.0


class _Settings(dict):
    def value(self, key, default=None, type=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ── pure pieces ──────────────────────────────────────────────────────────

def test_normalize_and_enabled_period_modes():
    n = ft8_engine.normalize_period_modes
    assert n(["FT4", "ft8", "FT4", "MSK144"]) == ("FT8", "FT4")
    assert n("FT4,FT8") == ("FT8", "FT4") and n("FT8+FT4") == ("FT8", "FT4")
    assert n("FT4") == ("FT4",) and n(()) == () and n(None) == ()

    class E:
        mode = "FT4"
    assert ft8_engine.enabled_period_modes(E()) == ("FT4",)
    E.mode = "MSK144"
    assert ft8_engine.enabled_period_modes(E()) == ()
    E.period_modes = ("FT4", "FT8")
    assert ft8_engine.enabled_period_modes(E()) == ("FT8", "FT4")


def test_dial_setting_only_for_primary_mode():
    class E:
        mode = "FT8"
        calling_freq_mhz = 14.074
    assert ft8_engine._dial_setting_for(E(), "FT8") == 14.074
    assert ft8_engine._dial_setting_for(E(), "FT4") is None
    # FT4 next to FT8 on 20 m gets its own standard dial, 14.080.
    assert ft8_engine.pick_dial_mhz("FT4", 14.077, FS, None) == pytest.approx(14.080)
    assert ft8_engine.pick_dial_mhz("FT8", 14.077, FS, 14.074) == pytest.approx(14.074)


def test_decode_rates_per_mode():
    class E:
        period_modes = ("FT8", "FT4")
    e = E()
    assert ft8_engine.decode_rates(e) == {"FT8": 0.0, "FT4": 0.0}
    for k in range(4):                        # one minute of FT8, 15 decodes a period
        ft8_engine._count_rate(e, "FT8", 15.0 * k, 15)
    ft8_engine._count_rate(e, "FT4", 0.0, 3)  # one FT4 period: 3 in 7.5 s
    r = ft8_engine.decode_rates(e)
    assert r["FT8"] == pytest.approx(60.0) and r["FT4"] == pytest.approx(24.0)
    assert ft8_engine.rate_text(e) == "FT8 60/min · FT4 24/min"
    for k in range(4, 12):                    # the window slides: old periods drop
        ft8_engine._count_rate(e, "FT8", 15.0 * k, 0)
    assert ft8_engine.decode_rates(e)["FT8"] == 0.0


def test_set_period_modes_keeps_other_mode_state():
    from ws_skim.engine import Engine
    e = Engine(sample_rate=int(FS))
    with pytest.raises(ValueError):
        e.set_period_modes(("MSK144",))
    e.set_period_modes(("FT4", "FT8"))
    assert e.mode == "FT8" and e.period_modes == ("FT8", "FT4")
    hist = ft8_engine.DecodeHistory()
    e._ft8_histories["FT8"] = hist
    e._ft8_last_by.update(FT8=10, FT4=20)
    e.set_period_modes(("FT8",))                 # FT4 off: FT8 keeps its history
    assert e._ft8_histories["FT8"] is hist and "FT4" not in e._ft8_last_by
    assert e._ft8_last_by["FT8"] == 10
    e.set_period_modes(("FT8", "FT4"))            # FT4 back on: fresh FT4 clock
    assert e._ft8_last_by == {"FT8": 10}
    e.set_mode("MSK144")
    assert e.period_modes == () and e._ft8_histories == {}


# ── engine: both modes from one IQ stream ──────────────────────────────

def _drain(e):
    for th in list(getattr(e, "_ft8_threads", {}).values()):
        th.join(timeout=30)


def test_engine_decodes_ft8_and_ft4_together(tmp_path, monkeypatch):
    from ws_skim import ft8_decode
    from ws_skim.engine import Engine
    monkeypatch.setenv("WS_SKIM_OUTPUT_DIR", str(tmp_path))
    calls = []

    def fake_jt9(cmd, cwd, timeout_s=0):
        calls.append(cmd)
        if cmd[1] == ft8_decode.JT9_MODE_FLAG["FT4"]:
            return "000000 -12  0.1 1200 +  CQ W1AW FN31\n"
        return "000000 -10  0.3 1500 ~  CQ K1JT FN20\n"

    monkeypatch.setattr(ft8_decode, "run_jt9", fake_jt9)
    e = Engine(center_freq_mhz=14.077, sample_rate=int(FS), calling_freq_mhz=14.074)
    e._ft8_jt9 = "jt9"
    e.set_blanker("Bypass")
    e.set_period_modes(("FT8", "FT4"))
    x = cs.synth_noise(FS, int(36 * FS), rng=np.random.default_rng(3))
    chunk = 8192
    e.process_iq_data(x[:chunk], 0, 0)
    tb = e.timebase
    assert tb.is_anchored()
    tb.should_mark_period = lambda wall: False      # fed faster than real time
    for s in range(chunk, len(x), chunk):
        e.process_iq_data(x[s:s + chunk], 0, 0)
        _drain(e)
    _drain(e)
    r8, r4 = e._ft8_results.get("FT8"), e._ft8_results.get("FT4")
    assert r8 is not None and r4 is not None
    assert r8.mode == "FT8" and r4.mode == "FT4" and r8.decoded and r4.decoded
    assert r4.period_start_utc / 7.5 == pytest.approx(round(r4.period_start_utc / 7.5))
    # Each mode's always-decoded dial section sits on its own standard dial.
    assert any(abs(s.base_hz - (-3000.0)) <= 300 for s in r8.sections)
    assert any(abs(s.base_hz - 3000.0) <= 300 for s in r4.sections)
    flags = {c[1]: c[c.index("-p") + 1] for c in calls}
    assert flags == {"-8": "15", "-5": "7.5"}
    # FT4 ran more periods than FT8 over the same stream (7.5 s vs 15 s).
    assert e._ft8_last_by["FT4"] > e._ft8_last_by["FT8"]
    recs = []
    while not e._decode_queue.empty():
        recs.append(e._decode_queue.get_nowait())
    by_mode = {r["mode"] for r in recs}
    assert by_mode == {"FT8", "FT4"}
    ft4 = next(r for r in recs if r["mode"] == "FT4")
    assert ft4["message"] == "CQ W1AW FN31"
    assert ft4["dial_khz"] == pytest.approx(14080.0, abs=0.5)
    rates = ft8_engine.decode_rates(e)
    assert rates["FT8"] > 0 and rates["FT4"] > 0


def test_udp_instances_per_mode():
    from ws_skim.wsjtx_udp import instance_id
    a = instance_id("radio", 14_074_000, "FT8")
    b = instance_id("radio", 14_080_000, "FT4")
    assert a.endswith("-20m-FT8-74") and b.endswith("-20m-FT4-80") and a != b


# ── UI: Mode menu ─────────────────────────────────────────────────────

def test_saved_modes_migrates_single_mode():
    from ws_skim import ft8_window
    assert ft8_window.saved_modes(_Settings()) == ("MSK144",)
    assert ft8_window.saved_modes(_Settings(mode="MSK144")) == ("MSK144",)
    assert ft8_window.saved_modes(_Settings(mode="FT8")) == ("FT8", "FT4")
    assert ft8_window.saved_modes(_Settings(mode="FT4")) == ("FT8", "FT4")
    assert ft8_window.saved_modes(_Settings(mode="FT8", period_modes="FT8")) == ("FT8",)
    assert ft8_window.saved_modes(_Settings(mode="FT4", period_modes="FT4")) == ("FT4",)
    assert ft8_window.saved_modes(_Settings(mode="FT8", period_modes=["FT4", "FT8"])) \
        == ("FT8", "FT4")
    assert ft8_window.saved_modes(_Settings(mode="JT65")) == ("MSK144",)


def test_mode_menu_ft8_ft4_independent_msk144_exclusive(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    from ws_skim import ft8_window
    settings = _Settings(mode="FT8")              # an older install: FT8 only
    monkeypatch.setattr(vis, "_SETTINGS", settings)

    class Eng(QtWidgets.QMainWindow):
        mode = "MSK144"
        period_modes = ()
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
    try:
        bar = eng.menuBar()
        view = bar.addMenu("&View")
        bar.addMenu("&Diagnostics")
        ft8_window.setup_ft8(eng, bar, view)
        acts = eng._mode_actions
        checked = lambda: tuple(m for m, a in acts.items() if a.isChecked())  # noqa: E731
        # Migration: the old FT8 setting comes back as FT8 + FT4.
        assert checked() == ("FT8", "FT4") and eng.period_modes == ("FT8", "FT4")
        assert settings["period_modes"] == "FT8,FT4" and settings["mode"] == "FT8"
        acts["FT8"].trigger()                      # FT8 off: FT4 alone
        assert checked() == ("FT4",) and eng.period_modes == ("FT4",)
        assert settings["period_modes"] == "FT4"
        acts["FT4"].trigger()                      # last one cannot go off
        assert checked() == ("FT4",) and eng.period_modes == ("FT4",)
        acts["MSK144"].trigger()                   # MSK144 turns both off
        assert checked() == ("MSK144",) and eng.mode == "MSK144"
        assert settings["mode"] == "MSK144"
        acts["FT8"].trigger()
        assert checked() == ("FT8",) and eng.period_modes == ("FT8",)
        acts["FT4"].trigger()
        assert checked() == ("FT8", "FT4")
        assert ft8_window.saved_modes(settings) == ("FT8", "FT4")
        # Status bar reports a rate per mode.
        ft8_window._update_rate_label(eng)
        assert eng._ft8_rate_label.text() == "FT8 0/min · FT4 0/min"
    finally:
        for name in ("_band_activity_win", "_ft8_win"):
            win = getattr(eng, name, None)
            for t in ("_ba_timer", "_ft8_timer"):
                timer = getattr(win, t, None)
                if timer is not None:
                    timer.stop()
            if win is not None:
                win._parent_win = None
                win.hide()
        eng._ft8_rate_timer.stop()


def test_sync_map_show_box_picks_mode(qapp, monkeypatch):
    """With FT8 and FT4 both on, the Sync Map's Show box picks the mode
    (it moved there from the old FT8 Activity window)."""
    from types import SimpleNamespace
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    from ws_skim import ft8_window
    settings = _Settings(activity_view_mode="FT4")     # migrated once
    monkeypatch.setattr(vis, "_SETTINGS", settings)

    class Eng(QtWidgets.QMainWindow):
        mode = "FT8"
        period_modes = ("FT8", "FT4")
        sample_rate = 48000.0
        _ft8_result = None

        def set_period_modes(self, ms):
            self.period_modes = tuple(ms)
            self.mode = ms[0]

        def set_blanker(self, b):
            pass

    eng = Eng()
    bar = eng.menuBar()
    view = bar.addMenu("&View")
    bar.addMenu("&Diagnostics")
    try:
        monkeypatch.setattr(ft8_window, "saved_modes", lambda _s: ("FT8", "FT4"))
        ft8_window.setup_ft8(eng, bar, view)
        win = eng._ft8_win
        assert eng._ft8_view_mode == "FT4"                    # from the old setting
        r8 = SimpleNamespace(mode="FT8")
        r4 = SimpleNamespace(mode="FT4")
        eng._ft8_results = {"FT8": r8, "FT4": r4}
        ft8_window._tick(eng, win)
        combo = win._ft8_show_combo
        assert [combo.itemText(i) for i in range(combo.count())] == ["FT8", "FT4"]
        assert not combo.isHidden() and combo.currentText() == "FT4"
        assert ft8_window.shown_result(eng) is r4
        combo.setCurrentText("FT8")
        assert eng._ft8_view_mode == "FT8" and settings["sync_map_mode"] == "FT8"
        assert win.windowTitle() == "WS Skim — FT8 Sync Map"
        assert ft8_window.shown_result(eng) is r8
        # FT8 turned off: the Sync Map follows to FT4 and the Show box hides.
        eng.period_modes = ("FT4",)
        eng._ft8_results = {"FT4": r4}
        ft8_window._tick(eng, win)
        assert eng._ft8_view_mode == "FT4" and combo.isHidden()
        # Band Activity is the View menu's first item and opened with FT8/FT4.
        assert view.actions()[0].text() == "Band Activity"
        assert eng._band_activity_win.isVisible()
    finally:
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
