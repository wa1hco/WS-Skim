# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8 Activity view: period binning, scrolling window, SNR colours, menus."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ws_skim import activity_window as aw  # noqa: E402
from ws_skim.costas_search import Candidate  # noqa: E402
from ws_skim.ft8_decode import Decode  # noqa: E402

CENTER = 14.074
T0 = 1_700_000_010.0          # an FT8 period boundary (divisible by 15)


@dataclass
class _Sec:
    base_hz: float
    low_audio_hz: float = 500.0
    high_audio_hz: float = 2500.0
    candidates: list = field(default_factory=list)


@dataclass
class _Res:
    mode: str
    period_start_utc: float
    center_freq_mhz: float = CENTER
    candidates: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    decodes: list = field(default_factory=list)
    decoded: bool = False


def _dec(start, rf_hz, snr, msg="CQ K1ABC FN42", dt=0.2, mode="FT8"):
    return Decode(mode=mode, period_start_utc=start, snr_db=snr, dt_s=dt,
                  audio_hz=1000.0, rf_hz=rf_hz, dial_hz=rf_hz - 1000.0, message=msg)


def _res(start, decodes=(), n_cands=2, mode="FT8"):
    cands = [Candidate(-3.0, freq_hz=-10_000.0 + 3000 * i, dt_s=0.1, score=3.0, mode=mode)
             for i in range(n_cands)]
    secs = [_Sec(base_hz=-12_000.0), _Sec(base_hz=-9_000.0)]
    return _Res(mode=mode, period_start_utc=start, candidates=cands, sections=secs,
                decodes=list(decodes), decoded=bool(decodes))


def test_period_binning_snaps_to_period_start():
    assert aw.period_start(T0 + 14.9, 15.0) == T0
    assert aw.period_start(T0 + 15.0, 15.0) == T0 + 15
    assert aw.period_start(T0 + 3.0, 7.5) == T0
    assert aw.period_start(T0 + 8.0, 7.5) == T0 + 7.5
    e = aw.entry_from_result(_res(T0 + 0.3, [_dec(T0, 14_075_000.0, -5)]))
    assert e.start == T0
    m = e.decodes[0]
    assert T0 < m.t0 < m.t1 < T0 + 15                    # inside its column
    assert m.f_lo_mhz == pytest.approx(14.075) and m.f_hi_mhz == pytest.approx(14.07505)
    assert m.call == "K1ABC" and m.snr_db == -5
    assert len(e.sections) == 2 and len(e.candidates) == 2


def test_history_scrolls_and_keeps_n_periods():
    h = aw.ActivityHistory(n_periods=4)
    for k in range(10):
        assert h.add(_res(T0 + 15 * k, [_dec(T0 + 15 * k, 14_075_000.0 + k, -10)]))
    assert len(h.entries) == 4
    assert sorted(h.entries) == [T0 + 15 * k for k in range(6, 10)]
    assert h.x_range() == (T0 + 15 * 6, T0 + 15 * 10)       # 4 periods = 60 s
    assert aw.view_x_range(T0, 15.0, 8) == (T0 - 105.0, T0 + 15.0)  # 2 minutes
    h.set_periods(2)
    assert sorted(h.entries) == [T0 + 15 * 8, T0 + 15 * 9]
    # A period older than the window is ignored.
    assert not h.add(_res(T0, [_dec(T0, 14_075_000.0, -1)]))
    assert aw.clamp_periods(0) == aw.MIN_PERIODS
    assert aw.clamp_periods(999) == aw.MAX_PERIODS
    assert aw.clamp_periods("bad") == aw.DEFAULT_PERIODS


def test_history_updates_period_when_decodes_arrive_and_resets_on_mode():
    h = aw.ActivityHistory()
    r = _res(T0)
    assert h.add(r)
    v = h.version
    assert not h.add(r)                                    # same content: no redraw
    assert h.version == v
    assert h.add(replace(r, decodes=[_dec(T0, 14_075_000.0, -3)], decoded=True))
    assert len(h.decodes()) == 1 and len(h.entries) == 1
    assert h.add(_res(T0 + 7.5, mode="FT4"))               # mode change clears
    assert h.mode == "FT4" and list(h.entries) == [T0 + 7.5]
    assert h.add(replace(_res(T0 + 15, mode="FT4"), center_freq_mhz=7.074))
    assert list(h.entries) == [T0 + 15]                   # band change clears


