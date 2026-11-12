# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""jt9-missing guidance is a GUI message, not a CLI or env-var hint."""

from __future__ import annotations

from map144_app.jt9_guide import (
    JT9_INSTALL_HINT,
    JT9_MISSING_MESSAGE,
    JT9_MISSING_TITLE,
    show_jt9_missing_dialog,
)


def _assert_plain(text: str) -> None:
    lower = text.lower()
    assert "python -m" not in lower
    assert "pythonpath" not in lower
    assert "map144_jt9" not in lower
    assert "env var" not in lower
    assert "environment variable" not in lower
    assert "wsjt.sourceforge.io" in lower
    assert "wsjt-x" in lower


def test_message_explains_wsjtx_without_cli_hints():
    assert "WSJT-X" in JT9_MISSING_TITLE or "WSJT-X" in JT9_MISSING_MESSAGE
    _assert_plain(JT9_MISSING_MESSAGE)
    _assert_plain(JT9_INSTALL_HINT)


def test_dialog_uses_the_plain_message(monkeypatch):
    seen = {}

    def fake_warning(parent, title, text):
        seen["parent"] = parent
        seen["title"] = title
        seen["text"] = text
        return 0

    from PyQt5.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    show_jt9_missing_dialog(parent="win")
    assert seen["title"] == JT9_MISSING_TITLE
    assert seen["text"] == JT9_MISSING_MESSAGE
    assert seen["parent"] == "win"
    _assert_plain(seen["text"])
