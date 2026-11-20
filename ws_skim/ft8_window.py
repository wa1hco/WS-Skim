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
"""Mode menu (MSK144, or FT8 and/or FT4) and the FT8 Sync Map (Diagnostics) window.

The operating view is ``band_activity_window`` (View → Band Activity, all
modes); this Sync Map is the sync detector's diagnostic view, under
Diagnostics.  With FT8 and FT4 both on, its *Show* box picks the mode.

The window draws ``engine._ft8_result`` (``ft8_engine.PeriodResult``) once
per period, when the Costas search finishes: the sync score over RF
frequency (x) and signal start time (y), each jt9 section outlined and
labelled by its dial's kHz within the band ("74" = 7074 kHz), and each
candidate as a short bar as wide as the signal, coloured by sync strength.
A later decode result for the same period updates only the Decodes table,
the status line, and callsign/SNR labels next to the bars; the map,
sections and axes stay put. The axes are locked to the mode's fixed DT
window and the map's frequency range, so a manual zoom is kept across
periods until the frequency, mode, or extent changes.
"""
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from . import ft8_engine
from .plot_style import style_plot

#: Score shown at the top of the colour scale (noise is ~1).
SCORE_MAX = 6.0
#: Blanker per mode when the operator has not picked one: FT8/FT4 bands are
#: full of steady carriers that pull the impulse threshold around.
DEFAULT_BLANKER = {"MSK144": "Linrad", "FT8": "Bypass", "FT4": "Bypass"}
#: MSK144-only panels, hidden when switching into a period mode.
_MSK144_WINDOWS = ("_fast_graph_win", "_detect_win", "_sync_detect_win", "_cand_map_win")


# White → amber → red, so the bars read on the viridis map.
BAR_CMAP = pg.ColorMap(
    np.array([0.0, 0.5, 1.0]),
    np.array([[255, 255, 255, 255],
              [255, 200, 40, 255],
              [220, 20, 20, 255]], dtype=np.ubyte),
)
# Match a decode to a candidate: this close in RF and DT.
_LABEL_DF_HZ = 80.0
_LABEL_DT_S = 0.4


def bar_color(score: float) -> tuple[int, int, int]:
    """RGB for a sync score on :data:`BAR_CMAP` (1 = weak, SCORE_MAX = strong)."""
    t = (float(score) - 1.0) / max(1e-6, SCORE_MAX - 1.0)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    c = BAR_CMAP.map(t, mode="qcolor")
    return (int(c.red()), int(c.green()), int(c.blue()))


def call_from_message(message: str) -> str:
    """The transmitting callsign in a WSJT-X message, or '' if none is clear.

    For CQ messages the call is the first token after CQ that looks like a
    callsign (skipping DX / TEST / POTA and similar).  For a two-call
    message it is the sender, the second call.
    """
    raw = (message or "").replace(".", " ")
    toks = [t.strip("<>") for t in raw.split() if t.strip("<>")]
    if not toks:
        return ""

    def looks_like_call(tok: str) -> bool:
        t = tok.strip("+")
        if len(t) < 3 or len(t) > 10:
            return False
        # Maidenhead grid, not a call.
        if len(t) in (4, 6) and t[0:2].isalpha() and t[2:4].isdigit() and (
                len(t) == 4 or t[4:6].isalpha()):
            if t[0].upper() <= "R" and t[1].upper() <= "R":
                return False
        return any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t)

    if toks[0].upper() in ("CQ", "DE", "QRZ"):
        for t in toks[1:]:
            if looks_like_call(t):
                return t
        return toks[1] if len(toks) > 1 else ""
    if len(toks) >= 2 and looks_like_call(toks[1]):
        return toks[1]          # two-call message: the sender
    if looks_like_call(toks[0]):
        return toks[0]          # "CALL RR73" / a single call
    return toks[1] if len(toks) >= 2 else toks[0]


