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
"""FT8 / FT4 Activity window: who was heard where, over the last few minutes.

A rotated, waterfall-style history.  Frequency runs up the side, with the
numbered ~3 kHz jt9 audio sections shaded across the plot; UTC time runs
along the bottom, one column per T/R period, scrolling left as periods end
(8 periods by default, set in the window and remembered).  Each decoded
station is a short tick in its period column, as tall as the signal, and
coloured by SNR.  Undecoded sync candidates can be shown as faint grey
marks.

Callsigns are not printed by default: hover over a tick for call, SNR,
DT and frequency, or tick *Show calls*.  ``set_highlighted_calls`` names
calls that are always labelled and outlined; that is the hook for a future
WS Hub "needed" feed.

The history is a pure data model (:class:`ActivityHistory`) fed once a
second from ``engine._ft8_result``.  Recording a period is a few list
appends; the plot is only redrawn while the window is visible, and only
when a new period arrives or that period's decodes come in.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from .plot_style import style_plot

#: Periods shown when the operator has not picked a number.
DEFAULT_PERIODS = 8
MIN_PERIODS = 2
MAX_PERIODS = 40
#: SNR colour scale ends, dB (WSJT-X reports -24 … about +20).
SNR_MIN = -24.0
SNR_MAX = 10.0
#: Each tick fills this share of its period column (gap between periods).
COLUMN_FILL = 0.84
#: Ticks are at least this many pixels tall when zoomed out.
MIN_TICK_PX = 3.0

# Light blue (weak) → blue → green → orange → red (strong); all readable on white.
SNR_CMAP = pg.ColorMap(
    np.array([0.0, 0.26, 0.5, 0.71, 1.0]),
    np.array([[150, 190, 235, 255],
              [30, 90, 200, 255],
              [30, 150, 70, 255],
              [240, 140, 20, 255],
              [200, 0, 0, 255]], dtype=np.ubyte),
)
CANDIDATE_RGBA = (140, 140, 140, 110)


def snr_fraction(snr_db: float) -> float:
    """Position of ``snr_db`` on the colour scale, clipped to 0…1."""
    t = (float(snr_db) - SNR_MIN) / (SNR_MAX - SNR_MIN)
    return 0.0 if t < 0.0 else 1.0 if t > 1.0 else t


def snr_color(snr_db: float) -> tuple[int, int, int]:
    """RGB for an SNR on :data:`SNR_CMAP`."""
    c = SNR_CMAP.map(snr_fraction(snr_db), mode="qcolor")
    return (int(c.red()), int(c.green()), int(c.blue()))


def clamp_periods(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_PERIODS
    return max(MIN_PERIODS, min(MAX_PERIODS, n))


def period_seconds(mode: str) -> float:
    from .costas_search import MODES
    return float(MODES[mode].period_s)


def signal_width_hz(mode: str) -> float:
    from .costas_search import MODES
    return float(MODES[mode].bandwidth_hz)


def period_start(t_utc: float, period_s: float) -> float:
    """Start of the T/R period containing ``t_utc``."""
    return float(np.floor(float(t_utc) / period_s + 1e-9) * period_s)


def view_x_range(newest_start: float, period_s: float, n_periods: int) -> tuple[float, float]:
    """Time axis showing the newest ``n_periods`` columns, newest on the right."""
    n = clamp_periods(n_periods)
    return (float(newest_start) - (n - 1) * period_s, float(newest_start) + period_s)


@dataclass
class Mark:
    """One tick: a decode (``snr_db`` set) or an undecoded sync candidate."""

    t0: float              # UTC seconds, left edge of the tick
    t1: float              # right edge
    f_lo_mhz: float
    f_hi_mhz: float
    dt_s: float
    snr_db: int | None = None
    score: float | None = None
    call: str = ""
    message: str = ""

    @property
    def is_decode(self) -> bool:
        return self.snr_db is not None

    @property
    def f_mid_mhz(self) -> float:
        return 0.5 * (self.f_lo_mhz + self.f_hi_mhz)

    def tooltip(self) -> str:
        when = time.strftime("%H:%M:%S", time.gmtime(self.t0 - _col_pad(self)))
        if self.is_decode:
            head = self.call or "?"
            return (f"{head}  {self.snr_db:+d} dB  DT {self.dt_s:+.1f} s\n"
                    f"{self.f_lo_mhz:.6f} MHz  {when} UTC\n{self.message}")
        return (f"Sync candidate (not decoded)  sync {self.score or 0:.1f}\n"
                f"DT {self.dt_s:+.1f} s  {self.f_lo_mhz:.6f} MHz  {when} UTC")


def _col_pad(mark: Mark) -> float:
    width = mark.t1 - mark.t0
    return width * (1.0 - COLUMN_FILL) / (2.0 * COLUMN_FILL)


@dataclass
class PeriodEntry:
    mode: str
    start: float
    period_s: float
    center_freq_mhz: float
    decodes: list = field(default_factory=list)       # Mark
    candidates: list = field(default_factory=list)    # Mark
    sections: list = field(default_factory=list)      # (lo_mhz, hi_mhz)
    n_decodes_raw: int = 0
    decoded: bool = False


def _column(start: float, period_s: float) -> tuple[float, float]:
    pad = period_s * (1.0 - COLUMN_FILL) / 2.0
    return start + pad, start + period_s - pad


def entry_from_result(res) -> PeriodEntry:
    """Turn one ``ft8_engine.PeriodResult`` into a history column."""
    from .ft8_window import call_from_message, rf_mhz, section_rf_mhz
    p = period_seconds(res.mode)
    bw = signal_width_hz(res.mode)
    start = period_start(res.period_start_utc, p)
    t0, t1 = _column(start, p)
    decodes = []
    for d in list(getattr(res, "decodes", []) or []):
        rf = float(d.rf_hz)
        decodes.append(Mark(t0, t1, rf / 1e6, (rf + bw) / 1e6, float(d.dt_s),
                            snr_db=int(d.snr_db), call=call_from_message(d.message),
                            message=str(d.message)))
    cands = []
    for c in list(getattr(res, "candidates", []) or []):
        lo = float(rf_mhz(res, c.freq_hz))
        hi = float(rf_mhz(res, c.freq_hz + bw))
        cands.append(Mark(t0, t1, lo, hi, float(c.dt_s), score=float(c.score)))
    sections = []
    for sec in list(getattr(res, "sections", []) or []):
        try:
            sections.append(section_rf_mhz(res, sec))
        except Exception:
            pass
    return PeriodEntry(mode=res.mode, start=start, period_s=p,
                       center_freq_mhz=float(res.center_freq_mhz),
                       decodes=decodes, candidates=cands, sections=sections,
                       n_decodes_raw=len(decodes),
                       decoded=bool(getattr(res, "decoded", False)))


class ActivityHistory:
    """The last ``n_periods`` period columns for one mode and centre frequency."""

    def __init__(self, n_periods: int = DEFAULT_PERIODS):
        self.n_periods = clamp_periods(n_periods)
        self.entries: dict[float, PeriodEntry] = {}
        self.mode: str | None = None
        self.center_freq_mhz: float | None = None
        #: Bumped whenever the visible content changes; the window redraws on it.
        self.version = 0

    def set_periods(self, n: int) -> None:
        n = clamp_periods(n)
        if n != self.n_periods:
            self.n_periods = n
            self._trim()
            self.version += 1

    def clear(self) -> None:
        if self.entries:
            self.version += 1
        self.entries.clear()

    def newest_start(self) -> float | None:
        return max(self.entries) if self.entries else None

    def period_s(self) -> float:
        return period_seconds(self.mode) if self.mode else 15.0

    def add(self, res) -> bool:
        """Record ``res``; True if the history changed (new period or new decodes)."""
        if res is None:
            return False
        center = float(res.center_freq_mhz)
        if (self.mode != res.mode or self.center_freq_mhz is None
                or abs(center - self.center_freq_mhz) > 1e-9):
            self.clear()
            self.mode = res.mode
            self.center_freq_mhz = center
        p = period_seconds(res.mode)
        start = period_start(res.period_start_utc, p)
        old = self.entries.get(start)
        n_dec = len(getattr(res, "decodes", []) or [])
        decoded = bool(getattr(res, "decoded", False))
        if old is not None and old.n_decodes_raw == n_dec and old.decoded == decoded \
                and len(old.candidates) == len(res.candidates):
            return False
        newest = self.newest_start()
        if newest is not None and start < newest - (self.n_periods - 1) * p - 1e-6:
            return False                    # older than the window: ignore
        self.entries[start] = entry_from_result(res)
        self._trim()
        self.version += 1
        return True

    def _trim(self) -> None:
        newest = self.newest_start()
        if newest is None:
            return
        keep_from = newest - (self.n_periods - 1) * self.period_s() - 1e-6
        for k in [k for k in self.entries if k < keep_from]:
            del self.entries[k]

    def columns(self) -> list[PeriodEntry]:
        return [self.entries[k] for k in sorted(self.entries)]

    def decodes(self) -> list[Mark]:
        return [m for e in self.columns() for m in e.decodes]

    def candidates(self) -> list[Mark]:
        return [m for e in self.columns() for m in e.candidates]

    def sections(self) -> list[tuple[float, float]]:
        """Sections of the newest period (they move a little period to period)."""
        newest = self.newest_start()
        return list(self.entries[newest].sections) if newest is not None else []

    def x_range(self) -> tuple[float, float] | None:
        newest = self.newest_start()
        if newest is None:
            return None
        return view_x_range(newest, self.period_s(), self.n_periods)

    def nearest(self, t: float, f_mhz: float, f_tol_mhz: float,
                include_candidates: bool = False) -> Mark | None:
        """The tick under (``t``, ``f_mhz``): same column, within ``f_tol_mhz``."""
        pools = [self.decodes()]
        if include_candidates:
            pools.append(self.candidates())
        for pool in pools:                 # decodes win over candidates
            best, best_d = None, None
            for m in pool:
                if not (m.t0 <= t <= m.t1):
                    continue
                if m.f_lo_mhz - f_tol_mhz <= f_mhz <= m.f_hi_mhz + f_tol_mhz:
                    d = abs(f_mhz - m.f_mid_mhz)
                    if best_d is None or d < best_d:
                        best, best_d = m, d
            if best is not None:
                return best
        return None


def labelled_marks(marks: list[Mark], show_calls: bool, highlighted: set[str]) -> list[Mark]:
    """Decodes that get a printed call: all with ``show_calls``, else highlighted only."""
    hl = {c.upper() for c in highlighted}
    return [m for m in marks if m.is_decode and m.call
            and (show_calls or m.call.upper() in hl)]


class TicksItem(pg.GraphicsObject):
    """Paints a list of :class:`Mark` as filled rectangles (min pixel height)."""

    def __init__(self):
        super().__init__()
        self._marks: list[Mark] = []
        self._colors: list[tuple] = []
        self._outline: list[bool] = []
        self._bounds = QtCore.QRectF()

    def set_marks(self, marks, colors, outline=None) -> None:
        self.prepareGeometryChange()
        self._marks = list(marks)
        self._colors = list(colors)
        self._outline = list(outline) if outline is not None else [False] * len(self._marks)
        if self._marks:
            x0 = min(m.t0 for m in self._marks)
            x1 = max(m.t1 for m in self._marks)
            y0 = min(m.f_lo_mhz for m in self._marks)
            y1 = max(m.f_hi_mhz for m in self._marks)
            pad = 0.002
            self._bounds = QtCore.QRectF(x0, y0 - pad, x1 - x0, (y1 - y0) + 2 * pad)
        else:
            self._bounds = QtCore.QRectF()
        self.update()

    def mark_count(self) -> int:
        return len(self._marks)

    def colors(self) -> list[tuple]:
        return list(self._colors)

    def boundingRect(self):
        return self._bounds

    def paint(self, painter, *_args):
        if not self._marks:
            return
        view = self.getViewBox()
        min_h = 0.0
        if view is not None:
            _px, py = view.viewPixelSize()
            min_h = MIN_TICK_PX * float(py)
        outline_pen = pg.mkPen((0, 0, 0), width=1.5)
        outline_pen.setCosmetic(True)
        painter.setPen(QtCore.Qt.NoPen)
        for m, col, hl in zip(self._marks, self._colors, self._outline):
            y0, y1 = m.f_lo_mhz, m.f_hi_mhz
            if y1 - y0 < min_h:
                mid = 0.5 * (y0 + y1)
                y0, y1 = mid - min_h / 2.0, mid + min_h / 2.0
            painter.setBrush(QtGui.QBrush(QtGui.QColor(*col)))
            painter.setPen(outline_pen if hl else QtCore.Qt.NoPen)
            painter.drawRect(QtCore.QRectF(m.t0, y0, m.t1 - m.t0, y1 - y0))


# ── Window ─────────────────────────────────────────────────────────────────

def _settings():
    from .visualizer import _SETTINGS
    return _SETTINGS


def _bool_setting(key: str, default: bool) -> bool:
    v = _settings().value(key, default)
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return bool(v)


def build_window(self, view_action):
    """Create the Activity panel (hidden); stored as ``self._activity_win``."""
    from .ui import _PanelWindow

    win = _PanelWindow("WS Skim — FT8 Activity", view_action, self, "activity_geometry")
    win.setMinimumSize(520, 420)
    geo = _settings().value("activity_geometry")
    if geo:
        win.restoreGeometry(geo)
    else:
        win.setGeometry(QtCore.QRect(480, 50, 980, 680))
    layout = QtWidgets.QVBoxLayout(win)
    layout.setContentsMargins(4, 4, 4, 4)

    status = QtWidgets.QLabel("Waiting for the first full period…")
    layout.addWidget(status)
    top = QtWidgets.QHBoxLayout()
    top.addWidget(QtWidgets.QLabel("History:"))
    spin = QtWidgets.QSpinBox()
    spin.setRange(MIN_PERIODS, MAX_PERIODS)
    spin.setSuffix(" periods")
    spin.setToolTip("How many T/R periods to show (8 FT8 periods = 2 minutes)")
    spin.setValue(clamp_periods(_settings().value("activity_periods", DEFAULT_PERIODS)))
    top.addWidget(spin)
    calls_cb = QtWidgets.QCheckBox("Show calls")
    calls_cb.setToolTip("Print every decoded call next to its tick. "
                        "Hover over a tick to see one call.")
    calls_cb.setChecked(_bool_setting("activity_show_calls", False))
    top.addWidget(calls_cb)
    cand_cb = QtWidgets.QCheckBox("Show sync candidates")
    cand_cb.setToolTip("Also mark signals the sync search found, decoded or not, in grey")
    cand_cb.setChecked(_bool_setting("activity_show_candidates", False))
    top.addWidget(cand_cb)
    top.addStretch(1)
    layout.addLayout(top)

    axis = pg.DateAxisItem(orientation="bottom", utcOffset=0)
    plot = pg.PlotWidget(axisItems={"bottom": axis})
    plot.setBackground("w")
    plot.setLabel("bottom", "Time (UTC)")
    plot.setLabel("left", "Frequency (MHz)")
    style_plot(plot)
    for _name in ("bottom", "left"):
        plot.getAxis(_name).setPen("k")
        plot.getAxis(_name).setTextPen("k")
    plot.showGrid(x=True, y=False, alpha=0.25)
    plot.getViewBox().disableAutoRange()
    # Mouse zoom and drag act on frequency only; time follows the History setting.
    plot.getViewBox().setMouseEnabled(x=False, y=True)
    plot.setMenuEnabled(True)

    cands = TicksItem()
    cands.setZValue(5)
    plot.addItem(cands)
    ticks = TicksItem()
    ticks.setZValue(10)
    plot.addItem(ticks)

    cbar_host = pg.GraphicsLayoutWidget()
    cbar_host.setBackground("w")
    cbar_host.setMinimumWidth(80)
    cbar_host.setMaximumWidth(104)
    cbar = pg.ColorBarItem(values=(SNR_MIN, SNR_MAX), colorMap=SNR_CMAP,
                           label="SNR (dB)", interactive=False, colorMapMenu=False,
                           pen="k", width=14)
    try:
        cbar.axis.setPen("k")
        cbar.axis.setTextPen("k")
    except Exception:
        pass
    cbar_host.addItem(cbar, row=0, col=0)
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    row.addWidget(plot, stretch=1)
    row.addWidget(cbar_host)
    layout.addLayout(row, stretch=1)

    win._act_status = status
    win._act_plot = plot
    win._act_ticks = ticks
    win._act_cands = cands
    win._act_cbar = cbar
    win._act_spin = spin
    win._act_calls_cb = calls_cb
    win._act_cand_cb = cand_cb
    win._act_regions = []
    win._act_section_labels = []
    win._act_labels = []
    win._act_history = ActivityHistory(spin.value())
    win._act_highlight = set()
    win._act_drawn_version = None
    win._act_y_key = None
    win._act_x_range = None
    win._act_newest = None
    win._act_last_res = None

    def on_periods(n):
        _settings().setValue("activity_periods", int(n))
        win._act_history.set_periods(n)
        win._act_newest = None               # reset the time axis to the new width
        redraw(win)

    def on_calls(on):
        _settings().setValue("activity_show_calls", bool(on))
        redraw(win, force=True)

    def on_cands(on):
        _settings().setValue("activity_show_candidates", bool(on))
        redraw(win, force=True)

    spin.valueChanged.connect(on_periods)
    calls_cb.toggled.connect(on_calls)
    cand_cb.toggled.connect(on_cands)
    plot.scene().sigMouseMoved.connect(lambda pos: _hover(win, pos))

    self._activity_win = win
    win.hide()
    timer = QtCore.QTimer(win)
    timer.setInterval(1000)
    timer.timeout.connect(lambda: tick(self, win))
    timer.start()
    win._act_timer = timer
    return win


def set_highlighted_calls(win, calls) -> None:
    """Calls always labelled and outlined (hook for a WS Hub 'needed' list)."""
    win._act_highlight = {str(c).upper() for c in (calls or []) if str(c).strip()}
    redraw(win, force=True)


def tick(eng, win) -> None:
    """Record the engine's latest period; redraw only when visible and changed."""
    from . import ft8_engine
    try:
        if not ft8_engine.is_period_mode(getattr(eng, "mode", "MSK144")):
            win._act_status.setText("Mode is MSK144. Choose Mode → FT8 to see FT8 activity.")
            return
        win._act_sample_rate = getattr(eng, "sample_rate", None)
        res = getattr(eng, "_ft8_result", None)
        if res is not None and res is not win._act_last_res:
            win._act_last_res = res
            win._act_history.add(res)
        if win._act_history.newest_start() is None:
            win._act_status.setText("Waiting for the first full period…")
            return
        if win.isVisible():
            redraw(win)
            _pin_time(win, win._act_history.x_range())
    except Exception as e:                        # never let a bad frame kill the panel
        win._act_status.setText(f"redraw error: {e}")


