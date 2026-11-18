# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Optional classic scrolling IQ waterfall (View → Waterfall).

A separate window from the FT8 Sync Map.  It reuses the overlap-FFT
already computed for Fast Graph when either window is visible, and
keeps a short scrolling history (newest at the top).  Nothing is
computed for this window while it is hidden.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

from .plot_style import style_plot
from .ui import _PanelWindow

#: Rows in the scrolling image (~25 s at :data:`ROW_RATE_HZ`).
WF_ROWS = 200
#: Target number of new rows per second (the overlap-FFT is faster; we keep one).
ROW_RATE_HZ = 8.0
#: dB floor used to fill unused rows.
WF_FILL_DB = -130.0
_DEFAULT_GEO = QtCore.QRect(500, 80, 900, 520)
_TIMER_MS = 150


def make_state(n_bins: int, n_rows: int = WF_ROWS) -> dict:
    """A scrolling spectrogram buffer.  Row 0 is the newest."""
    return {
        "buf": np.full((n_rows, n_bins), WF_FILL_DB, dtype=np.float32),
        "last_t": None,
        "dirty": False,
        "n": 0,
    }


def push_row(state: dict, power_db, t: float, min_dt: float = 1.0 / ROW_RATE_HZ) -> bool:
    """Insert one spectrum row if enough time has passed.  Returns True if kept."""
    last = state.get("last_t")
    if last is not None and (t - last) < min_dt:
        return False
    row = np.asarray(power_db, dtype=np.float32).reshape(-1)
    buf = state["buf"]
    if row.shape[0] != buf.shape[1]:
        state["buf"] = np.full((buf.shape[0], row.shape[0]), WF_FILL_DB, dtype=np.float32)
        buf = state["buf"]
        state["n"] = 0
    buf[1:] = buf[:-1]
    buf[0] = row[: buf.shape[1]]
    state["last_t"] = float(t)
    state["dirty"] = True
    state["n"] = min(int(state.get("n", 0)) + 1, buf.shape[0])
    return True


def push_row_from_engine(eng, power_db, t: float) -> bool:
    """Append a row to the engine's waterfall state (radio thread, visible only)."""
    state = getattr(eng, "_waterfall_state", None)
    n_bins = int(getattr(eng, "fft_size", len(np.asarray(power_db))))
    if state is None or state["buf"].shape[1] != n_bins:
        state = make_state(n_bins)
        eng._waterfall_state = state
    return push_row(state, power_db, t)


def freq_axis_mhz(eng) -> np.ndarray:
    """RF frequency of each FFT bin, matching the fftshifted overlap-FFT."""
    n = int(getattr(eng, "fft_size", 2048))
    fs = float(getattr(eng, "sample_rate", 48000.0))
    center = float(getattr(eng, "center_freq_mhz", 0.0))
    return center + np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs)) / 1e6


