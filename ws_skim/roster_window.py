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
"""View → Roster: Hub-style browser roster (needed badges, filters, Ctrl+/-).

The radio and decoding path is unchanged.  ``engine.decode_panel`` is an
in-memory decode list so status-bar rates and source-start clear still work.
View → Roster starts a loopback HTTP server and opens the page in a Chrome
or Edge app window (Firefox or the system browser if those are missing).
"""
from __future__ import annotations

import json

from .roster.browser import open_roster_url, should_open_browser
from .roster.model import RosterSession
from .roster.server import RosterServer

#: Settings keys.
GEOMETRY_KEY = "roster_geometry"
VISIBLE_KEY = "roster_visible"
# Wait this long at startup for an already-open roster window to reconnect.
REOPEN_WAIT_MS = 5000
WATCH_MS = 1000
# No page connected for this many seconds means the user closed the window.
CLOSED_AFTER_TICKS = 10
LOG_PATH_KEY = "roster_log_path"
CHECKLIST_KEY = "roster_checklist"
WIN_X_KEY = "roster_win_x"
WIN_Y_KEY = "roster_win_y"
WIN_W_KEY = "roster_win_w"
WIN_H_KEY = "roster_win_h"

DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 700


def _settings():
    from .visualizer import _SETTINGS
    return _SETTINGS


def _station_from_settings():
    s = _settings()
    return (
        str(s.value("reporting_mycall", "") or ""),
        str(s.value("reporting_mygrid", "") or ""),
    )


def _load_checklist(s):
    raw = s.value(CHECKLIST_KEY, "", type=str)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, (dict, list)) else None


class _Chooser(object):
    def __init__(self, parent, session, settings):
        from PyQt5 import QtCore

        class _Bridge(QtCore.QObject):
            requested = QtCore.pyqtSignal()

        self.session = session
        self.settings = settings
        self.parent = parent
        self._bridge = _Bridge(parent)
        self._bridge.requested.connect(self._open, QtCore.Qt.QueuedConnection)
        # Keep a reference on the parent so it is not collected.
        parent._roster_log_chooser = self

    def request(self):
        self._bridge.requested.emit()

    def _open(self):
        from PyQt5 import QtWidgets
        start = self.session.log.path or ""
        path, _ok = QtWidgets.QFileDialog.getOpenFileName(
            self.parent,
            "Choose a log (ADIF)",
            start,
            "ADIF logs (*.adi *.adif);;All files (*)",
        )
        if not path:
            return
        self.session.set_log(path)
        self.settings.setValue(LOG_PATH_KEY, path)


class _RosterHandle:
    """Stand-in for the old Qt roster window (visibility only)."""

    def __init__(self, action, server):
        self._action = action
        self._server = server

    def isVisible(self):
        return bool(self._action.isChecked())

    def hide(self):
        self._action.blockSignals(True)
        self._action.setChecked(False)
        self._action.blockSignals(False)

    def show(self):
        self._action.blockSignals(True)
        self._action.setChecked(True)
        self._action.blockSignals(False)

    def windowTitle(self):
        return "WS Skim — Roster"


def _geom(s):
    def _num(key, default):
        try:
            v = s.value(key, default)
            return int(v)
        except (TypeError, ValueError):
            return default
    return {
        "x": _num(WIN_X_KEY, 40),
        "y": _num(WIN_Y_KEY, 80),
        "width": _num(WIN_W_KEY, DEFAULT_WIDTH),
        "height": _num(WIN_H_KEY, DEFAULT_HEIGHT),
    }


def _open_page(server, s) -> str:
    g = _geom(s)
    return open_roster_url(
        server.url, width=g["width"], height=g["height"], x=g["x"], y=g["y"],
    )


def setup_roster_window(self, view_action) -> None:
    """Build View → Roster: start the local server and open a browser window."""
    s = _settings()
    call, grid = _station_from_settings()
    log_path = str(s.value(LOG_PATH_KEY, "") or "")
    session = RosterSession(
        my_call=call, my_grid=grid,
        checklist=_load_checklist(s),
        log_path=log_path,
        station_getter=_station_from_settings,
    )
    chooser = _Chooser(self, session, s)

    def _persist_checklist():
        try:
            s.setValue(CHECKLIST_KEY, json.dumps(session.rules.items))
        except Exception:
            pass

    _orig_set = session.set_checklist

    def _set_and_save(items):
        _orig_set(items)
        _persist_checklist()

    session.set_checklist = _set_and_save  # type: ignore[method-assign]

    _orig_log = session.set_log

    def _set_log(path, title=None):
        ok = _orig_log(path, title=title)
        if ok:
            s.setValue(LOG_PATH_KEY, str(path))
        return ok

    session.set_log = _set_log  # type: ignore[method-assign]

    from . import click_to_work
    server = RosterServer(session, choose_log=chooser.request,
                          work=lambda body: click_to_work.request(self, body),
                          work_state=lambda: click_to_work.state(self))
    server.start()

    self._roster_session = session
    self._roster_server = server
    self.decode_panel = session.decodes
    handle = _RosterHandle(view_action, server)
    self._roster_win = handle

    visible = s.value(VISIBLE_KEY, True, type=bool)
    view_action.setChecked(bool(visible))
    view_action.setToolTip(
        "Needed-station roster in a browser window (filters, badges, Ctrl +/- zoom)"
    )

    def _on_toggle(checked):
        s.setValue(VISIBLE_KEY, bool(checked))
        if checked and should_open_browser() and server.client_count == 0:
            _open_page(server, s)

    view_action.triggered.connect(_on_toggle)

    # A roster window left open from the last run (for example across an
    # upgrade) reconnects by itself within a few seconds, so wait before
    # deciding to open one.  Closing the roster window unticks View → Roster,
    # so WS Skim doesn't reopen a window the user closed.
    state = {"seen": False, "idle": 0}

    def _startup_open():
        if (view_action.isChecked() and should_open_browser()
                and server.client_count == 0):
            _open_page(server, s)

    def _watch_clients():
        if server.client_count > 0:
            state["seen"] = True
            state["idle"] = 0
            if not view_action.isChecked():
                handle.show()
                s.setValue(VISIBLE_KEY, True)
            return
        if not state["seen"] or not view_action.isChecked():
            return
        state["idle"] += 1
        if state["idle"] >= CLOSED_AFTER_TICKS:
            state["seen"] = False
            state["idle"] = 0
            handle.hide()
            s.setValue(VISIBLE_KEY, False)

    from PyQt5 import QtCore
    timer = QtCore.QTimer(self)
    timer.setInterval(WATCH_MS)
    timer.timeout.connect(_watch_clients)
    timer.start()
    self._roster_watch_timer = timer
    if visible:
        QtCore.QTimer.singleShot(REOPEN_WAIT_MS, _startup_open)