def redraw(win, force: bool = False) -> None:
    hist: ActivityHistory = win._act_history
    if not force and win._act_drawn_version == hist.version:
        return
    win._act_drawn_version = hist.version
    if hist.newest_start() is None:
        win._act_ticks.set_marks([], [])
        win._act_cands.set_marks([], [])
        return
    hl = set(getattr(win, "_act_highlight", set()))
    decs = hist.decodes()
    win._act_ticks.set_marks(decs, [snr_color(m.snr_db) for m in decs],
                             [bool(m.call) and m.call.upper() in hl for m in decs])
    if win._act_cand_cb.isChecked():
        cands = hist.candidates()
        win._act_cands.set_marks(cands, [CANDIDATE_RGBA] * len(cands))
    else:
        win._act_cands.set_marks([], [])
    _lock_axes(win, hist)
    _sync_sections(win, hist)
    _sync_labels(win, labelled_marks(decs, win._act_calls_cb.isChecked(), hl), hl)
    _update_status(win, hist)


def _lock_axes(win, hist: ActivityHistory) -> None:
    """Pin time to the newest periods; keep the operator's frequency zoom."""
    plot = win._act_plot
    vb = plot.getViewBox()
    vb.disableAutoRange()
    newest = hist.newest_start()
    xr = hist.x_range()
    # Time is locked to the History setting: newest period at the right edge.
    # (A mouse-wheel zoom used to stretch it, leaving the ticks bunched left.)
    _pin_time(win, xr)
    win._act_newest = newest
    win._act_x_range = xr

    from .ft8_engine import EDGE_FRACTION
    entry = hist.entries[newest]
    secs = entry.sections
    if secs:
        y0 = min(s[0] for s in secs)
        y1 = max(s[1] for s in secs)
    else:
        y0 = y1 = entry.center_freq_mhz
    rate = getattr(win, "_act_sample_rate", None)
    if rate:
        half = EDGE_FRACTION * float(rate) / 1e6
        y0 = min(y0, entry.center_freq_mhz - half)
        y1 = max(y1, entry.center_freq_mhz + half)
    if y1 - y0 < 0.003:
        y0, y1 = entry.center_freq_mhz - 0.024, entry.center_freq_mhz + 0.024
    key = (entry.mode, round(entry.center_freq_mhz, 9))
    if win._act_y_key != key:
        plot.setYRange(y0, y1, padding=0.01)
        win._act_y_key = key


