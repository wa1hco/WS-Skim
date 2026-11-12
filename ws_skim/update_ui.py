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
"""Update dialogs: a check shortly after startup and Help → Check for Updates.

The logic (GitHub query, download, checksum, installer hand-off) is in
``update_check``; this module only adds Qt threads and dialogs.
"""
from __future__ import annotations

import html
import logging

from PyQt5 import QtCore, QtWidgets

from . import update_check as uc
from .user_paths import SETTINGS

log = logging.getLogger(__name__)

#: Delay before the automatic check, so startup is not slowed.
STARTUP_CHECK_DELAY_MS = 15_000
_SKIP_KEY = "updates/skip_tag"
_AUTO_KEY = "updates/auto_check"


class _CheckThread(QtCore.QThread):
    done = QtCore.pyqtSignal(object, str)      # Release | None, error text

    def run(self):
        try:
            self.done.emit(uc.check_for_update(), "")
        except Exception as e:                  # offline, rate-limited, ...
            self.done.emit(None, str(e) or e.__class__.__name__)


class _DownloadThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int)
    done = QtCore.pyqtSignal(object, str)      # Path | None, error text

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self._release = release
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            path = uc.download(self._release, progress=self.progress.emit,
                               cancelled=lambda: self._cancel)
            self.done.emit(path, "")
        except uc.Cancelled:
            self.done.emit(None, "cancelled")
        except Exception as e:
            self.done.emit(None, str(e) or e.__class__.__name__)


def setup_update_check(self, help_menu: QtWidgets.QMenu) -> None:
    """Add Help → Check for Updates and schedule the startup check.

    ``self`` is the main window.
    """
    act = QtWidgets.QAction("Check for Updates…", self)
    act.triggered.connect(lambda: _start_check(self, manual=True))
    help_menu.addAction(act)
    self._update_check_action = act
    if uc.installed_tag() and _auto_check_enabled():
        QtCore.QTimer.singleShot(STARTUP_CHECK_DELAY_MS,
                                 lambda: _start_check(self, manual=False))


def _auto_check_enabled() -> bool:
    v = SETTINGS.value(_AUTO_KEY, True)
    return str(v).lower() not in ("false", "0", "no")


def _start_check(win, manual: bool) -> None:
    current = uc.installed_tag()
    if not current:
        if manual:
            QtWidgets.QMessageBox.information(
                win, "Check for Updates",
                "This copy of WS Skim was not installed from a release, so it "
                "does not update itself.\n\nReleases are at\n" + uc.RELEASES_PAGE)
        return
    if getattr(win, "_update_thread", None) is not None:
        return                                   # a check is already running
    t = _CheckThread(win)
    win._update_thread = t

    def finished(release, err):
        win._update_thread = None
        _on_check_done(win, release, err, manual, current)

    t.done.connect(finished)
    t.finished.connect(t.deleteLater)
    t.start()


def _on_check_done(win, release, err, manual, current) -> None:
    if err:
        log.info("update check failed: %s", err)
        if manual:
            QtWidgets.QMessageBox.warning(
                win, "Check for Updates",
                f"Could not reach GitHub to check for updates.\n\n{err}")
        return
    if release is None:
        log.info("update check: %s is current", current)
        if manual:
            QtWidgets.QMessageBox.information(
                win, "Check for Updates", f"WS Skim {current} is the newest version.")
        return
    log.info("update check: %s available (have %s)", release.tag, current)
    if not manual and str(SETTINGS.value(_SKIP_KEY, "")) == release.tag:
        return
    _offer(win, release, current)


def _offer(win, release, current) -> None:
    notes = release.notes.strip()
    if len(notes) > 800:
        notes = notes[:800].rstrip() + " …"
    box = QtWidgets.QMessageBox(win)
    box.setWindowTitle("WS Skim update")
    box.setIcon(QtWidgets.QMessageBox.Information)
    box.setTextFormat(QtCore.Qt.RichText)
    kind = "test release" if release.prerelease else "release"
    box.setText(
        f"<b>WS Skim {html.escape(release.tag)}</b> is available "
        f"(a {kind}). You have {html.escape(current)}.<br><br>"
        f'<a href="{html.escape(release.html_url)}">Release notes on GitHub</a>')
    if notes:
        box.setInformativeText(notes)
    box.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
    update_btn = box.addButton("Update Now", QtWidgets.QMessageBox.AcceptRole)
    box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
    skip_btn = box.addButton("Skip This Version", QtWidgets.QMessageBox.DestructiveRole)
    box.setDefaultButton(update_btn)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is skip_btn:
        SETTINGS.setValue(_SKIP_KEY, release.tag)
    elif clicked is update_btn:
        _download_and_install(win, release)


def _download_and_install(win, release) -> None:
    dlg = QtWidgets.QProgressDialog(
        f"Downloading WS Skim {release.tag}…", "Cancel", 0, 100, win)
    dlg.setWindowTitle("WS Skim update")
    dlg.setWindowModality(QtCore.Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    t = _DownloadThread(release, win)
    win._update_download = t
    cancelled = {"v": False}

    def on_progress(done, total):
        if total > 0 and not cancelled["v"]:
            dlg.setValue(min(100, int(done * 100 / total)))

    def on_cancel():
        cancelled["v"] = True
        t.cancel()

    def finished(path, err):
        win._update_download = None
        # hide(), not close(): QProgressDialog.closeEvent emits canceled().
        dlg.canceled.disconnect(on_cancel)
        dlg.hide()
        dlg.deleteLater()
        if cancelled["v"]:
            return
        if err or path is None:
            QtWidgets.QMessageBox.warning(
                win, "WS Skim update",
                f"The update could not be downloaded.\n\n{err}\n\n"
                f"You can download it yourself from\n{uc.RELEASES_PAGE}")
            return
        _hand_off(win, release, path)

    t.progress.connect(on_progress)
    t.done.connect(finished)
    t.finished.connect(t.deleteLater)
    dlg.canceled.connect(on_cancel)
    t.start()
    dlg.show()


def _hand_off(win, release, path) -> None:
    ok = QtWidgets.QMessageBox.question(
        win, "WS Skim update",
        f"WS Skim will close, install {release.tag}, and start again.\n"
        "This takes a minute or two. Your settings are kept.",
        QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        QtWidgets.QMessageBox.Ok)
    if ok != QtWidgets.QMessageBox.Ok:
        return
    try:
        uc.start_install(path)
    except Exception as e:
        log.exception("could not start the installer")
        QtWidgets.QMessageBox.warning(
            win, "WS Skim update",
            f"The installer could not be started.\n\n{e}\n\n"
            f"It was downloaded to\n{path}")
        return
    log.info("update: handed off to installer %s; quitting", path)
    win.close()
    QtWidgets.QApplication.quit()
