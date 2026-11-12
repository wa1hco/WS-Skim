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


def test_engine_ft8_mode_finds_signal_in_utc_period(tmp_path, monkeypatch):
    from ws_skim import ft8_decode
    from ws_skim.engine import Engine
    monkeypatch.setenv("WS_SKIM_OUTPUT_DIR", str(tmp_path))
    jt9_calls = []

    def fake_jt9(cmd, cwd, timeout_s=0):
        jt9_calls.append(cmd)
        return "000000 -10  0.3 1500 ~  CQ K1ABC FN42\n"

    monkeypatch.setattr(ft8_decode, "run_jt9", fake_jt9)
    spec = cs.FT8
    e = Engine(center_freq_mhz=28.074, sample_rate=int(FS))
    e._ft8_jt9 = "jt9"
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
    # The fake jt9 "decodes" one line per section (each period it runs).
    assert r.decoded and jt9_calls and len(r.decodes) == len(r.sections)
    assert {d.message for d in r.decodes} == {"CQ K1ABC FN42"}
    recs = []
    while not e._decode_queue.empty():
        recs.append(e._decode_queue.get_nowait())
    ft8 = [x for x in recs if x.get("mode") == "FT8"]
    assert ft8 and ft8[0]["message"] == "CQ K1ABC FN42"
    assert (tmp_path / "decodes.jsonl").is_file()


def test_engine_ft8_without_jt9_says_so():
    from ws_skim.engine import Engine
    spec = cs.FT8
    e = Engine(center_freq_mhz=28.074, sample_rate=int(FS))
    e._ft8_jt9 = None
    e.set_blanker("Bypass")
    e.set_mode("FT8")
    rng = np.random.default_rng(6)
    n = int(40 * FS)
    x = cs.synth_noise(FS, n, rng=rng)
    chunk = 8192
    _feed(e, x, chunk, upto=chunk)
    tb = e.timebase
    tb.should_mark_period = lambda wall: False
    p0 = math.ceil(tb.utc_at(0) / spec.period_s) * spec.period_s
    s0 = tb.sample_at(p0)
    x = x + cs.synth_signal(spec, FS, n, freq_hz=-3000.0, snr_db=-8, t0_s=-s0 / FS, rng=rng)
    end = s0 + int((spec.period_s + ft8_engine.TAIL_S + 0.5) * FS)
    for s in range(chunk, end, chunk):
        e.process_iq_data(x[s:s + chunk], 0, 0)
        _drain(e)
    r = e._ft8_result
    assert r is not None and r.sections and not r.decoded
    assert "jt9 not found" in r.decode_note


def test_engine_msk144_mode_never_runs_ft8_search():
    from ws_skim.engine import Engine
    e = Engine(center_freq_mhz=50.260, sample_rate=int(FS))
    e._ft8_jt9 = None
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
    win._ft8_decodes = QtWidgets.QTableWidget(0, 4)
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
    assert "decoding" in win._ft8_status.text()
    from dataclasses import replace
    from ws_skim import ft8_decode
    d = ft8_decode.Decode(mode="FT8", period_start_utc=r.period_start_utc, snr_db=-9, dt_s=0.1,
                          audio_hz=1000.0, rf_hz=28_075_000.0, dial_hz=28_074_000.0,
                          message="CQ K1ABC FN42")
    ft8_window.draw_result(win, replace(r, decodes=[d], decoded=True))
    assert win._ft8_decodes.rowCount() == 1
    assert win._ft8_decodes.item(0, 3).text() == "CQ K1ABC FN42"
    assert "1 decoded" in win._ft8_status.text()
    assert float(win._ft8_table.item(0, 0).text()) == pytest.approx(
        28.074 + r.candidates[0].freq_hz / 1e6, abs=1e-6)