class CandidateBarsItem(pg.GraphicsObject):
    """One item that paints every candidate as a horizontal bar, coloured by score."""

    def __init__(self):
        super().__init__()
        self._picture = QtGui.QPicture()
        self._x0 = np.zeros(0)
        self._x1 = np.zeros(0)
        self._y = np.zeros(0)
        self._colors: list[tuple[int, int, int]] = []
        self._bounds = QtCore.QRectF()

    def bar_count(self) -> int:
        return int(self._x0.size)

    def bar_colors(self) -> list[tuple[int, int, int]]:
        return list(self._colors)

    def bar_spans(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return self._x0, self._x1, self._y

    def set_bars(self, x0, x1, y, scores) -> None:
        self._x0 = np.asarray(x0, dtype=float).reshape(-1)
        self._x1 = np.asarray(x1, dtype=float).reshape(-1)
        self._y = np.asarray(y, dtype=float).reshape(-1)
        self._colors = [bar_color(s) for s in np.asarray(scores, dtype=float).reshape(-1)]
        self._rebuild()
        self.prepareGeometryChange()
        self.update()

    def _rebuild(self) -> None:
        pic = QtGui.QPicture()
        painter = QtGui.QPainter(pic)
        for x0, x1, y, col in zip(self._x0, self._x1, self._y, self._colors):
            pen = QtGui.QPen(QtGui.QColor(*col))
            pen.setWidth(3)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(float(x0), float(y)),
                             QtCore.QPointF(float(x1), float(y)))
        painter.end()
        self._picture = pic
        if self._x0.size:
            pad = 0.05
            self._bounds = QtCore.QRectF(
                float(self._x0.min()), float(self._y.min()) - pad,
                float(self._x1.max() - self._x0.min()),
                float(self._y.max() - self._y.min()) + 2 * pad)
        else:
            self._bounds = QtCore.QRectF()

    def boundingRect(self):
        return self._bounds

    def paint(self, painter, *_args):
        # Prefer a live paint so a zoomed-out 50 Hz bar still has a
        # minimum pixel width; fall back to the cached picture.
        view = self.getViewBox()
        if view is None or not self._x0.size:
            painter.drawPicture(0, 0, self._picture)
            return
        px_x, _px_y = view.viewPixelSize()
        min_dx = 10.0 * float(px_x)          # at least ~10 px wide
        for x0, x1, y, col in zip(self._x0, self._x1, self._y, self._colors):
            xa, xb = float(x0), float(x1)
            if xb - xa < min_dx:
                mid = 0.5 * (xa + xb)
                xa, xb = mid - min_dx / 2.0, mid + min_dx / 2.0
            pen = QtGui.QPen(QtGui.QColor(*col))
            pen.setWidth(3)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(xa, float(y)),
                             QtCore.QPointF(xb, float(y)))




def blanker_key(mode: str) -> str:
    """Settings key for the blanker choice in ``mode`` (MSK144 keeps the old key)."""
    return "nb_backend" if mode == "MSK144" else f"nb_backend_{mode}"


def setup_ft8(self, menu_bar: QtWidgets.QMenuBar, view_menu: QtWidgets.QMenu) -> None:
    """Add the Mode menu, View → Band Activity and Diagnostics → FT8 Sync Map.

    Band Activity (all modes) is the operating view and opens with FT8/FT4.
    The Sync Map is the sync detector's diagnostic view; it lives under
    Diagnostics and only opens when asked.  Restores the saved mode.
    """
    from .visualizer import _SETTINGS
    from . import band_activity_window

    act_action = QtWidgets.QAction("Band Activity", self)
    act_action.setCheckable(True)
    act_action.setToolTip("Waterfall of the whole band with every decode marked by mode")
    first = view_menu.actions()[0] if view_menu.actions() else None
    view_menu.insertAction(first, act_action)         # top of View, easy to find
    band_activity_window.build_window(self, act_action)
    act_action.triggered.connect(
        lambda checked: self._band_activity_win.show() if checked
        else self._band_activity_win.hide())
    self._band_activity_action = act_action

    diag = next((a.menu() for a in menu_bar.actions()
                 if a.menu() is not None and a.text().replace("&", "") == "Diagnostics"), None)
    view_action = QtWidgets.QAction("FT8 Sync Map", self)
    view_action.setCheckable(True)
    view_action.setToolTip("The sync detector's view of the last period: sync score "
                           "by frequency and DT, jt9 sections, and candidates")
    if diag is not None:
        diag.addAction(view_action)
    else:
        view_menu.addAction(view_action)
    _build_window(self, view_action)
    view_action.triggered.connect(
        lambda checked: self._ft8_win.show() if checked else self._ft8_win.hide())
    self._sync_map_action = view_action

    # Mode menu: MSK144 (VHF meteor scatter) on its own, or any of the
    # period modes (FT8, FT4) together from the one receiver.
    mode_menu = QtWidgets.QMenu("&Mode", self)
    mode_menu.setToolTipsVisible(True)          # explains a greyed-out MSK144
    menu_bar.insertMenu(view_menu.menuAction(), mode_menu)
    self._mode_actions = {}
    for mode in ft8_engine.MODES:
        act = QtWidgets.QAction(mode, self, checkable=True)
        if mode == "MSK144":
            act.setToolTip(MSK144_ON_TIP)
        else:
            act.setToolTip(f"Decode {mode}; FT8 and FT4 can both be on")
        act.triggered.connect(lambda _c, m=mode: _on_mode_action(self, m))
        mode_menu.addAction(act)
        if mode == "MSK144":
            mode_menu.addSeparator()
        self._mode_actions[mode] = act

    # Diagnostics → Save FT8 period IQ (off by default, not remembered).
    if diag is not None:
        save_act = QtWidgets.QAction("Save FT8 period IQ (last 10 min)", self, checkable=True)
        save_act.setToolTip("Write each FT8/FT4 period's radio data to the output "
                            "folder's ft8_iq/ for offline analysis (~7 MB per period)")
        save_act.toggled.connect(lambda on: setattr(self, "_ft8_save_iq", bool(on)))
        diag.addAction(save_act)
        self._ft8_save_iq_action = save_act

    _setup_rate_label(self)
    saved = saved_modes(_SETTINGS)
    if saved == ("MSK144",) and not msk144_saved_band_ok(_SETTINGS):
        # MSK144 was saved on a band where it is no longer offered (70 cm).
        saved = ft8_engine.PERIOD_MODES
    if saved == ("MSK144",):
        apply_mode(self, "MSK144", startup=True)
    else:
        apply_modes(self, saved, startup=True)
    update_msk144_action(self)


