# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Band Activity view: waterfall buffer, detection markers, check boxes."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ws_skim import band_activity_window as ba  # noqa: E402
from ws_skim.ft8_decode import Decode  # noqa: E402

CENTER = 14.074
FS = 48000.0
T0 = 1_700_000_010.0          # an FT8 period boundary (divisible by 15)


class _Settings(dict):
    def value(self, key, default=None, type=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@dataclass
class _Res:
    mode: str
    period_start_utc: float
    decodes: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    decoded: bool = True
    center_freq_mhz: float = CENTER


@dataclass
class _Rejection:
    decode: Decode
    reason: str = "ap"
    detail: str = ""


def _dec(start, rf_hz, snr, msg="CQ K1ABC FN42", mode="FT8"):
    return Decode(mode=mode, period_start_utc=start, snr_db=snr, dt_s=0.2,
                  audio_hz=1000.0, rf_hz=rf_hz, dial_hz=rf_hz - 1000.0, message=msg)


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


_WINS = []


@pytest.fixture(autouse=True)
def _stop_timers():
    yield
    while _WINS:
        win = _WINS.pop()
        win._ba_timer.stop()
        win._parent_win = None
        win.hide()


def _window(qapp, monkeypatch, settings=None, **eng_attrs):
    from PyQt5 import QtWidgets
    import ws_skim.visualizer as vis
    settings = _Settings() if settings is None else settings
    monkeypatch.setattr(vis, "_SETTINGS", settings)

    class Eng(QtWidgets.QMainWindow):
        mode = "FT8"
        period_modes = ("FT8", "FT4")
        sample_rate = FS
        center_freq_mhz = CENTER
        source_mode = "radio"
        _ft8_result = None

    eng = Eng()
    for k, v in eng_attrs.items():
        setattr(eng, k, v)
    win = ba.build_window(eng, QtWidgets.QAction("Band Activity", eng, checkable=True))
    _WINS.append(win)
    return eng, win, settings


def _fill_waterfall(wf, t_start, seconds, tones_hz=(), n=2048):
    """Noise plus steady tones (IQ offsets from CENTER), pushed like the engine."""
    rng = np.random.default_rng(1)
    hop = (n // 2) / FS
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / FS))
    t = t_start
    while t < t_start + seconds:
        mag = rng.rayleigh(1e-5, n).astype(np.float32)
        for f in tones_hz:
            mag[np.argmin(np.abs(freqs - f))] += 1e-3
        wf.push(mag, t, CENTER, FS)
        t += hop


# ── Buffer and model ───────────────────────────────────────────────────────

def test_waterfall_reduces_columns_and_rows_and_scrolls():
    wf = ba.BandWaterfall(span_s=60.0, max_columns=512)
    assert wf.row_s == ba.MIN_ROW_S and wf.n_rows == 120
    assert wf.snapshot() is None
    _fill_waterfall(wf, T0, 90.0, tones_hz=(3000.0,))
    rows, t0, t1, f0, f1 = wf.snapshot()
    assert rows.shape == (120, 512)                 # 2048 bins → 512 columns, 60 s
    assert t1 - t0 == pytest.approx(60.0)
    assert t1 == pytest.approx(T0 + 90.0, abs=1.0)
    assert f0 == pytest.approx(CENTER - FS / 2e6, abs=1e-4)
    assert f1 == pytest.approx(CENTER + FS / 2e6, abs=1e-4)
    col = int(round((CENTER + 3000e-6 - f0) / (f1 - f0) * 512 - 0.5))
    assert rows[:, col].mean() > rows.mean() + 20          # the tone stands out
    lo, hi = ba.color_levels(rows)
    assert hi - lo == ba.LEVEL_RANGE_DB and lo < np.median(rows) < hi
    # A retune starts over.
    wf.push(np.ones(2048, np.float32), T0 + 91, 7.074, FS)
    assert wf.snapshot() is None


def test_waterfall_gap_leaves_blank_rows():
    wf = ba.BandWaterfall(span_s=60.0)
    _fill_waterfall(wf, T0, 5.0)
    _fill_waterfall(wf, T0 + 15.0, 5.0)              # 10 s with no radio data
    rows, t0, t1, *_ = wf.snapshot()
    assert t1 == pytest.approx(T0 + 20.0, abs=1.0)
    blank = (rows <= ba.FILL_DB + 1).all(axis=1)
    assert 15 <= blank.sum() <= 22                    # ~10 s of 0.5 s rows