def _pin_time(win, xr) -> None:
    """Put the time axis back on ``xr`` if anything (e.g. View All) moved it."""
    if xr is None:
        return
    (cx0, cx1), _ = win._act_plot.getViewBox().viewRange()
    if abs(cx0 - xr[0]) > 1e-3 or abs(cx1 - xr[1]) > 1e-3:
        win._act_plot.setXRange(*xr, padding=0)


def _sync_sections(win, hist: ActivityHistory) -> None:
    secs = hist.sections()
    regions = list(win._act_regions)
    labels = list(win._act_section_labels)
    # Alternate sections shaded / clear so neighbours read as stripes.
    brushes = (pg.mkBrush(255, 220, 80, 60), pg.mkBrush(255, 255, 255, 0))
    pen = pg.mkPen((205, 160, 0, 230), width=1.5)
    x_left = win._act_plot.getViewBox().viewRange()[0][0]
    for n, (lo, hi) in enumerate(secs, 1):
        if n <= len(regions):
            reg = regions[n - 1]
            reg.setRegion((lo, hi))
            reg.show()
        else:
            reg = pg.LinearRegionItem(values=(lo, hi), orientation="horizontal",
                                      movable=False, pen=pen)
            reg.setZValue(1)
            win._act_plot.addItem(reg)
            regions.append(reg)
        reg.setBrush(brushes[(n - 1) % 2])
        if n <= len(labels):
            lab = labels[n - 1]
            lab.setText(str(n))
        else:
            lab = pg.TextItem(str(n), color=(20, 20, 20),
                              fill=pg.mkBrush(255, 255, 255, 210), anchor=(0, 0.5))
            lab.setZValue(15)
            win._act_plot.addItem(lab)
            labels.append(lab)
        lab.setPos(x_left, 0.5 * (lo + hi))
        lab.show()
    for reg in regions[len(secs):]:
        reg.hide()
    for lab in labels[len(secs):]:
        lab.hide()
    win._act_regions = regions
    win._act_section_labels = labels