#: Tooltip of Mode → MSK144 when the band has no MSK144.
MSK144_OFF_TIP = ("MSK144 (meteor scatter) is offered on 6 m, 2 m and 1.25 m only. "
                  "Pick one of those bands in the toolbar first.")
MSK144_ON_TIP = "Meteor scatter (6 m, 2 m, 1.25 m); turns FT8 and FT4 off"


def msk144_saved_band_ok(settings) -> bool:
    """The saved MSK144 calling frequency is on an MSK144 band."""
    from .ui import msk144_allowed
    return msk144_allowed(settings.value("calling_freq_mhz", 50.260))


def update_msk144_action(self) -> None:
    """Enable Mode → MSK144 only on 6 m, 2 m and 1.25 m (always while on)."""
    from .ui import msk144_allowed
    act = (getattr(self, "_mode_actions", None) or {}).get("MSK144")
    if act is None:
        return
    on = getattr(self, "mode", "") == "MSK144" and not ft8_engine.enabled_period_modes(self)
    ok = on or msk144_allowed(getattr(self, "calling_freq_mhz", None))
    act.setEnabled(ok)
    act.setToolTip(MSK144_ON_TIP if ok else MSK144_OFF_TIP)


#: Settings key for the mode the Sync Map shows when FT8 and FT4 are both on
#: (the FT8 Activity window's ``activity_view_mode`` is read once to migrate).
SYNC_MAP_MODE_KEY = "sync_map_mode"


#: Settings key for the decoded period modes ("FT8,FT4").  Older versions
#: saved one ``mode``; that key is still written (the primary mode).
MODES_KEY = "period_modes"


def saved_modes(settings) -> tuple[str, ...]:
    """The remembered mode selection: ``("MSK144",)`` or period modes.

    Settings from before FT8 and FT4 could run together have only
    ``mode=FT8`` or ``mode=FT4``; a background skimmer should hear both,
    so either one turns into FT8 + FT4.
    """
    mode = str(settings.value("mode", ft8_engine.DEFAULT_MODE))
    if mode not in ft8_engine.MODES:
        mode = ft8_engine.DEFAULT_MODE
    if mode == "MSK144":
        return ("MSK144",)
    raw = settings.value(MODES_KEY, None)
    if raw is None:
        return ft8_engine.PERIOD_MODES                    # migrate: FT8 or FT4 -> both
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    return ft8_engine.normalize_period_modes(str(raw)) or (mode,)


def _on_mode_action(self, mode: str) -> None:
    """A Mode menu click: MSK144 is exclusive; FT8 / FT4 toggle together."""
    acts = getattr(self, "_mode_actions", {})
    if mode == "MSK144":
        apply_mode(self, "MSK144")
        return
    on = tuple(m for m in ft8_engine.PERIOD_MODES
               if acts.get(m) is not None and acts[m].isChecked())
    if not on:                                   # never leave nothing decoding
        acts[mode].setChecked(True)
        on = (mode,)
    apply_modes(self, on)


def _sync_mode_checks(self, modes) -> None:
    for m, act in getattr(self, "_mode_actions", {}).items():
        want = m in modes
        if act.isChecked() != want:
            act.setChecked(want)


