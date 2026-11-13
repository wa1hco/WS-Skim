# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Desktop, dock, and taskbar identity for WS Skim windows.

GNOME matches a running window to its ``.desktop`` launcher using
``StartupWMClass`` and the X11 ``WM_CLASS`` property (or, on Wayland,
the app id from ``QGuiApplication.setDesktopFileName``). Windows groups
the pinned shortcut with the running window using an explicit
AppUserModelID.  Both the main entry point and the router GUI use this
module so they stay in sync with the generated launcher and the Inno
Setup shortcuts.

Qt 5's xcb plugin builds ``WM_CLASS`` as ``instance\\0class``:

- instance: ``RESOURCE_NAME`` if set, otherwise the basename of argv[0]
  (``ws_skim.py`` when launched as a script — which is why the dock
  showed a generic icon)
- class: ``QCoreApplication.applicationName()``, or a capitalised argv[0]
  when that is empty

This module sets both fields to ``APP_ID`` (``ws-skim``) so either
matching rule works, and sets the visible name with
``setApplicationDisplayName``.  ``RESOURCE_NAME`` and the Windows
AppUserModelID must be applied *before* ``QApplication`` is created;
the Qt name and icon are applied immediately after, before any window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Visible name in window titles, the menu, and the taskbar.
APP_DISPLAY_NAME = "WS Skim"
# X11 WM_CLASS (both fields), Wayland app id, and .desktop file stem.
APP_ID = "ws-skim"
DESKTOP_FILE_NAME = APP_ID  # without the .desktop suffix
# Must match packaging/windows/ws-skim.iss [Icons] AppUserModelID.
WINDOWS_APP_USER_MODEL_ID = "wa1hco.WSSkim"


def install_root() -> Path:
    """The folder that contains ``ws_skim.py`` and ``scripts/``."""
    # Prefer the same lookup the rest of the app uses, so a relocated
    # tree still finds its icons.  Fall back to this package's parent.
    try:
        from .user_paths import program_root
        return program_root()
    except Exception:
        return Path(__file__).resolve().parents[1]


def icon_paths(root: Path | None = None) -> list[Path]:
    """Bundled icon files that exist, largest / richest first.

    Paths are resolved from the install or checkout tree, never the
    process working directory.
    """
    base = root if root is not None else install_root()
    candidates = (
        base / "scripts" / "windows" / "assets" / "ws-skim.ico",
        base / "scripts" / "assets" / "ws-skim.png",
        base / "scripts" / "assets" / "ws-skim-128.png",
        base / "scripts" / "assets" / "ws-skim-64.png",
        base / "scripts" / "assets" / "ws-skim-48.png",
        base / "assets" / "icons" / "ws-skim.png",
    )
    return [p for p in candidates if p.is_file()]


def window_icon(root: Path | None = None):
    """A ``QIcon`` with every bundled size (and the Windows .ico)."""
    from PyQt5 import QtGui

    icon = QtGui.QIcon()
    for path in icon_paths(root):
        icon.addFile(str(path))
    return icon


def prepare_process() -> None:
    """Process-level identity.  Call before constructing ``QApplication``.

    On Windows this registers the AppUserModelID so the running window
    groups with the Start-menu / desktop shortcut.  On X11 it sets
    ``RESOURCE_NAME`` so Qt's xcb plugin uses a stable WM_CLASS
    instance name instead of the script filename.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                WINDOWS_APP_USER_MODEL_ID
            )
        except Exception:
            pass
    # Always set: a leftover RESOURCE_NAME would otherwise keep the
    # generic dock icon.  This is process identity, not a user setting.
    os.environ["RESOURCE_NAME"] = APP_ID


def apply(app) -> None:
    """Set the Qt application name, desktop file, and default window icon.

    Call after ``QApplication`` is created and before any window is
    shown.  Safe to call more than once.  Settings files are *not*
    keyed off ``applicationName`` (they use an explicit ini path), so
    changing it here does not move the user's configuration.
    """
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    if hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName(DESKTOP_FILE_NAME)
    icon = window_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
