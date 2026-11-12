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
"""Mode menu (MSK144 / FT8 / FT4) and the FT8 Sync Map window.

The window draws ``engine._ft8_result`` (``ft8_engine.PeriodResult``) once
per period: the Costas sync score over RF frequency (x) and signal start
time (y), each candidate circled, and the ~2.8 kHz sections that will go to
jt9 shaded.  A table lists the candidates, strongest first.
"""
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

from . import ft8_engine

#: Score shown at the top of the colour scale (noise is ~1).
SCORE_MAX = 6.0
#: Blanker per mode when the operator has not picked one: FT8/FT4 bands are
#: full of steady carriers that pull the impulse threshold around.
DEFAULT_BLANKER = {"MSK144": "Linrad", "FT8": "Bypass", "FT4": "Bypass"}
#: MSK144-only panels, hidden when switching into a period mode.
_MSK144_WINDOWS = ("_fast_graph_win", "_detect_win", "_sync_detect_win", "_cand_map_win")


def blanker_key(mode: str) -> str:
    """Settings key for the blanker choice in ``mode`` (MSK144 keeps the old key)."""
    return "nb_backend" if mode == "MSK144" else f"nb_backend_{mode}"


def setup_ft8(self, menu_bar: QtWidgets.QMenuBar, view_menu: QtWidgets.QMenu) -> None:
    """Add the Mode menu and View → FT8 Sync Map, then restore the saved mode."""
    from .visualizer import _SETTINGS

    view_action = QtWidgets.QAction("FT8 Sync Map", self)
    view_action.setCheckable(True)
    view_menu.addAction(view_action)
    _build_window(self, view_action)
    view_action.triggered.connect(
        lambda checked: self._ft8_win.show() if checked else self._ft8_win.hide())

    mode_menu = QtWidgets.QMenu("&Mode", self)
    menu_bar.insertMenu(view_menu.menuAction(), mode_menu)
    group = QtWidgets.QActionGroup(self)
    group.setExclusive(True)
    self._mode_actions = {}
    for mode in ft8_engine.MODES:
        act = QtWidgets.QAction(mode, self, checkable=True)
        act.triggered.connect(lambda _c, m=mode: apply_mode(self, m))
        group.addAction(act)
        mode_menu.addAction(act)
        self._mode_actions[mode] = act

    saved = str(_SETTINGS.value("mode", ft8_engine.DEFAULT_MODE))
    if saved not in ft8_engine.MODES:
        saved = ft8_engine.DEFAULT_MODE
    apply_mode(self, saved, startup=True)


def apply_mode(self, mode: str, startup: bool = False) -> None:
    """Switch the engine, the blanker and the visible panels to ``mode``."""
    from .visualizer import _SETTINGS

    self.set_mode(mode)
    _SETTINGS.setValue("mode", mode)
    act = getattr(self, "_mode_actions", {}).get(mode)
    if act is not None and not act.isChecked():
        act.setChecked(True)

    combo = getattr(self, "nb_backend_combo", None)
    want = str(_SETTINGS.value(blanker_key(mode), DEFAULT_BLANKER.get(mode, "Linrad")))
    if combo is not None and combo.findText(want) >= 0:
        if combo.currentText() == want:
            self.set_blanker(want)
        else:
            combo.setCurrentText(want)        # on_nb_backend_changed applies + saves
    elif combo is None:
        self.set_blanker(want)

    win = getattr(self, "_ft8_win", None)
    if ft8_engine.is_period_mode(mode):
        for name in _MSK144_WINDOWS:
            w = getattr(self, name, None)
            if w is not None and w.isVisible():
                w.hide()
        if win is not None:
            win.setWindowTitle(f"WS Skim — {mode} Sync Map")
            win.show()
    elif win is not None and not startup:
        win.hide()


# ── Window ─────────────────────────────────────────────────────────────────

def _build_window(self, view_action) -> None:
    from .ui import _PanelWindow
    from .visualizer import _SETTINGS

    win = _PanelWindow("WS Skim — FT8 Sync Map", view_action, self, "ft8_geometry")
    win.setMinimumSize(520, 420)
    geo = _SETTINGS.value("ft8_geometry")
    if geo:
        win.restoreGeometry(geo)
    else:
        win.setGeometry(QtCore.QRect(480, 50, 900, 640))
    layout = QtWidgets.QVBoxLayout(win)
    layout.setContentsMargins(4, 4, 4, 4)

    status = QtWidgets.QLabel("Waiting for the first full period…")
    layout.addWidget(status)

    plot = pg.PlotWidget()
    plot.setLabel("bottom", "Frequency (MHz)")
    plot.setLabel("left", "Start time in period (s)")
    plot.showGrid(x=True, y=True, alpha=0.2)
    img = pg.ImageItem()
    img.setColorMap(pg.colormap.get("viridis"))
    img.setLevels((1.0, SCORE_MAX))
    plot.addItem(img)
    scatter = pg.ScatterPlotItem(size=12, symbol="o", brush=None,
                                 pen=pg.mkPen("w", width=1.5))
    plot.addItem(scatter)

    table = QtWidgets.QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Frequency (MHz)", "DT (s)", "Sync", "Section"])
    table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

    decodes = QtWidgets.QTableWidget(0, 4)
    decodes.setHorizontalHeaderLabels(["Frequency (MHz)", "SNR (dB)", "DT (s)", "Message"])
    decodes.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
    decodes.horizontalHeader().setStretchLastSection(True)
    decodes.verticalHeader().setVisible(False)
    decodes.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

    tabs = QtWidgets.QTabWidget()
    tabs.addTab(decodes, "Decodes")
    tabs.addTab(table, "Candidates")

    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.addWidget(plot)
    split.addWidget(tabs)
    split.setSizes([400, 200])
    layout.addWidget(split, stretch=1)

    win._ft8_status = status
    win._ft8_plot = plot
    win._ft8_img = img
    win._ft8_scatter = scatter
    win._ft8_table = table
    win._ft8_decodes = decodes
    win._ft8_regions = []
    win._ft8_drawn = None
    self._ft8_win = win
    win.hide()

    timer = QtCore.QTimer(win)
    timer.setInterval(1000)
    timer.timeout.connect(lambda: _tick(self, win))
    timer.start()
    win._ft8_timer = timer