def test_band_list_follows_mode(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    from ws_skim import ui, visualizer
    settings = _FakeSettings({"dial_freq_mhz_FT8": 7.074, "calling_freq_mhz": 50.260})
    monkeypatch.setattr(visualizer, "_SETTINGS", settings)

    class Win:
        mode = "MSK144"

        def __init__(self):
            self._band_combo = QtWidgets.QComboBox()
            for label, _f, _s in ui.MSK144_BANDS:
                self._band_combo.addItem(label)
            self._bands = ui.MSK144_BANDS
            self.picked = []

        def on_band_changed(self, idx):
            self.picked.append(ui._bands(self)[idx][1])

    w = Win()
    w.mode = "FT8"
    ui.set_band_list(w, "FT8")
    labels = [w._band_combo.itemText(i) for i in range(w._band_combo.count())]
    assert labels[0].startswith("160m") and any("7.074" in t for t in labels)
    assert w._band_combo.currentText().startswith("40m") and w.picked == [7.074]
    ui.set_band_list(w, "FT8")                       # same mode: nothing reapplied
    assert w.picked == [7.074]
    w.mode = "MSK144"
    ui.set_band_list(w, "MSK144")
    assert w._band_combo.currentText().startswith("6m") and w.picked[-1] == 50.260
    assert ui.band_setting_key("MSK144") == "calling_freq_mhz"
    assert ui.band_setting_key("FT4") == "dial_freq_mhz_FT4"


# ── dial section, arrival-time anchoring, IQ save ─────────────────────────

def test_dial_section_added_first_when_missing():
    spec = cs.FT8
    dial = ft8_engine.dial_section(-4300.0, FS, spec)
    assert dial.base_hz == -4300.0 and dial.candidates == []
    far = cs.Section(base_hz=9000.0, low_audio_hz=300, high_audio_hz=400, candidates=[])
    assert ft8_engine.with_dial_section([far], dial) == [dial, far]
    near = cs.Section(base_hz=-4200.0, low_audio_hz=300, high_audio_hz=400, candidates=[])
    assert ft8_engine.with_dial_section([near], dial) == [near]


def test_dial_section_none_outside_band():
    spec = cs.FT8
    assert ft8_engine.dial_section(None, FS, spec) is None
    assert ft8_engine.dial_section(0.45 * FS - 1000.0, FS, spec) is None
    assert ft8_engine.dial_section(-0.45 * FS - 300.0, FS, spec) is None


def test_search_period_always_has_dial_section_in_noise():
    rng = np.random.default_rng(11)
    x = cs.synth_noise(FS, int(18 * FS), rng=rng)
    r = ft8_engine.search_period(x, FS, "FT8", t0_s=-2.0, period_start_utc=0.0,
                                 center_freq_mhz=7.0783, dial_offset_hz=-4300.0)
    assert any(s.base_hz == -4300.0 for s in r.sections)


def test_period_anchor_uses_packet_arrival_time():
    """A backlog must not shift sample→UTC: the re-peg uses the chunk's
    arrival wall (``_chunk_rx_wall``), not the time it is processed."""
    import time as _time
    from ws_skim.engine import Engine
    e = Engine(center_freq_mhz=7.0783, sample_rate=int(FS))
    e.set_blanker("Bypass")
    e.set_mode("FT8")
    arrived = _time.time() - 4.0                   # processed 4 s after arrival
    e._chunk_rx_wall = arrived
    e.process_iq_data(np.zeros(1024, np.complex64), 0, 0)
    assert e.timebase.utc_at(0) == pytest.approx(arrived, abs=1e-6)
    assert e._chunk_rx_wall == 0.0                 # consumed: never reused
    before = _time.time()
    e._timebase_anchor_pending = True
    e.process_iq_data(np.zeros(1024, np.complex64), 0, 0)
    assert e.timebase.utc_at(1024) >= before       # no stamp → falls back to now


def test_save_period_iq_round_trip_and_keeps_last(tmp_path):
    iq = (np.arange(10) + 1j).astype(np.complex64)
    for k in range(5):
        ft8_engine.save_period_iq(iq, fs=FS, t0_s=-2.0, period_start_utc=15.0 * k,
                                  mode="FT8", center_freq_mhz=7.0783,
                                  timing={"backlog_s": 0.25}, out_dir=tmp_path, keep=3)
    files = sorted(tmp_path.glob("*.npz"))
    assert len(files) == 3 and files[-1].name == "19700101_000100_FT8.npz"
    z = np.load(files[-1])
    assert np.array_equal(z["iq"], iq) and float(z["t0_s"]) == -2.0
    assert float(z["period_start_utc"]) == 60.0 and float(z["backlog_s"]) == 0.25


def test_save_period_iq_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    ft8_engine.save_period_iq(np.zeros(4, np.complex64), fs=FS, t0_s=0.0,
                              period_start_utc=0.0, mode="FT8", center_freq_mhz=7.0,
                              timing={}, out_dir=blocker / "sub")


def test_vita_packet_carries_arrival_time():
    from flexclient.models import VitaPacket
    p = VitaPacket(stream_id=1, timestamp_int=0, timestamp_frac=0, tsf=1, sequence=0,
                   samples=np.zeros((128, 1), np.complex64))
    assert p.rx_wall == 0.0
