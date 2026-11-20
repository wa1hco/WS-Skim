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
"""Roster window (View → Roster): every decode, newest first, sortable.

This is the decode list that used to fill the main window.  It is the
start of the needed-station roster in
``docs/plan/ws-skim-standalone-and-modes.md``; the "needed" filters are not
built yet.

One row per decode: UTC, mode, frequency, DF, polarisation (MSK144 dual-pol
only), SNR, call and message.  Click a column header to sort by it (click
again to reverse); the newest decodes are on top by default.  Text fades
with age (black under 2 min, then amber, orange and grey after 20 min).
Right-click an MSK144 decode to open its saved recording in the Analysis
window.

The engine keeps a reference to the table as ``engine.decode_panel`` (the
name the decode path and source-start clear have always used).
"""
from __future__ import annotations

import time

from PyQt5 import QtCore, QtGui, QtWidgets

#: Settings keys.
GEOMETRY_KEY = "roster_geometry"
VISIBLE_KEY = "roster_visible"

COLUMNS = ("UTC", "Mode", "Freq (kHz)", "DF (Hz)", "Pol", "SNR", "Call", "Message")
COLUMN_TIPS = (
    "UTC time of the decode (MSK144: the ping)",
    "FT8, FT4 or MSK144",
    "Radio frequency of the signal, kHz",
    "FT8/FT4: audio frequency in its WSJT-X region (WSJT-X's Freq column).\n"
    "MSK144: offset from the calling frequency.",
    "Polarisation angle (dual-polarisation MSK144 receivers only)",
    "Signal-to-noise ratio, dB (WSJT-X scale)",
    "The sending station",
    "The decoded message",
)
COL_UTC, COL_MODE, COL_FREQ, COL_DF, COL_POL, COL_SNR, COL_CALL, COL_MSG = range(8)

#: At most this many rows; the oldest is dropped first.
MAX_ROWS = 2000

#: Age colours for a white background (WSJT-X/MAP65 fade, light mode).
AGE_COLORS = (
    (120, (0, 0, 0)),             # < 2 min  black
    (480, (130, 95, 0)),          # < 8 min  amber
    (1200, (190, 90, 0)),         # < 20 min orange
    (None, (140, 140, 140)),      # older    grey
)

_SORT_ROLE = QtCore.Qt.UserRole + 10
_EPOCH_ROLE = QtCore.Qt.UserRole
_WAV_ROLE = QtCore.Qt.UserRole + 1


def age_rgb(age_s: float) -> tuple[int, int, int]:
    for limit, rgb in AGE_COLORS:
        if limit is None or age_s < limit:
            return rgb
    return AGE_COLORS[-1][1]


class _Item(QtWidgets.QTableWidgetItem):
    """A cell that sorts by a stored key (numbers as numbers)."""

    def __lt__(self, other):
        a, b = self.data(_SORT_ROLE), other.data(_SORT_ROLE)
        if a is None or b is None:
            return super().__lt__(other)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


def _cell(text: str, key, align=None, tip: str | None = None) -> _Item:
    it = _Item(text)
    it.setData(_SORT_ROLE, key)
    it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
    if align is not None:
        it.setTextAlignment(align)
    if tip:
        it.setToolTip(tip)
    return it


