# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Plain-language guidance when the jt9 decoder from WSJT-X is missing."""

from __future__ import annotations

JT9_MISSING_TITLE = "WSJT-X was not found"

JT9_MISSING_MESSAGE = (
    "WS Skim uses the jt9 decoder that comes with WSJT-X, and did not "
    "find it on this computer.\n\n"
    "Install WSJT-X from https://wsjt.sourceforge.io/, then start WS Skim "
    "again if you want that decoder as a fallback.\n\n"
    "You can keep going now. Some decodes still work without it."
)

JT9_INSTALL_HINT = (
    "Install WSJT-X from https://wsjt.sourceforge.io/. "
    "WS Skim will use its jt9 decoder when it is present. "
    "You can still open WS Skim now."
)


def show_jt9_missing_dialog(parent=None) -> None:
    """Show a GUI message about installing WSJT-X. No CLI or env-var hints."""
    from PyQt5 import QtWidgets

    QtWidgets.QMessageBox.warning(
        parent,
        JT9_MISSING_TITLE,
        JT9_MISSING_MESSAGE,
    )