def apply_mode(self, mode: str, startup: bool = False) -> None:
    """Switch the engine, the blanker and the visible panels to ``mode``
    (FT8 or FT4 alone, or MSK144)."""
    if ft8_engine.is_period_mode(mode):
        apply_modes(self, (mode,), startup=startup)
        return
    from .visualizer import _SETTINGS
    if mode == "MSK144" and not startup:
        # Stay on the band: FT8 on 6 m → MSK144 on 6 m (50.260).
        from .ui import msk144_band_for
        same = msk144_band_for(getattr(self, "calling_freq_mhz", None))
        if same is not None:
            _SETTINGS.setValue("calling_freq_mhz", same)
    self.set_mode(mode)
    _SETTINGS.setValue("mode", mode)
    _sync_mode_checks(self, (mode,))
    _apply_band_and_blanker(self, mode)
    if not startup:
        # Band Activity covers MSK144 too and stays open; the Sync Map is FT8/FT4 only.
        w = getattr(self, "_ft8_win", None)
        if w is not None:
            w.hide()
    _update_rate_label(self)
    update_msk144_action(self)


def apply_modes(self, modes, startup: bool = False) -> None:
    """Decode the period modes ``modes`` (FT8 and/or FT4) side by side."""
    from .visualizer import _SETTINGS
    modes = ft8_engine.normalize_period_modes(modes) or ("FT8",)
    primary = modes[0]
    if hasattr(self, "set_period_modes"):
        self.set_period_modes(modes)
    else:
        self.set_mode(primary)
    _SETTINGS.setValue("mode", primary)
    _SETTINGS.setValue(MODES_KEY, ",".join(modes))
    _sync_mode_checks(self, modes)
    _apply_band_and_blanker(self, primary)

    win = getattr(self, "_ft8_win", None)
    act = getattr(self, "_band_activity_win", None)
    for name in _MSK144_WINDOWS:
        w = getattr(self, name, None)
        if w is not None and w.isVisible():
            w.hide()
    view = getattr(self, "_ft8_view_mode", None)
    if view not in modes:
        saved = str(_SETTINGS.value(SYNC_MAP_MODE_KEY, None)
                    or _SETTINGS.value("activity_view_mode", "") or "")
        view = self._ft8_view_mode = saved if saved in modes else primary
    if win is not None:
        win.setWindowTitle(f"WS Skim — {view} Sync Map")
        _sync_show_combo(self, win, modes)
    if act is not None:
        act.show()
    elif win is not None:
        win.show()
    _update_rate_label(self)
    update_msk144_action(self)


def _apply_band_and_blanker(self, mode: str) -> None:
    from .visualizer import _SETTINGS
    if hasattr(self, "_band_combo"):
        from .ui import set_band_list
        set_band_list(self, mode)

    combo = getattr(self, "nb_backend_combo", None)
    want = str(_SETTINGS.value(blanker_key(mode), DEFAULT_BLANKER.get(mode, "Linrad")))
    if combo is not None and combo.findText(want) >= 0:
        if combo.currentText() == want:
            self.set_blanker(want)
        else:
            combo.setCurrentText(want)        # on_nb_backend_changed applies + saves
    elif combo is None:
        self.set_blanker(want)


# ── Status bar: decode rate per mode ───────────────────────────────────────

def _setup_rate_label(self) -> None:
    """A permanent status-bar label: ``FT8 312/min · FT4 48/min``."""
    bar_fn = getattr(self, "statusBar", None)
    if bar_fn is None:
        return
    try:
        label = QtWidgets.QLabel("")
        label.setToolTip("Decodes per minute for each mode over the last minute")
        bar_fn().addPermanentWidget(label)
    except Exception:
        return
    self._ft8_rate_label = label
    timer = QtCore.QTimer(label)
    timer.setInterval(2000)
    timer.timeout.connect(lambda: _update_rate_label(self))
    timer.start()
    self._ft8_rate_timer = timer


