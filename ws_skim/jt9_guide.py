# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Plain-language guidance when the jt9 decoder from WSJT-X is missing.

WS Skim keeps running without jt9: the SPD decoder is primary and jt9 is
only the fallback for candidates SPD misses.  No CLI or env-var hints here;
those belong in the developer notes.
"""

from __future__ import annotations

import os

JT9_MISSING_TITLE = "WSJT-X was not found"

JT9_MISSING_HTML = (
    "WS Skim uses the <b>jt9</b> decoder that comes with WSJT-X as a "
    "fallback, and did not find it on this computer.<br><br>"
    "Install WSJT-X from "
    "<a href='https://wsjt.sourceforge.io/'>wsjt.sourceforge.io</a>, "
    "then start WS Skim again.<br><br>"
    "You can keep going now; WS Skim's own decoder still runs."
)


def show_jt9_missing_dialog(parent=None) -> None:
    """Non-blocking for the caller's logic: shows a dialog, then returns."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return  # headless (tests/CI): nobody to click OK
    from PyQt5 import QtCore, QtWidgets

    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning,
                                JT9_MISSING_TITLE, JT9_MISSING_HTML,
                                QtWidgets.QMessageBox.Ok, parent)
    box.setTextFormat(QtCore.Qt.RichText)
    box.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
    box.exec_()