class _WaterfallPanel(_PanelWindow):
    """Panel window that tells the engine when the waterfall is shown."""

    def showEvent(self, event):
        super().showEvent(event)
        cb = getattr(self, "_on_vis", None)
        if cb is not None:
            cb(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        cb = getattr(self, "_on_vis", None)
        if cb is not None:
            cb(False)


def setup_waterfall(self, view_menu: QtWidgets.QMenu) -> None:
    """Add View → Waterfall (off by default) and build the panel."""
    from .visualizer import _SETTINGS

    view_action = QtWidgets.QAction("Waterfall", self)
    view_action.setCheckable(True)
    view_action.setChecked(False)
    # After setup_ft8, FT8 Sync Map is the first View item; put Waterfall next.
    acts = view_menu.actions()
    if len(acts) >= 2:
        view_menu.insertAction(acts[1], view_action)
    else:
        view_menu.addAction(view_action)

    win = _WaterfallPanel("WS Skim — Waterfall", view_action, self, "waterfall_geometry")
    win.setMinimumSize(420, 280)
    geo = _SETTINGS.value("waterfall_geometry")
    if geo:
        win.restoreGeometry(geo)
    else:
        win.setGeometry(_DEFAULT_GEO)

    layout = QtWidgets.QVBoxLayout(win)
    layout.setContentsMargins(4, 4, 4, 4)
    status = QtWidgets.QLabel("Open View → Waterfall while a source is running.")
    layout.addWidget(status)

    plot = pg.PlotWidget()
    plot.setBackground("w")
    plot.setLabel("bottom", "Frequency (MHz)")
    plot.setLabel("left", "Time (s ago)")
    style_plot(plot)
    for name in ("bottom", "left"):
        plot.getAxis(name).setPen("k")
        plot.getAxis(name).setTextPen("k")
    plot.showGrid(x=True, y=False, alpha=0.15)
    plot.getViewBox().disableAutoRange()
    plot.getViewBox().invertY(True)        # 0 (newest) at the top
    img = pg.ImageItem()
    img.setColorMap(pg.colormap.get("viridis"))
    plot.addItem(img)
    layout.addWidget(plot, stretch=1)

    win._wf_status = status
    win._wf_plot = plot
    win._wf_img = img
    win._wf_axis_key = None
    self._waterfall_win = win
    self._waterfall_wanted = False

    def _toggle(checked):
        self._waterfall_wanted = bool(checked)
        if checked:
            win.show()
        else:
            win.hide()

    view_action.triggered.connect(_toggle)

    timer = QtCore.QTimer(win)
    timer.setInterval(_TIMER_MS)
    timer.timeout.connect(lambda: tick(self, win))
    win._wf_timer = timer
    # Stay off until the window is shown.

    def _on_vis(visible):
        self._waterfall_wanted = bool(visible)
        if visible:
            if not timer.isActive():
                timer.start()
        else:
            timer.stop()

    win._on_vis = _on_vis

    visible = _SETTINGS.value("waterfall_visible", False, type=bool)
    if visible:
        view_action.setChecked(True)
        self._waterfall_wanted = True
        win.show()
        timer.start()
    else:
        win.hide()


def render_image(win, eng) -> None:
    """Copy the scrolling buffer onto the image item and lock the axes."""
    state = getattr(eng, "_waterfall_state", None)
    if state is None or state.get("n", 0) == 0:
        win._wf_status.setText("Waiting for spectrum…")
        return
    buf = state["buf"]
    freqs = freq_axis_mhz(eng)
    if freqs.size != buf.shape[1]:
        freqs = freq_axis_mhz(eng)
        if freqs.size != buf.shape[1]:
            # Engine FFT size changed; wait for a fresh buffer.
            return
    df = (float(freqs[-1]) - float(freqs[0])) / max(1, len(freqs) - 1)
    span_s = buf.shape[0] / ROW_RATE_HZ
    levels = (
        float(getattr(eng, "min_level", -90)),
        float(getattr(eng, "max_level", -30)),
    )
    # ImageItem: first index = x (frequency), second = y (time).  Column 0
    # is the newest row, at y = 0, after invertY.
    win._wf_img.setImage(np.clip(buf.T, levels[0], levels[1]), autoLevels=False)
    win._wf_img.setLevels(levels)
    win._wf_img.setRect(QtCore.QRectF(
        float(freqs[0]) - df / 2, 0.0, df * len(freqs), span_s))
    key = (round(float(getattr(eng, "center_freq_mhz", 0.0)), 9),
           int(buf.shape[1]), int(buf.shape[0]),
           round(float(freqs[0]), 9), round(float(freqs[-1]), 9))
    if getattr(win, "_wf_axis_key", None) != key:
        win._wf_plot.setRange(
            xRange=(float(freqs[0]) - df / 2, float(freqs[-1]) + df / 2),
            yRange=(0.0, span_s), padding=0)
        win._wf_plot.getViewBox().disableAutoRange()
        win._wf_axis_key = key
    n = int(state.get("n", 0))
    win._wf_status.setText(
        f"Waterfall · {n} rows · newest at the top · {span_s:.0f} s span")
    state["dirty"] = False


def tick(eng, win) -> None:
    """GUI-timer update: paint when visible and the buffer has new rows."""
    if not win.isVisible():
        eng._waterfall_wanted = False
        return
    eng._waterfall_wanted = True
    state = getattr(eng, "_waterfall_state", None)
    if state is None:
        win._wf_status.setText("Waiting for spectrum…")
        return
    if not state.get("dirty") and state.get("n", 0) > 0:
        return
    try:
        render_image(win, eng)
    except Exception as e:
        win._wf_status.setText(f"redraw error: {e}")