def _update_rate_label(self) -> None:
    label = getattr(self, "_ft8_rate_label", None)
    if label is None:
        return
    try:
        text = ft8_engine.rate_text(self)
        label.setText(text)
        label.setVisible(bool(text))
        tip = "Decodes per minute for each mode over the last minute"
        if ft8_engine.filter_text(self):
            tip += ("\nFiltered as false decodes since start: "
                    + ft8_engine.filter_detail(self)
                    + "\n(written to rejected.jsonl in the output folder)")
        label.setToolTip(tip)
    except Exception:
        pass


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
    top = QtWidgets.QHBoxLayout()
    top.addWidget(status, stretch=1)
    show_label = QtWidgets.QLabel("Show:")
    show_combo = QtWidgets.QComboBox()
    show_combo.setToolTip("Which mode's sync search this window shows")
    top.addWidget(show_label)
    top.addWidget(show_combo)
    show_label.hide()
    show_combo.hide()
    layout.addLayout(top)

    plot = pg.PlotWidget()
    plot.setBackground("w")
    plot.setLabel("bottom", "Frequency (MHz)")
    plot.setLabel("left", "DT (s)")
    style_plot(plot)
    for _name in ("bottom", "left"):
        plot.getAxis(_name).setPen("k")
        plot.getAxis(_name).setTextPen("k")
    plot.showGrid(x=True, y=True, alpha=0.2)
    plot.getViewBox().disableAutoRange()
    img = pg.ImageItem()
    img.setColorMap(pg.colormap.get("viridis"))
    img.setLevels((1.0, SCORE_MAX))
    plot.addItem(img)
    bars = CandidateBarsItem()
    bars.setZValue(10)
    plot.addItem(bars)

    # Preferred-DT band and the DT = 0 line; values set in set_dt_guides.
    pref = pg.LinearRegionItem(values=(-0.5, 1.0),
                               orientation="horizontal", movable=False,
                               brush=pg.mkBrush(255, 255, 255, 28),
                               pen=pg.mkPen((230, 230, 230, 160), width=1,
                                            style=QtCore.Qt.DashLine))
    pref.setZValue(1)
    pref.setVisible(False)
    plot.addItem(pref)
    zero = pg.InfiniteLine(pos=0.0, angle=0,
                           pen=pg.mkPen((255, 255, 255, 210), width=1.5))
    zero.setZValue(3)
    zero.setVisible(False)
    plot.addItem(zero)

    cbar_host = pg.GraphicsLayoutWidget()
    cbar_host.setBackground("w")
    cbar_host.setMinimumWidth(72)
    cbar_host.setMaximumWidth(96)
    cbar = pg.ColorBarItem(values=(1.0, SCORE_MAX), colorMap=BAR_CMAP,
                           label="Sync", interactive=False, colorMapMenu=False,
                           pen="k", width=14)
    cbar_host.addItem(cbar, row=0, col=0)
    plot_row = QtWidgets.QWidget()
    plot_h = QtWidgets.QHBoxLayout(plot_row)
    plot_h.setContentsMargins(0, 0, 0, 0)
    plot_h.setSpacing(2)
    plot_h.addWidget(plot, stretch=1)
    plot_h.addWidget(cbar_host)

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
    split.addWidget(plot_row)
    split.addWidget(tabs)
    split.setSizes([400, 200])
    layout.addWidget(split, stretch=1)

    win._ft8_status = status
    win._ft8_show_combo = show_combo
    win._ft8_show_label = show_label
    win._ft8_combo_modes = ()

    def on_show(text):
        if text and text != getattr(self, "_ft8_view_mode", None):
            set_view_mode(self, win, text)

    show_combo.currentTextChanged.connect(on_show)
    win._ft8_plot = plot
    win._ft8_img = img
    win._ft8_bars = bars
    win._ft8_dt_pref = pref
    win._ft8_dt_zero = zero
    win._ft8_cbar = cbar
    win._ft8_table = table
    win._ft8_decodes = decodes
    win._ft8_regions = []
    win._ft8_section_labels = []
    win._ft8_labels = []        # callsign TextItems, reused across periods
    win._ft8_drawn = None
    win._ft8_drawn_key = None   # (mode, period_start_utc) of the drawn map
    win._ft8_axis_key = None    # last (mode, centre, extent) used for setRange
    self._ft8_win = win
    win.hide()

    timer = QtCore.QTimer(win)
    timer.setInterval(1000)
    timer.timeout.connect(lambda: _tick(self, win))
    timer.start()
    win._ft8_timer = timer


def set_view_mode(eng, win, mode: str) -> None:
    """Show ``mode``'s sync search in the Sync Map (remembered)."""
    from .visualizer import _SETTINGS
    eng._ft8_view_mode = mode
    _SETTINGS.setValue(SYNC_MAP_MODE_KEY, mode)
    win.setWindowTitle(f"WS Skim — {mode} Sync Map")
    win._ft8_drawn = None
    win._ft8_drawn_key = None
    win._ft8_axis_key = None
    combo = win._ft8_show_combo
    if combo.currentText() != mode:
        combo.blockSignals(True)
        combo.setCurrentText(mode)
        combo.blockSignals(False)


def _sync_show_combo(eng, win, modes: tuple) -> None:
    """List the decoded period modes in the Show box (shown for two or more);
    follow the engine if the shown mode was turned off."""
    combo = getattr(win, "_ft8_show_combo", None)
    if combo is None or not modes:
        return
    if modes != win._ft8_combo_modes:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(modes))
        combo.blockSignals(False)
        win._ft8_combo_modes = modes
        many = len(modes) > 1
        combo.setVisible(many)
        win._ft8_show_label.setVisible(many)
    view = getattr(eng, "_ft8_view_mode", None)
    want = view if view in modes else modes[0]
    if want != view:
        set_view_mode(eng, win, want)
    elif combo.currentText() != want:
        combo.blockSignals(True)
        combo.setCurrentText(want)
        combo.blockSignals(False)