def test_snr_color_mapping():
    assert aw.snr_fraction(-40) == 0.0
    assert aw.snr_fraction(30) == 1.0
    assert aw.snr_fraction(aw.SNR_MIN) == 0.0
    assert aw.snr_fraction(aw.SNR_MAX) == 1.0
    weak, strong = aw.snr_color(-24), aw.snr_color(10)
    assert weak == (150, 190, 235)
    assert strong == (200, 0, 0)
    fr = [aw.snr_fraction(s) for s in (-24, -15, -5, 0, 10)]
    assert fr == sorted(fr) and len(set(fr)) == 5
    assert aw.snr_color(0)[0] > aw.snr_color(0)[2]          # 0 dB is on the warm side
    assert aw.snr_color(-18)[2] > aw.snr_color(-18)[0]      # weak is on the blue side


def test_nearest_and_labels_hook():
    h = aw.ActivityHistory()
    h.add(_res(T0, [_dec(T0, 14_075_000.0, -3, "CQ K1ABC FN42"),
                    _dec(T0, 14_076_000.0, -12, "W1AW K9XYZ -12")]))
    m = h.nearest(T0 + 7, 14.076_02, f_tol_mhz=0.0001)
    assert m is not None and m.call == "K9XYZ"
    assert "K9XYZ" in m.tooltip() and "-12 dB" in m.tooltip()
    assert h.nearest(T0 + 20, 14.076_02, f_tol_mhz=0.0001) is None
    c = h.nearest(T0 + 7, 14.074 - 0.010 + 0.00002, 0.0001, include_candidates=True)
    assert c is not None and not c.is_decode and "not decoded" in c.tooltip()
    decs = h.decodes()
    assert aw.labelled_marks(decs, False, set()) == []
    assert [m.call for m in aw.labelled_marks(decs, False, {"k9xyz"})] == ["K9XYZ"]
    assert len(aw.labelled_marks(decs, True, set())) == 2