def _sync_labels(win, marks: list[Mark], hl: set[str]) -> None:
    labels = list(win._act_labels)
    font = QtGui.QFont()
    font.setPointSize(9)
    bold = QtGui.QFont(font)
    bold.setBold(True)
    for i, m in enumerate(marks):
        is_hl = m.call.upper() in hl
        if i < len(labels):
            item = labels[i]
            item.setText(m.call)
        else:
            item = pg.TextItem(m.call, color=(10, 10, 10), anchor=(0, 1.0))
            item.setZValue(20)
            win._act_plot.addItem(item)
            labels.append(item)
        item.setColor((150, 0, 0) if is_hl else (10, 10, 10))
        item.fill = pg.mkBrush((255, 255, 170, 230) if is_hl else (255, 255, 255, 170))
        item.setFont(bold if is_hl else font)
        item.setPos(m.t0, m.f_hi_mhz)           # just above the tick
        item.show()
    for item in labels[len(marks):]:
        item.hide()
    win._act_labels = labels


def _update_status(win, hist: ActivityHistory) -> None:
    cols = hist.columns()
    newest = cols[-1]
    when = time.strftime("%H:%M:%S", time.gmtime(newest.start))
    n_dec = sum(len(e.decodes) for e in cols)
    calls = {m.call for e in cols for m in e.decodes if m.call}
    minutes = hist.n_periods * hist.period_s() / 60.0
    if newest.decoded:
        last = f"{len(newest.decodes)} decoded"
    else:
        last = "decoding…"
    win._act_status.setText(
        f"{newest.mode} · last period {when} UTC: {last} · "
        f"{len(calls)} stations / {n_dec} decodes in {minutes:g} min")


def _hover(win, scene_pos) -> None:
    try:
        vb = win._act_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            QtWidgets.QToolTip.hideText()
            return
        p = vb.mapSceneToView(scene_pos)
        _px, py = vb.viewPixelSize()
        m = win._act_history.nearest(p.x(), p.y(), f_tol_mhz=4.0 * float(py),
                                     include_candidates=win._act_cand_cb.isChecked())
        if m is None:
            QtWidgets.QToolTip.hideText()
            return
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), m.tooltip(), win._act_plot)
    except Exception:
        pass