def _tick(eng, win) -> None:
    try:
        modes = ft8_engine.enabled_period_modes(eng)
        if not modes:
            win._ft8_status.setText("Mode is MSK144. Choose Mode → FT8 to search for FT8.")
            return
        _sync_show_combo(eng, win, modes)
        res = shown_result(eng)
        if res is None:
            win._ft8_status.setText("Waiting for the first full period…")
            return
        if not win.isVisible():
            return
        apply_result(win, res)
    except Exception as e:                       # never let a bad frame kill the panel
        win._ft8_status.setText(f"redraw error: {e}")


def shown_result(eng):
    """The newest result of the mode the windows show (``_ft8_view_mode``,
    picked in the Sync Map's Show box), else the newest of any mode."""
    view = getattr(eng, "_ft8_view_mode", None)
    results = getattr(eng, "_ft8_results", None) or {}
    if view in results:
        return results[view]
    res = getattr(eng, "_ft8_result", None)
    if view is not None and res is not None and getattr(res, "mode", view) != view:
        return None
    return res


def rf_mhz(res, offset_hz) -> np.ndarray:
    return res.center_freq_mhz + np.asarray(offset_hz, dtype=float) / 1e6


def period_key(res):
    """Identity of one search period: mode plus UTC period start."""
    return (res.mode, res.period_start_utc)


def map_view_extent(res) -> tuple[float, float, float, float]:
    """Fixed axis window: full map frequency range, mode DT window.

    Y is DT in seconds (0 = on time): ``dt_min_s`` … ``dt_max_s``, not
    the data's own lag extent (which can vary by a lag or two and made
    the map jump).
    """
    from .costas_search import MODES
    spec = MODES[res.mode]
    y0 = spec.dt_min_s
    y1 = spec.dt_max_s
    freqs = getattr(res, "map_freqs_hz", None)
    f = rf_mhz(res, freqs) if freqs is not None else np.array([])
    if np.size(f) == 0:
        x0, x1 = float(res.center_freq_mhz) - 0.024, float(res.center_freq_mhz) + 0.024
    elif np.size(f) == 1:
        x0 = float(f[0]) - 0.0001
        x1 = float(f[0]) + 0.0001
    else:
        df = (float(f[-1]) - float(f[0])) / max(1, len(f) - 1)
        x0 = float(f[0] - df / 2)
        x1 = float(f[-1] + df / 2)
    return x0, x1, y0, y1


def apply_result(win, res) -> None:
    """Draw a new period's map, or only the decodes if this period is on screen."""
    key = period_key(res)
    if getattr(win, "_ft8_drawn_key", None) == key:
        update_decodes(win, res)
        update_status(win, res)
        update_labels(win, res)          # labels only; map / regions / axes stay
        win._ft8_drawn = res
        return
    draw_result(win, res)
    win._ft8_drawn = res
    win._ft8_drawn_key = key


def lock_axes(win, res) -> None:
    """Set the view to the fixed DT / frequency window when the extent changes.

    Autorange stays off.  A manual zoom is left alone until the centre
    frequency, mode, or map extent changes.
    """
    x0, x1, y0, y1 = map_view_extent(res)
    key = (res.mode, round(float(res.center_freq_mhz), 9),
           round(x0, 9), round(x1, 9), y0, y1)
    if getattr(win, "_ft8_axis_key", None) == key:
        return
    vb = win._ft8_plot.getViewBox()
    vb.disableAutoRange()
    win._ft8_plot.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0)
    win._ft8_axis_key = key



def section_rf_mhz(res, sec) -> tuple[float, float]:
    """RF limits of the audio window jt9 actually searches in this section.

    ``Section.low/high_audio_hz`` only bracket the Costas hits; jt9 is run
    over at least 200–3200 Hz around ``base_hz`` (see ``ft8_decode``).
    """
    from . import ft8_decode
    lo_hz = min(float(ft8_decode.JT9_LOW_HZ), float(sec.low_audio_hz))
    hi_hz = max(float(ft8_decode.JT9_HIGH_HZ), float(sec.high_audio_hz))
    return float(rf_mhz(res, sec.base_hz + lo_hz)), float(rf_mhz(res, sec.base_hz + hi_hz))


