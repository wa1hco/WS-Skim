# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Settings and log locations outside the program folder.

Linux uses the XDG directories (``~/.config/ws-skim`` and
``~/.local/state/ws-skim`` when unset). Windows uses ``%APPDATA%\\WS-Skim``
and ``%LOCALAPPDATA%\\WS-Skim``. Reinstalling or updating the program
does not touch these folders.

A one-time migration copies older in-tree logs (``MSK144/logs``) and
existing Qt / ``~/.config/map144`` settings into the new places.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "ws-skim"
WIN_APP_DIR_NAME = "WS-Skim"
SETTINGS_FILENAME = "settings.ini"

# Qt org/app used before this path change.
_LEGACY_QT_ORG = "RadioIQ"
_LEGACY_QT_APP = "RadioIQVisualizer"
_LEGACY_MAP144_CONFIG = "map144"

_migrated = False


def program_root(start: Path | None = None) -> Path:
    """The folder that contains ``map144.py`` and ``scripts/``."""
    p = Path(start or __file__).resolve()
    candidates = [p, *p.parents]
    for cand in candidates:
        if (cand / "map144.py").is_file() and (cand / "scripts").is_dir():
            return cand
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / WIN_APP_DIR_NAME
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / WIN_APP_DIR_NAME
    xdg = (os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".local" / "state" / APP_DIR_NAME


def log_dir() -> Path:
    return state_dir() / "logs"


def settings_file() -> Path:
    return config_dir() / SETTINGS_FILENAME


def _legacy_qt_conf() -> Path | None:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        ini = Path(base) / _LEGACY_QT_ORG / f"{_LEGACY_QT_APP}.ini"
        return ini if ini.is_file() else None
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    cfg = Path(xdg) if xdg else Path.home() / ".config"
    conf = cfg / _LEGACY_QT_ORG / f"{_LEGACY_QT_APP}.conf"
    return conf if conf.is_file() else None


def _legacy_map144_config() -> Path | None:
    if sys.platform.startswith("win"):
        return None
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    cfg = Path(xdg) if xdg else Path.home() / ".config"
    old = cfg / _LEGACY_MAP144_CONFIG
    return old if old.is_dir() else None


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    shutil.copy2(src, dest)


def _copy_tree_files(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            _copy_tree_files(item, target)
        elif not target.exists():
            shutil.copy2(item, target)


def _migrate_qt_keys(dest: Path) -> None:
    """Copy keys from the old RadioIQ QSettings store if dest is empty."""
    try:
        from PyQt5 import QtCore
    except ImportError:
        return
    if dest.is_file() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    old = QtCore.QSettings(_LEGACY_QT_ORG, _LEGACY_QT_APP)
    keys = old.allKeys()
    if not keys:
        return
    new = QtCore.QSettings(str(dest), QtCore.QSettings.IniFormat)
    if new.allKeys():
        return
    for key in keys:
        new.setValue(key, old.value(key))
    new.sync()


def migrate_user_data(root: Path | None = None) -> None:
    """Copy older settings and logs into the new locations once.

    Idempotent. Never deletes the source, and never overwrites a file
    that already exists in the new place.
    """
    global _migrated
    if _migrated:
        return
    _migrated = True
    cfg = config_dir()
    logs = log_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    dest = settings_file()
    legacy_conf = _legacy_qt_conf()
    if legacy_conf is not None and not dest.exists():
        _copy_file(legacy_conf, dest)
    if not dest.exists() or dest.stat().st_size == 0:
        _migrate_qt_keys(dest)

    old_map = _legacy_map144_config()
    if old_map is not None:
        _copy_tree_files(old_map, cfg)

    tree = program_root(root)
    old_logs = tree / "MSK144" / "logs"
    if old_logs.is_dir():
        for item in old_logs.iterdir():
            if item.is_file() and not (logs / item.name).exists():
                shutil.copy2(item, logs / item.name)


def qsettings():
    """Qt settings stored in the user config directory."""
    migrate_user_data()
    from PyQt5 import QtCore
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    return QtCore.QSettings(str(path), QtCore.QSettings.IniFormat)


class LazySettings:
    """QSettings stand-in that opens the store on first use.

    Modules bind ``_SETTINGS`` at import time; deferring the real
    QSettings until first use lets the one-time migration run first and
    keeps importing this module free of Qt.
    """

    def __init__(self):
        self._inner = None

    def _get(self):
        if self._inner is None:
            self._inner = qsettings()
        return self._inner

    def value(self, *args, **kwargs):
        return self._get().value(*args, **kwargs)

    def setValue(self, *args, **kwargs):
        return self._get().setValue(*args, **kwargs)

    def sync(self):
        return self._get().sync()

    def fileName(self):
        return self._get().fileName()


#: The app-wide settings store (shared by the main window and analysis windows).
SETTINGS = LazySettings()


def redirect_missing_console() -> None:
    """Give pythonw.exe (no console) somewhere to write.

    Under pythonw ``sys.stdout``/``sys.stderr`` are None, so print(),
    logging's StreamHandler and faulthandler would fail.  Send them to
    ``console.log`` in the log folder instead.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    stream = open(logs / "console.log", "a", buffering=1, encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


# For tests: allow a second migration after they change HOME / XDG.
def _reset_migration_flag() -> None:
    global _migrated
    _migrated = False