class _Settings(dict):
    def value(self, key, default=None, type=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


_ENGINES = []


@pytest.fixture(autouse=True)
def _stop_panel_timers():
    """Stop the 1 s panel timers so they never fire after a test's engine is gone."""
    yield
    while _ENGINES:
        eng = _ENGINES.pop()
        for name in ("_activity_win", "_ft8_win"):
            win = getattr(eng, name, None)
            if win is None:
                continue
            for t in ("_act_timer", "_ft8_timer"):
                timer = getattr(win, t, None)
                if timer is not None:
                    timer.stop()
            win._parent_win = None
            win.hide()


def test_window_draws_scrolls_and_remembers_settings(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    settings = _Settings(activity_periods=3)
    monkeypatch.setattr(vis, "_SETTINGS", settings)

    class Eng(QtWidgets.QMainWindow):
        mode = "FT8"
        sample_rate = 48000.0
        _ft8_result = None

    eng = Eng()
    _ENGINES.append(eng)
    act = QtWidgets.QAction("FT8 Activity", eng, checkable=True)
    win = aw.build_window(eng, act)
    assert win._act_spin.value() == 3
    assert not win._act_calls_cb.isChecked()
    win.show()
    for k in range(5):
        eng._ft8_result = _res(T0 + 15 * k, [_dec(T0 + 15 * k, 14_075_000.0, -20 + 5 * k)])
        aw.tick(eng, win)
    assert win._act_ticks.mark_count() == 3
    (x0, x1), (y0, y1) = win._act_plot.getViewBox().viewRange()
    assert x0 == pytest.approx(T0 + 30) and x1 == pytest.approx(T0 + 75)
    assert y0 < 14.075 < y1
    assert sum(lab.isVisible() for lab in win._act_labels) == 0     # calls off
    # Frequency zoom survives the next period; time scrolls by one period.
    win._act_plot.setYRange(14.0745, 14.0755, padding=0)
    eng._ft8_result = _res(T0 + 75, [_dec(T0 + 75, 14_075_000.0, 0)])
    aw.tick(eng, win)
    (x0, x1), (y0, y1) = win._act_plot.getViewBox().viewRange()
    assert x0 == pytest.approx(T0 + 45) and y0 == pytest.approx(14.0745)
    win._act_calls_cb.setChecked(True)
    assert settings["activity_show_calls"] is True
    assert sum(lab.isVisible() for lab in win._act_labels) == 3
    win._act_calls_cb.setChecked(False)
    aw.set_highlighted_calls(win, ["K1ABC"])
    assert sum(lab.isVisible() for lab in win._act_labels) == 3     # all K1ABC
    win._act_spin.setValue(5)
    assert settings["activity_periods"] == 5
    win._act_cand_cb.setChecked(True)
    assert win._act_cands.mark_count() == 2 * 3        # 3 periods kept
    assert "stations" in win._act_status.text()
    win.hide()


def test_hidden_window_records_but_does_not_draw(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    monkeypatch.setattr(vis, "_SETTINGS", _Settings())

    class Eng(QtWidgets.QMainWindow):
        mode = "FT8"
        _ft8_result = None

    eng = Eng()
    _ENGINES.append(eng)
    win = aw.build_window(eng, QtWidgets.QAction("FT8 Activity", eng, checkable=True))
    eng._ft8_result = _res(T0, [_dec(T0, 14_075_000.0, -3)])
    aw.tick(eng, win)
    assert len(win._act_history.entries) == 1
    assert win._act_ticks.mark_count() == 0                 # nothing drawn while hidden
    eng.mode = "MSK144"
    aw.tick(eng, win)
    assert "MSK144" in win._act_status.text()


def test_setup_puts_activity_in_view_and_sync_map_in_diagnostics(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    from ws_skim import ft8_window
    monkeypatch.setattr(vis, "_SETTINGS", _Settings(mode="FT8"))

    class Eng(QtWidgets.QMainWindow):
        mode = "MSK144"
        _ft8_result = None

        def set_mode(self, m):
            self.mode = m

        def set_blanker(self, b):
            pass

    eng = Eng()
    _ENGINES.append(eng)
    bar = eng.menuBar()
    view = bar.addMenu("&View")
    view.addAction("Fast Graph")
    diag = bar.addMenu("&Diagnostics")
    ft8_window.setup_ft8(eng, bar, view)
    assert view.actions()[0].text() == "FT8 Activity"
    diag_labels = [a.text() for a in diag.actions()]
    assert "FT8 Sync Map" in diag_labels
    assert "FT8 Sync Map" not in [a.text() for a in view.actions()]
    assert eng._activity_win.isVisible()                   # opens with FT8
    assert not eng._ft8_win.isVisible()                    # sync map only on request
    assert eng._activity_win.windowTitle() == "WS Skim — FT8 Activity"
    eng._sync_map_action.trigger()
    assert eng._ft8_win.isVisible()
    ft8_window.apply_mode(eng, "MSK144")
    assert not eng._activity_win.isVisible() and not eng._ft8_win.isVisible()


def test_time_axis_stays_locked_to_history(qapp, monkeypatch):
    """A wheel zoom or View All must not stretch time (ticks bunched left)."""
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    monkeypatch.setattr(vis, "_SETTINGS", _Settings(activity_periods=4))

    class Eng(QtWidgets.QMainWindow):
        mode = "FT8"
        sample_rate = 48000.0
        _ft8_result = None

    eng = Eng()
    _ENGINES.append(eng)
    win = aw.build_window(eng, QtWidgets.QAction("FT8 Activity", eng, checkable=True))
    win.show()
    vb = win._act_plot.getViewBox()
    assert vb.state["mouseEnabled"] == [False, True]        # wheel/drag: frequency only
    eng._ft8_result = _res(T0, [_dec(T0, 14_075_000.0, -3)])
    aw.tick(eng, win)
    win._act_plot.setXRange(T0 - 600, T0 + 600, padding=0)  # e.g. View All
    aw.tick(eng, win)                                        # same period: re-pinned
    (x0, x1), _ = vb.viewRange()
    assert x0 == pytest.approx(T0 - 45) and x1 == pytest.approx(T0 + 15)
    win._act_plot.setXRange(T0 - 600, T0 + 600, padding=0)
    eng._ft8_result = _res(T0 + 15, [_dec(T0 + 15, 14_075_000.0, -3)])
    aw.tick(eng, win)                                        # new period: newest at right
    (x0, x1), _ = vb.viewRange()
    assert x0 == pytest.approx(T0 - 30) and x1 == pytest.approx(T0 + 30)
    win.hide()