def sync_regions(win, res) -> dict:
    """Reuse section region items, setting values and hiding extras.

    Each section is a clearly outlined band with a number at the top of
    the plot.  Returns a map of ``id(candidate)`` to 1-based section number.
    """
    section_of = {}
    existing = list(getattr(win, "_ft8_regions", []))
    labels = list(getattr(win, "_ft8_section_labels", []))
    used = 0
    _x0, _x1, _y0, y1 = map_view_extent(res)
    # In front of the (opaque) map image so the outlines show; fill stays
    # light so the sync score still reads through the band.
    brush = pg.mkBrush(255, 240, 120, 35)
    pen = pg.mkPen((255, 230, 80, 255), width=2)
    from .wsjtx_udp import region_name
    for sec in res.sections:
        n = region_name(float(rf_mhz(res, sec.base_hz)) * 1e6)
        lo, hi = section_rf_mhz(res, sec)
        mid = 0.5 * (lo + hi)
        if used < len(existing):
            reg = existing[used]
            reg.setRegion((lo, hi))
            reg.setBrush(brush)
            for ln in getattr(reg, "lines", []):
                ln.setPen(pen)
            reg.setZValue(2)
            reg.show()
        else:
            reg = pg.LinearRegionItem(values=(lo, hi), movable=False,
                                      brush=brush, pen=pen)
            reg.setZValue(2)
            win._ft8_plot.addItem(reg)
            existing.append(reg)
        if used < len(labels):
            lab = labels[used]
            lab.setText(n)
            lab.setPos(mid, y1)
            lab.show()
        else:
            lab = pg.TextItem(n, color=(20, 20, 20),
                              fill=pg.mkBrush(255, 255, 255, 200),
                              anchor=(0.5, 0))
            lab.setZValue(15)
            lab.setPos(mid, y1)
            win._ft8_plot.addItem(lab)
            labels.append(lab)
        used += 1
        for c in sec.candidates:
            section_of[id(c)] = n
    for reg in existing[used:]:
        reg.hide()
    for lab in labels[used:]:
        lab.hide()
    win._ft8_regions = existing
    win._ft8_section_labels = labels
    return section_of


def update_decodes(win, res) -> None:
    """Fill the Decodes table from ``res`` (safe to call on a decode-only update)."""
    dtab = getattr(win, "_ft8_decodes", None)
    decodes = list(getattr(res, "decodes", []))
    if dtab is None:
        return
    dtab.setRowCount(len(decodes))
    for row, d in enumerate(decodes):
        vals = (f"{d.rf_hz / 1e6:.6f}", f"{d.snr_db:+d}", f"{d.dt_s:+.1f}", d.message)
        for col, v in enumerate(vals):
            item = QtWidgets.QTableWidgetItem(v)
            if col < 3:
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            dtab.setItem(row, col, item)


def update_status(win, res) -> None:
    when = time.strftime("%H:%M:%S", time.gmtime(res.period_start_utc))
    partial = "" if res.data_fraction >= 0.99 else f" · {res.data_fraction:.0%} of period"
    decodes = list(getattr(res, "decodes", []))
    if getattr(res, "decode_note", ""):
        dec = f" · {res.decode_note}"
    elif getattr(res, "decoded", False):
        dec = f" · {len(decodes)} decoded"
    elif res.sections:
        dec = " · decoding…"
    else:
        dec = ""
    aside = len(getattr(res, "dropped_candidates", []) or [])
    aside_txt = f" (+{aside} set aside: no recent decodes nearby)" if aside else ""
    win._ft8_status.setText(
        f"{res.mode} period {when} UTC · {len(res.candidates)} candidates{aside_txt} in "
        f"{len(res.sections)} sections · search {res.compute_s:.2f} s{dec}{partial}")


def ensure_bars(win) -> CandidateBarsItem:
    """The bars item, created on first use so tests can omit it from the stub."""
    bars = getattr(win, "_ft8_bars", None)
    if bars is None:
        bars = CandidateBarsItem()
        bars.setZValue(10)
        win._ft8_plot.addItem(bars)
        win._ft8_bars = bars
    return bars


def set_dt_guides(win, res) -> None:
    """Show the preferred-DT band and a faint line at DT = 0."""
    from .costas_search import MODES
    spec = MODES[res.mode]
    pref = getattr(win, "_ft8_dt_pref", None)
    if pref is None:
        pref = pg.LinearRegionItem(values=(spec.dt_pref_min_s, spec.dt_pref_max_s),
                                   orientation="horizontal", movable=False,
                                   brush=pg.mkBrush(255, 255, 255, 28),
                                   pen=pg.mkPen((230, 230, 230, 160), width=1,
                                                style=QtCore.Qt.DashLine))
        pref.setZValue(1)
        win._ft8_plot.addItem(pref)
        win._ft8_dt_pref = pref
    else:
        pref.setRegion((spec.dt_pref_min_s, spec.dt_pref_max_s))
    pref.setVisible(True)
    zero = getattr(win, "_ft8_dt_zero", None)
    if zero is None:
        zero = pg.InfiniteLine(pos=0.0, angle=0,
                               pen=pg.mkPen((255, 255, 255, 210), width=1.5))
        zero.setZValue(3)
        win._ft8_plot.addItem(zero)
        win._ft8_dt_zero = zero
    else:
        zero.setPos(0.0)
    zero.setVisible(True)