def test_markers_placed_at_frequency_and_period():
    d = ba.detection_from_decode(_dec(T0 + 0.4, 14_075_000.0, -7))
    assert d.mode == "FT8"
    assert d.f_lo_mhz == pytest.approx(14.075)
    assert d.f_hi_mhz == pytest.approx(14.075 + 50e-6)
    assert T0 < d.t0 < d.t1 < T0 + 15 and d.when == T0
    d4 = ba.detection_from_decode(_dec(T0 + 7.6, 14_081_000.0, -3, mode="FT4"))
    assert T0 + 7.5 < d4.t0 < d4.t1 < T0 + 15
    rec = {"outcome": "decoded", "decoded": True, "mode": "MSK144",
           "message": "W1AW K2AA FN20", "radio_khz": 144_151.5, "jt9_snr": 3,
           "utc_time": "22:13:30", "t_sec": 4.25}
    now = 1_700_000_000.0 + 3600                     # 2023-11-14 23:13:20 UTC
    m = ba.detection_from_record(rec, now=now)
    assert m.mode == "MSK144" and m.call == "K2AA" and m.grid == "FN20"
    assert m.f_lo_mhz < 144.1515 < m.f_hi_mhz
    assert m.when % 60 == pytest.approx(34.25)       # 22:13:30 + 4.25 s
    assert now - 3700 < m.when < now
    # FT8 records on the decode queue are not doubled (they come from results).
    assert ba.detection_from_record(dict(rec, mode="FT8"), now=now) is None
    assert ba.detection_from_record(dict(rec, outcome="no_decode", decoded=False)) is None


def test_tooltip_text():
    d = ba.detection_from_decode(_dec(T0, 14_075_123.0, -12, "W1AW K9XYZ EN52"))
    tip = d.tooltip()
    for part in ("K9XYZ", "EN52", "FT8", "-12 dB", "22:13:30 UTC", "14.075123 MHz",
                 "W1AW K9XYZ EN52"):
        assert part in tip, part
    nogrid = ba.detection_from_decode(_dec(T0, 14_075_000.0, 0, "W1AW K9XYZ RR73"))
    assert "no grid" in nogrid.tooltip()


def test_mode_colors_distinct_and_legend():
    cols = [ba.mode_color(m) for m in ba.MODE_ORDER]
    assert len(set(cols)) == 3
    assert ba.mode_color("FT8") == ba.MODE_COLORS["FT8"]
    html = ba.legend_html()
    for m in ba.MODE_ORDER:
        r, g, b = ba.MODE_COLORS[m]
        assert m in html and f"rgb({r},{g},{b})" in html
    item = ba.MarkersItem()
    dets = [ba.detection_from_decode(_dec(T0, 14_075_000.0, 0, mode=m))
            for m in ("FT8", "FT4")]
    item.set_detections(dets, translucent=False)
    c8, c4 = (item.brush_color(d) for d in dets)
    assert (c8.red(), c8.green(), c8.blue()) == ba.MODE_COLORS["FT8"]
    assert (c4.red(), c4.green(), c4.blue()) == ba.MODE_COLORS["FT4"]
    assert c8.alpha() > 200
    item.set_detections(dets, translucent=True)       # over the waterfall
    assert item.brush_color(dets[0]).alpha() < 200


def test_rejected_decodes_never_stored():
    store = ba.DetectionStore(span_s=120.0)
    kept = _dec(T0, 14_075_000.0, -5, "CQ K1ABC FN42")
    junk = _dec(T0, 14_076_000.0, -20, "K1ABC W9XYZ AA00")
    res = _Res("FT8", T0, decodes=[kept], rejected=[_Rejection(junk)])
    assert store.add_period_result(res)
    assert [d.call for d in store.items] == ["K1ABC"]
    assert not store.add_period_result(res)                # same period again
    assert store.nearest(14.076, T0 + 7, 0.0002, 1.0) is None
    # Not-yet-decoded periods add nothing.
    assert not store.add_period_result(_Res("FT8", T0 + 15, decoded=False))


def test_store_trims_to_span_and_subbands():
    store = ba.DetectionStore(span_s=60.0)
    for k in range(8):
        store.add_period_result(_Res("FT8", T0 + 15 * k,
                                     decodes=[_dec(T0 + 15 * k, 14_075_000.0, 0)]))
    store.trim()
    assert len(store.items) == 4 + 1                  # t1 within the last 60 s
    bands = ba.subbands(CENTER - 0.024, CENTER + 0.024)
    assert ("FT8", 14.0742, 14.077, 14.074) in [
        (m, round(lo, 6), round(hi, 6), d) for m, lo, hi, d in bands]
    assert any(m == "FT4" and d == 14.080 for m, _lo, _hi, d in bands)
    assert ba.minutes_from_periods(8) == 2 and ba.minutes_from_periods(1) == 1
    assert ba.clamp_minutes(999) == ba.MAX_MINUTES


# ── Window ─────────────────────────────────────────────────────────────────

