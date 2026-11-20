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

"""Standard directories used to find WSJT-X logs and cty.dat."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_home() -> Path:
    if sys.platform == "win32":
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local:
            return Path(local)
        return Path.home() / "AppData" / "Local"
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def config_home() -> Path:
    if sys.platform == "win32":
        return data_home()
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def installed_dirs(home: Path | None = None) -> list[Path]:
    """WSJT-X data directories under the data home (``WSJT-X`` and ``WSJT-X - NAME``)."""
    root = home or data_home()
    if not root.is_dir():
        return []
    found = []
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if child.name == "WSJT-X" or child.name.startswith("WSJT-X - "):
                found.append(child)
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name.lower())


def wrig_instance_dirs(home: Path | None = None) -> list[Path]:
    """wrig instance folders (``~/.config/wrig/instances/<name>/``)."""
    root = (home or config_home()) / "wrig" / "instances"
    if not root.is_dir():
        return []
    try:
        return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return []


def shipped_cty_dat() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "cty.dat"