def set_candidate_bars(win, res) -> None:
    """Replace the candidate marks with one set of bandwidth-wide score bars."""
    from .costas_search import MODES
    spec = MODES[res.mode]
    bw = float(spec.bandwidth_hz)
    x0 = rf_mhz(res, [c.freq_hz for c in res.candidates])
    x1 = rf_mhz(res, [c.freq_hz + bw for c in res.candidates])
    ys = [c.dt_s for c in res.candidates]
    scores = [c.score for c in res.candidates]
    ensure_bars(win).set_bars(x0, x1, ys, scores)


def label_position(res, dec, candidates) -> tuple[float, float]:
    """x, y for a decode label: nearest candidate bar, else the decode itself."""
    from .costas_search import MODES
    spec = MODES[res.mode]
    y_d = dec.dt_s
    rf = float(dec.rf_hz)
    best = None
    best_m = None
    for c in candidates:
        rf_c = res.center_freq_mhz * 1e6 + c.freq_hz + spec.bandwidth_hz / 2.0
        df = abs(rf_c - rf)
        dt = abs(c.dt_s - y_d)
        if df <= _LABEL_DF_HZ and dt <= _LABEL_DT_S:
            m = (df / 25.0) ** 2 + (dt / 0.16) ** 2
            if best_m is None or m < best_m:
                best, best_m = c, m
    if best is not None:
        x = float(rf_mhz(res, best.freq_hz + spec.bandwidth_hz))
        y = best.dt_s
    else:
        x = rf / 1e6
        y = y_d
    return x, y


def clear_labels(win) -> None:
    """Hide every callsign label (a new period is about to be drawn)."""
    for item in getattr(win, "_ft8_labels", []):
        item.hide()


def update_labels(win, res) -> None:
    """Place 'CALL SNR' text next to each decoded station.  Reuses TextItems."""
    labels = list(getattr(win, "_ft8_labels", []))
    decodes = list(getattr(res, "decodes", []) or [])
    candidates = list(res.candidates)
    used = 0
    for dec in decodes:
        call = call_from_message(dec.message)
        if not call:
            continue
        text = f"{call} {dec.snr_db:+d}"
        x, y = label_position(res, dec, candidates)
        if used < len(labels):
            item = labels[used]
            item.setText(text)
            item.setPos(x, y)
            item.show()
        else:
            item = pg.TextItem(text, color=(255, 255, 255),
                               fill=pg.mkBrush(0, 0, 0, 150),
                               anchor=(0, 0.5))
            item.setZValue(20)
            item.setPos(x, y)
            win._ft8_plot.addItem(item)
            labels.append(item)
        used += 1
    for item in labels[used:]:
        item.hide()
    win._ft8_labels = labels


def draw_result(win, res) -> None:
    """Redraw the map, regions, candidate bars, decodes and status for one period."""
    m = res.map_score
    if m.size:
        f = rf_mhz(res, res.map_freqs_hz)
        from .costas_search import MODES
        t = res.map_start_s - MODES[res.mode].nominal_start_s
        df = (f[-1] - f[0]) / max(1, len(f) - 1)
        dt = (t[-1] - t[0]) / max(1, len(t) - 1) if len(t) > 1 else 1.0
        # ImageItem axes: first index = x (frequency), second = y (DT).
        win._ft8_img.setImage(np.clip(m.T, 0.0, SCORE_MAX), autoLevels=False)
        win._ft8_img.setRect(QtCore.QRectF(f[0] - df / 2, t[0] - dt / 2,
                                           df * len(f), dt * len(t)))

    lock_axes(win, res)
    set_dt_guides(win, res)
    set_candidate_bars(win, res)
    clear_labels(win)

    section_of = sync_regions(win, res)

    table = win._ft8_table
    table.setRowCount(len(res.candidates))
    for row, c in enumerate(res.candidates):
        vals = (f"{float(rf_mhz(res, c.freq_hz)):.6f}", f"{c.dt_s:+.2f}",
                f"{c.score:.1f}", str(section_of.get(id(c), "")))
        for col, v in enumerate(vals):
            item = QtWidgets.QTableWidgetItem(v)
            item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            table.setItem(row, col, item)

    update_decodes(win, res)
    update_status(win, res)
    update_labels(win, res)