def test_window_checkboxes_toggle_layers_and_are_remembered(qapp, monkeypatch):
    settings = _Settings(activity_periods=16)            # old FT8 Activity setting
    eng, win, settings = _window(qapp, monkeypatch, settings)
    assert win._ba_spin.value() == 4                     # 16 FT8 periods = 4 min
    assert win._ba_wf_cb.isChecked() and win._ba_det_cb.isChecked()
    assert not eng._band_wf.wanted                       # hidden: no FFT work
    win.show()
    assert eng._band_wf.wanted
    _fill_waterfall(eng._band_wf, T0, 30.0)
    eng._ft8_results = {"FT8": _Res("FT8", T0, decodes=[_dec(T0, 14_075_000.0, -5)])}
    ba.tick(eng, win, now=T0 + 30)
    assert win._ba_img.isVisible() and win._ba_img.image is not None
    assert len(win._ba_markers.detections()) == 1
    (x0, x1), (y0, y1) = win._ba_plot.getViewBox().viewRange()
    assert y1 == pytest.approx(T0 + 30) and y1 - y0 == pytest.approx(240.0)
    assert x0 < 14.075 < x1
    eng.source_mode = "wav"        # redraws below follow the data's clock, not now
    # Waterfall off: image hidden, engine stops computing, markers solid.
    win._ba_wf_cb.setChecked(False)
    assert settings[ba.WATERFALL_KEY] is False and not eng._band_wf.wanted
    assert not win._ba_img.isVisible()
    assert not win._ba_markers.translucent
    # Detections off too: nothing drawn.
    win._ba_det_cb.setChecked(False)
    assert settings[ba.DETECTIONS_KEY] is False
    assert win._ba_markers.detections() == [] and not win._ba_markers.isVisible()
    assert "Tick Waterfall or Detections" in win._ba_status.text()
    win._ba_det_cb.setChecked(True)
    assert len(win._ba_markers.detections()) == 1
    win._ba_spin.setValue(3)
    assert settings[ba.MINUTES_KEY] == 3
    win.hide()
    assert not eng._band_wf.wanted
    # Settings come back on the next start.
    eng2, win2, _ = _window(qapp, monkeypatch, settings)
    assert not win2._ba_wf_cb.isChecked() and win2._ba_det_cb.isChecked()
    assert win2._ba_spin.value() == 3


def test_window_all_modes_and_hover_tooltip(qapp, monkeypatch):
    eng, win, _ = _window(qapp, monkeypatch)
    win._ba_wf_cb.setChecked(False)
    win.show()
    eng._ft8_results = {
        "FT8": _Res("FT8", T0, decodes=[_dec(T0, 14_075_000.0, -5, "CQ K1ABC FN42")],
                    rejected=[_Rejection(_dec(T0, 14_078_000.0, -24, "K1ABC W9XYZ AA00"))]),
        "FT4": _Res("FT4", T0 + 7.5, decodes=[_dec(T0 + 7.5, 14_081_000.0, -9,
                                                   "CQ W1AW FN31", mode="FT4")]),
    }
    ba.note_decode_record(win, {"outcome": "decoded", "decoded": True, "mode": "MSK144",
                                "message": "CQ VE3XX FN03", "radio_khz": 14_090.0,
                                "jt9_snr": 1, "timestamp": "2023-11-14_22:13:30",
                                "t_sec": 3.0})
    ba.tick(eng, win, now=T0 + 20)
    modes = sorted(d.mode for d in win._ba_markers.detections())
    assert modes == ["FT4", "FT8", "MSK144"]
    assert "FT8 1 · FT4 1 · MSK144 1" in win._ba_status.text()
    tip = ba.tooltip_at(win, 14.075_02, T0 + 7, 1e-6, 0.1)
    assert tip is not None and "K1ABC" in tip and "FN42" in tip and "FT8" in tip
    assert ba.tooltip_at(win, 14.078_02, T0 + 7, 1e-6, 0.1) is None    # rejected
    tip4 = ba.tooltip_at(win, 14.081_04, T0 + 11, 1e-6, 0.1)
    assert "W1AW" in tip4 and "FT4" in tip4
    assert "sub-band" in ba.tooltip_at(win, 14.0765, T0 - 60, 1e-6, 0.1)
    win._ba_det_cb.setChecked(False)
    assert ba.tooltip_at(win, 14.075_02, T0 + 7, 1e-6, 0.1).startswith("FT8 sub-band")


def test_geometry_migrates_from_old_window(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    w = QtWidgets.QWidget()
    w.setGeometry(123, 77, 700, 500)
    old = w.saveGeometry()
    eng, win, _ = _window(qapp, monkeypatch, _Settings(activity_geometry=old))
    assert win.width() == 700 and win.height() == 500


def test_engine_feed_skipped_when_not_wanted():
    class Eng:
        center_freq_mhz = CENTER
        sample_rate = FS
    eng = Eng()
    ba.push_spectrum(eng, np.ones(2048), T0)            # no window yet: no error
    eng._band_wf = ba.BandWaterfall(60.0)
    for k in range(5):
        ba.push_spectrum(eng, np.ones(2048, np.float32), T0 + 0.3 * k)
    rows, *_ = eng._band_wf.snapshot()
    assert rows.shape[1] == 1024