def _tick(eng, win) -> None:
    try:
        res = getattr(eng, "_ft8_result", None)
        if not ft8_engine.is_period_mode(getattr(eng, "mode", "MSK144")):
            win._ft8_status.setText("Mode is MSK144. Choose Mode → FT8 to search for FT8.")
            return
        if res is None:
            win._ft8_status.setText("Waiting for the first full period…")
            return
        if res is win._ft8_drawn or not win.isVisible():
            return
        draw_result(win, res)
        win._ft8_drawn = res
    except Exception as e:                       # never let a bad frame kill the panel
        win._ft8_status.setText(f"redraw error: {e}")


def rf_mhz(res, offset_hz) -> np.ndarray:
    return res.center_freq_mhz + np.asarray(offset_hz, dtype=float) / 1e6


def draw_result(win, res) -> None:
    """Redraw the window for one ``PeriodResult``."""
    from .costas_search import MODES
    spec = MODES[res.mode]
    m = res.map_score
    if m.size:
        f = rf_mhz(res, res.map_freqs_hz)
        t = res.map_start_s
        df = (f[-1] - f[0]) / max(1, len(f) - 1)
        dt = (t[-1] - t[0]) / max(1, len(t) - 1) if len(t) > 1 else 1.0
        # ImageItem axes: first index = x (frequency), second = y (time).
        win._ft8_img.setImage(np.clip(m.T, 0.0, SCORE_MAX), autoLevels=False)
        win._ft8_img.setRect(QtCore.QRectF(f[0] - df / 2, t[0] - dt / 2,
                                           df * len(f), dt * len(t)))

    half_bw = spec.bandwidth_hz / 2
    xs = rf_mhz(res, [c.freq_hz + half_bw for c in res.candidates])
    ys = [c.dt_s + spec.nominal_start_s for c in res.candidates]
    win._ft8_scatter.setData(xs, ys)

    for r in win._ft8_regions:
        win._ft8_plot.removeItem(r)
    win._ft8_regions = []
    section_of = {}
    for n, sec in enumerate(res.sections, 1):
        lo = rf_mhz(res, sec.base_hz + sec.low_audio_hz)
        hi = rf_mhz(res, sec.base_hz + sec.high_audio_hz)
        reg = pg.LinearRegionItem(values=(float(lo), float(hi)), movable=False,
                                  brush=pg.mkBrush(255, 255, 255, 28),
                                  pen=pg.mkPen(255, 255, 255, 60))
        reg.setZValue(-5)
        win._ft8_plot.addItem(reg)
        win._ft8_regions.append(reg)
        for c in sec.candidates:
            section_of[id(c)] = n

    table = win._ft8_table
    table.setRowCount(len(res.candidates))
    for row, c in enumerate(res.candidates):
        vals = (f"{float(rf_mhz(res, c.freq_hz)):.6f}", f"{c.dt_s:+.2f}",
                f"{c.score:.1f}", str(section_of.get(id(c), "")))
        for col, v in enumerate(vals):
            item = QtWidgets.QTableWidgetItem(v)
            item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            table.setItem(row, col, item)

    dtab = getattr(win, "_ft8_decodes", None)
    decodes = list(getattr(res, "decodes", []))
    if dtab is not None:
        dtab.setRowCount(len(decodes))
        for row, d in enumerate(decodes):
            vals = (f"{d.rf_hz / 1e6:.6f}", f"{d.snr_db:+d}", f"{d.dt_s:+.1f}", d.message)
            for col, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(v)
                if col < 3:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                dtab.setItem(row, col, item)

    when = time.strftime("%H:%M:%S", time.gmtime(res.period_start_utc))
    partial = "" if res.data_fraction >= 0.99 else f" · {res.data_fraction:.0%} of period"
    if getattr(res, "decode_note", ""):
        dec = f" · {res.decode_note}"
    elif getattr(res, "decoded", False):
        dec = f" · {len(decodes)} decoded"
    elif res.sections:
        dec = " · decoding…"
    else:
        dec = ""
    win._ft8_status.setText(
        f"{res.mode} period {when} UTC · {len(res.candidates)} candidates in "
        f"{len(res.sections)} sections · search {res.compute_s:.2f} s{dec}{partial}")
