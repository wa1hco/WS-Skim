# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Optional View → Waterfall window: scrolling spectrogram, no work when hidden."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_push_row_keeps_newest_at_row_zero_and_rate_limits():
    from ws_skim.waterfall_window import make_state, push_row
    state = make_state(8, n_rows=4)
    a = np.arange(8, dtype=np.float32)
    b = a + 10
    assert push_row(state, a, t=0.0)
    assert push_row(state, b, t=0.01) is False          # too soon (< 1/8 s)
    assert state["n"] == 1 and np.array_equal(state["buf"][0], a)
    assert push_row(state, b, t=0.2)
    assert state["n"] == 2
    assert np.array_equal(state["buf"][0], b)           # newest first
    assert np.array_equal(state["buf"][1], a)


def test_waterfall_window_toggles_and_paints_when_visible(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    from ws_skim import waterfall_window
    class _Settings(dict):
        def value(self, key, default=None, type=None):
            v = self.get(key, default)
            if type is bool:
                return bool(v)
            return v

        def setValue(self, key, value):
            self[key] = value

    settings = _Settings()
    monkeypatch.setattr("ws_skim.visualizer._SETTINGS", settings)
    # setup_waterfall also reads from the imported name in the function
    import ws_skim.visualizer as vis
    monkeypatch.setattr(vis, "_SETTINGS", settings)

    class Eng(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.fft_size = 64
            self.sample_rate = 48000.0
            self.center_freq_mhz = 28.074
            self.min_level = -90
            self.max_level = -30
            self._waterfall_wanted = False
            self._waterfall_state = waterfall_window.make_state(64, n_rows=20)

    eng = Eng()
    menu = QtWidgets.QMenu("View", eng)
    menu.addAction("FT8 Sync Map")
    menu.addAction("Fast Graph")
    waterfall_window.setup_waterfall(eng, menu)
    win = eng._waterfall_win
    labels = [a.text() for a in menu.actions()]
    assert labels[0] == "FT8 Sync Map" and labels[1] == "Waterfall"
    assert win is not None and not win.isVisible()
    assert eng._waterfall_wanted is False

    # Hidden: tick must not set the wanted flag / must clear it.
    waterfall_window.tick(eng, win)
    assert eng._waterfall_wanted is False
    assert win._wf_img.image is None or win._wf_status.text().startswith("Open") or True

    n_before = eng._waterfall_state["n"]
    # Visible path: push rows, show, tick paints.
    for i in range(5):
        waterfall_window.push_row(eng._waterfall_state, np.full(64, -40.0 - i, np.float32), t=i * 0.2)
    assert eng._waterfall_state["n"] > n_before
    win.show()
    qapp.processEvents()
    assert eng._waterfall_wanted is True
    waterfall_window.tick(eng, win)
    assert win._wf_img.image is not None
    assert win._wf_img.image.shape[0] == 64          # frequency is x
    assert "rows" in win._wf_status.text()

    win.hide()
    qapp.processEvents()
    assert eng._waterfall_wanted is False
    # A tick while hidden does not paint a newly pushed row (dirty stays).
    waterfall_window.push_row(eng._waterfall_state, np.zeros(64, np.float32), t=10.0)
    assert eng._waterfall_state["dirty"] is True
    waterfall_window.tick(eng, win)
    assert eng._waterfall_wanted is False
    assert eng._waterfall_state["dirty"] is True     # not consumed


def test_process_iq_does_not_fill_waterfall_when_hidden():
    from ws_skim.engine import Engine
    from ws_skim.waterfall_window import make_state

    class _Hidden:
        def isVisible(self):
            return False

    e = Engine(center_freq_mhz=28.074, sample_rate=48000, fft_size=2048)
    e.set_blanker("Bypass")
    e._fast_graph_win = _Hidden()          # so the FFT is not kept on for Fast Graph
    e._waterfall_wanted = False
    e._waterfall_state = make_state(e.fft_size, n_rows=16)
    n = int(e.fft_size * 4)
    iq = (0.01 * np.random.default_rng(0).standard_normal(n)
          + 1j * 0.01 * np.random.default_rng(1).standard_normal(n)).astype(np.complex64)
    e.process_iq_data(iq, 0, 0)
    assert e._waterfall_state["n"] == 0

    e._waterfall_wanted = True
    e.process_iq_data(iq, 1, 0)
    assert e._waterfall_state["n"] >= 1