class RosterTable(QtWidgets.QTableWidget):
    """The decode table.  ``clear()`` empties the rows (keeps the headers)."""

    #: Emitted after rows are added or removed (for the window's count line).
    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(list(COLUMNS))
        for i, tip in enumerate(COLUMN_TIPS):
            self.horizontalHeaderItem(i).setToolTip(tip + "\n\nClick to sort.")
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(True)
        hdr.setSortIndicatorShown(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setFont(QtGui.QFont("Monospace", 10))
        self.setStyleSheet(
            "QTableWidget { background: #ffffff; alternate-background-color: #f4f6f9;"
            " color: #000000; gridline-color: #e0e0e0; }"
            "QHeaderView::section { background: #eceff3; color: #202020;"
            " padding: 3px 6px; border: 0; border-right: 1px solid #d0d4da;"
            " border-bottom: 1px solid #c0c4ca; font-weight: bold; }")
        self.setSortingEnabled(True)
        self.sortItems(COL_UTC, QtCore.Qt.DescendingOrder)    # newest on top
        self.setColumnHidden(COL_POL, True)       # shown once a dual-pol decode arrives

    # Rows -------------------------------------------------------------------
    def clear(self) -> None:          # QTableWidget.clear() would drop the headers
        self.setRowCount(0)
        self.changed.emit()

    def count(self) -> int:
        return self.rowCount()

    def add_decode(self, *, epoch: float, utc_time: str, mode: str, radio_khz: float,
                   df_hz: int | None, message: str, snr: int | None = None,
                   theta_deg: float | None = None, wav_path: str | None = None,
                   now: float | None = None) -> int:
        """Add one decode; returns its row (after sorting)."""
        from .ft8_window import call_from_message
        now = time.time() if now is None else float(now)
        right = int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        center = int(QtCore.Qt.AlignCenter)
        cells = [
            _cell(utc_time, float(epoch)),
            _cell(mode, mode, center),
            _cell(f"{float(radio_khz):.3f}", float(radio_khz), right),
            _cell("" if df_hz is None else f"{int(df_hz):+d}" if mode == "MSK144"
                  else f"{int(df_hz)}", -1e9 if df_hz is None else int(df_hz), right),
            _cell("" if theta_deg is None else f"{int(round(theta_deg))}°",
                  -1.0 if theta_deg is None else float(theta_deg), right),
            _cell("" if snr is None else f"{int(snr):+d}",
                  -999 if snr is None else int(snr), right),
            _cell(call_from_message(message), call_from_message(message)),
            _cell(message, message, tip=message),
        ]
        cells[COL_UTC].setData(_EPOCH_ROLE, float(epoch))
        if wav_path:
            cells[COL_UTC].setData(_WAV_ROLE, str(wav_path))
        if theta_deg is not None and self.isColumnHidden(COL_POL):
            self.setColumnHidden(COL_POL, False)
        rgb = QtGui.QColor(*age_rgb(max(0.0, now - float(epoch))))
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.insertRow(0)
        for col, it in enumerate(cells):
            it.setForeground(rgb)
            self.setItem(0, col, it)
        self._trim()
        self.setSortingEnabled(sorting)      # re-sorts by the current column
        self.changed.emit()
        return cells[COL_UTC].row()

    def _trim(self) -> None:
        while self.rowCount() > MAX_ROWS:
            oldest, oldest_t = 0, None
            for r in range(self.rowCount()):
                t = self.item(r, COL_UTC).data(_EPOCH_ROLE)
                if oldest_t is None or (t is not None and t < oldest_t):
                    oldest, oldest_t = r, t
            self.removeRow(oldest)

    def epoch_at(self, row: int):
        it = self.item(row, COL_UTC)
        return None if it is None else it.data(_EPOCH_ROLE)

    def wav_at(self, row: int):
        it = self.item(row, COL_UTC)
        return None if it is None else it.data(_WAV_ROLE)

    def recolor(self, now: float | None = None) -> None:
        """Fade text by age (called every 15 s)."""
        now = time.time() if now is None else float(now)
        for r in range(self.rowCount()):
            t = self.epoch_at(r)
            if t is None:
                continue
            rgb = QtGui.QColor(*age_rgb(now - t))
            for c in range(self.columnCount()):
                it = self.item(r, c)
                if it is not None:
                    it.setForeground(rgb)

    def recent_count(self, seconds: float, mode: str | None = None,
                     now: float | None = None) -> int:
        """Decodes newer than ``seconds`` (optionally of one mode)."""
        now = time.time() if now is None else float(now)
        n = 0
        for r in range(self.rowCount()):
            t = self.epoch_at(r)
            if t is None or now - t > seconds:
                continue
            if mode is not None and self.item(r, COL_MODE).text() != mode:
                continue
            n += 1
        return n


def add_queue_record(table: RosterTable, result: dict, calling_freq_mhz: float) -> int:
    """Add one decoded record from ``engine._decode_queue``."""
    from .band_activity_window import record_time
    mode = str(result.get("mode") or "MSK144")
    radio_khz = float(result.get("radio_khz", 0.0) or 0.0)
    if mode == "MSK144" or result.get("audio_hz") is None:
        df = int(round((radio_khz - float(calling_freq_mhz) * 1000.0) * 1000.0))
    else:
        df = int(round(float(result["audio_hz"])))
    epoch = record_time(result)
    snr = result.get("jt9_snr")
    return table.add_decode(
        epoch=epoch, utc_time=str(result.get("utc_time") or
                                  time.strftime("%H:%M:%S", time.gmtime(epoch))),
        mode=mode, radio_khz=radio_khz, df_hz=df,
        message=str(result.get("message") or "?"),
        snr=int(snr) if snr is not None else None,
        theta_deg=result.get("theta_deg"), wav_path=result.get("wav_path"))


def _settings():
    from .visualizer import _SETTINGS
    return _SETTINGS


def setup_roster_window(self, view_action) -> None:
    """Build View → Roster; sets ``self._roster_win`` and ``self.decode_panel``."""
    from .ui import _PanelWindow

    win = _PanelWindow("WS Skim — Roster", view_action, self, GEOMETRY_KEY)
    win.setMinimumSize(520, 260)
    geo = _settings().value(GEOMETRY_KEY)
    if geo:
        win.restoreGeometry(geo)
    else:
        win.setGeometry(QtCore.QRect(480, 760, 900, 420))
    win.setStyleSheet("QWidget { background: #ffffff; color: #000000; }")
    layout = QtWidgets.QVBoxLayout(win)
    layout.setContentsMargins(6, 6, 6, 6)
    info = QtWidgets.QLabel("")
    info.setToolTip("Every decode from this radio, all modes.  Filters for needed "
                    "stations come later (see the WS Skim plan).")
    layout.addWidget(info)
    table = RosterTable(win)
    layout.addWidget(table, stretch=1)

    def _count():
        n = table.rowCount()
        info.setText(f"{n:,} decode{'s' if n != 1 else ''} · click a column title to sort "
                     "· right-click an MSK144 decode to open its recording")

    table.changed.connect(_count)
    _count()
    table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    table.customContextMenuRequested.connect(lambda pos: _context_menu(self, table, pos))

    win._roster_table = table
    win._roster_info = info
    self._roster_win = win
    self.decode_panel = table
    view_action.triggered.connect(lambda checked: win.show() if checked else win.hide())
    # The list used to be the main window, so it starts shown.
    if _settings().value(VISIBLE_KEY, True, type=bool):
        view_action.setChecked(True)
        win.show()
    else:
        view_action.setChecked(False)
        win.hide()


def _context_menu(eng, table: RosterTable, pos) -> None:
    from pathlib import Path
    from .ui import _open_analysis_window
    idx = table.indexAt(pos)
    if not idx.isValid():
        return
    wav_path = table.wav_at(idx.row())
    menu = QtWidgets.QMenu(table)
    if wav_path and Path(wav_path).exists():
        act = menu.addAction(f"Open in Analysis Window  ({Path(wav_path).name})")
        act.triggered.connect(lambda: _open_analysis_window(eng, wav_path))
    else:
        act = menu.addAction("(no saved recording for this decode)")
        act.setEnabled(False)
    menu.exec_(table.viewport().mapToGlobal(pos))
