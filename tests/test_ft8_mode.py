# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8 / FT4 mode: period search, engine gating, mode switch, Sync Map draw.

SNR is in the WSJT-X 2.5 kHz reference bandwidth.  Fixed seeds.
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
from ws_skim.timebase import TimeBase  # noqa: E402

FS = 48000.0


# ── pure pieces ──────────────────────────────────────────────────────────

def test_sample_at_inverts_utc_at():
    tb = TimeBase(sample_rate_hz=FS)
    tb.mark_period_start(sample=1000, wall=1_700_000_000.25)
    for s in (0, 1, 48_000, 1_234_567):
        assert tb.sample_at(tb.utc_at(s)) == s


def test_downsample_map_keeps_peaks():
    rng = np.random.default_rng(0)
    sync = rng.random((10, 9000)).astype(np.float32)
    sync[4, 5001] = 50.0
    freqs = np.arange(9000, dtype=float)
    m, mf = ft8_engine.downsample_map(sync, freqs, baseline=2.0, columns=2400)
    assert m.shape == (10, 9000 // math.ceil(9000 / 2400))
    assert len(mf) == m.shape[1]
    assert m.max() == pytest.approx(25.0)
    assert np.all(np.diff(mf) > 0)


def test_downsample_map_empty():
    m, mf = ft8_engine.downsample_map(np.zeros((3, 0)), np.zeros(0), 1.0)
    assert m.shape == (3, 0) and mf.size == 0


@pytest.mark.parametrize("dt", [-0.8, 0.0, 1.2])
def test_search_period_finds_signal_with_preroll(dt):
    spec = cs.FT8
    t0 = -ft8_engine.HEAD_S
    n = int((ft8_engine.HEAD_S + spec.period_s + ft8_engine.TAIL_S) * FS)
    rng = np.random.default_rng(7)
    f = -12000.0
    x = cs.synth_noise(FS, n, rng=rng) + cs.synth_signal(
        spec, FS, n, freq_hz=f, dt_s=dt, snr_db=-12, t0_s=t0, rng=rng)
    r = ft8_engine.search_period(x, FS, "FT8", t0_s=t0, period_start_utc=0.0,
                                 center_freq_mhz=28.0)
    hit = [c for c in r.candidates
           if abs(c.freq_hz - f) <= spec.tone_spacing_hz and abs(c.dt_s - dt) <= spec.symbol_s / 2]
    assert hit, r.candidates[:3]
    assert r.map_score.ndim == 2 and r.map_score.shape[1] <= ft8_engine.DISPLAY_COLUMNS
    assert r.map_score.shape[0] == len(r.map_start_s)
    assert any(hit[0] in s.candidates for s in r.sections)


def test_search_period_noise_only_is_quiet():
    n = int(17 * FS)
    x = cs.synth_noise(FS, n, rng=np.random.default_rng(11))
    r = ft8_engine.search_period(x, FS, "FT8", t0_s=-2.0, period_start_utc=0.0,
                                 center_freq_mhz=28.0)
    assert len(r.candidates) <= 3
    assert 0.5 < float(np.median(r.map_score)) < 2.0       # noise ~ 1 on the display


# ── engine ──────────────────────────────────────────────────────────────

def _drain(e):
    t = getattr(e, "_ft8_thread", None)
    if t is not None:
        t.join(timeout=30)


def _feed(e, x, chunk=8192, upto=None):
    for s in range(0, len(x) if upto is None else upto, chunk):
        e.process_iq_data(x[s:s + chunk], 0, 0)
        _drain(e)


def test_engine_ft8_mode_finds_signal_in_utc_period():
    from ws_skim.engine import Engine
    spec = cs.FT8
    e = Engine(center_freq_mhz=28.074, sample_rate=int(FS))
    e.set_blanker("Bypass")
    e.set_mode("FT8")
    rng = np.random.default_rng(5)
    n = int(40 * FS)
    x = cs.synth_noise(FS, n, rng=rng)
    chunk = 8192
    _feed(e, x, chunk, upto=chunk)                 # anchors the timebase
    tb = e.timebase
    assert tb.is_anchored()
    # Fed faster than real time: keep the first anchor instead of re-pegging
    # it to the wall clock when the real clock crosses a period boundary.
    tb.should_mark_period = lambda wall: False
    p0 = math.ceil(tb.utc_at(0) / spec.period_s) * spec.period_s
    s0 = tb.sample_at(p0)
    f = 6000.0
    x = x + cs.synth_signal(spec, FS, n, freq_hz=f, dt_s=0.3, snr_db=-10,
                            t0_s=-s0 / FS, rng=rng)
    end = s0 + int((spec.period_s + ft8_engine.TAIL_S + 0.5) * FS)
    assert end <= n
    for s in range(chunk, end, chunk):
        e.process_iq_data(x[s:s + chunk], 0, 0)
        _drain(e)
    r = e._ft8_result
    assert r is not None and r.period_start_utc == pytest.approx(p0)
    assert r.center_freq_mhz == pytest.approx(28.074)
    hit = [c for c in r.candidates
           if abs(c.freq_hz - f) <= spec.tone_spacing_hz and abs(c.dt_s - 0.3) <= spec.symbol_s / 2]
    assert hit, r.candidates[:3]
    assert e._ch_buf_end == 0                      # MSK144 hop loop never ran


def test_engine_msk144_mode_never_runs_ft8_search():
    from ws_skim.engine import Engine
    e = Engine(center_freq_mhz=50.260, sample_rate=int(FS))
    e.set_blanker("Bypass")
    x = cs.synth_noise(FS, int(35 * FS), rng=np.random.default_rng(1))
    _feed(e, x)
    assert e._ft8_result is None and e._ft8_thread is None


def test_set_mode_validates_and_resets():
    from ws_skim.engine import Engine
    e = Engine(sample_rate=int(FS))
    with pytest.raises(ValueError):
        e.set_mode("JT65")
    e.set_mode("FT8")
    e._ft8_result = object()
    e._ft8_last_period = 99
    e.set_mode("FT4")
    assert e.mode == "FT4" and e._ft8_result is None and e._ft8_last_period == -1


# ── UI ──────────────────────────────────────────────────────────────────

@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


class _FakeSettings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


def test_apply_mode_switches_blanker_per_mode(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    from ws_skim import ft8_window, visualizer
    settings = _FakeSettings()
    monkeypatch.setattr(visualizer, "_SETTINGS", settings)

    class Win:
        mode = "MSK144"

        def __init__(self):
            self.blanker = None
            self.nb_backend_combo = QtWidgets.QComboBox()
            self.nb_backend_combo.addItems(["Linrad", "Bypass"])
            self.nb_backend_combo.currentTextChanged.connect(self.on_changed)

        def set_mode(self, m):
            self.mode = m

        def set_blanker(self, b):
            self.blanker = b

        def on_changed(self, b):
            self.set_blanker(b)
            settings.setValue(ft8_window.blanker_key(self.mode), b)

    w = Win()
    ft8_window.apply_mode(w, "FT8")
    assert (w.mode, w.blanker, settings["mode"]) == ("FT8", "Bypass", "FT8")
    ft8_window.apply_mode(w, "MSK144")
    assert (w.mode, w.blanker) == ("MSK144", "Linrad")
    w.nb_backend_combo.setCurrentText("Bypass")            # operator choice, MSK144
    ft8_window.apply_mode(w, "FT8")
    ft8_window.apply_mode(w, "MSK144")
    assert w.blanker == "Bypass"                            # remembered per mode
    assert ft8_window.blanker_key("MSK144") == "nb_backend"


def test_draw_result_smoke(qapp):
    from PyQt5 import QtWidgets
    from ws_skim import ft8_window
    import pyqtgraph as pg

    win = QtWidgets.QWidget()
    win._ft8_plot = pg.PlotWidget()
    win._ft8_img = pg.ImageItem()
    win._ft8_scatter = pg.ScatterPlotItem()
    win._ft8_table = QtWidgets.QTableWidget(0, 4)
    win._ft8_status = QtWidgets.QLabel()
    win._ft8_regions = []
    n = int(17 * FS)
    rng = np.random.default_rng(2)
    x = cs.synth_noise(FS, n, rng=rng) + cs.synth_signal(
        cs.FT8, FS, n, freq_hz=1000.0, snr_db=-8, t0_s=-2.0, rng=rng)
    r = ft8_engine.search_period(x, FS, "FT8", t0_s=-2.0, period_start_utc=1_700_000_010.0,
                                 center_freq_mhz=28.074, data_fraction=0.8)
    ft8_window.draw_result(win, r)
    ft8_window.draw_result(win, r)                          # redraw replaces regions
    assert win._ft8_table.rowCount() == len(r.candidates) >= 1
    assert len(win._ft8_regions) == len(r.sections)
    assert "80% of period" in win._ft8_status.text()
    assert float(win._ft8_table.item(0, 0).text()) == pytest.approx(
        28.074 + r.candidates[0].freq_hz / 1e6, abs=1e-6)
