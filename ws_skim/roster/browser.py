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


"""Open the roster in a dedicated browser app window (Hub-style)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


def should_open_browser() -> bool:
    """False in offscreen CI / no display, so tests never launch a browser."""
    plat = (os.environ.get("QT_QPA_PLATFORM") or "").strip().lower()
    if plat in {"offscreen", "minimal", "vnc"}:
        return False
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return True


def _which(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        pf = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        pf86 = Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
        local = Path(os.environ.get("LOCALAPPDATA") or "")
        extra = {
            "msedge.exe": [
                pf / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ],
            "chrome.exe": [
                pf / "Google" / "Chrome" / "Application" / "chrome.exe",
                pf86 / "Google" / "Chrome" / "Application" / "chrome.exe",
                local / "Google" / "Chrome" / "Application" / "chrome.exe",
            ],
            "firefox.exe": [
                pf / "Mozilla Firefox" / "firefox.exe",
                pf86 / "Mozilla Firefox" / "firefox.exe",
            ],
        }
        for name in names:
            for cand in extra.get(name, []):
                if cand.is_file():
                    return str(cand)
    return None


def _chromium_executable() -> str | None:
    if sys.platform == "win32":
        return _which(["msedge.exe", "chrome.exe", "chromium.exe"])
    return _which([
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "microsoft-edge", "microsoft-edge-stable",
    ])


def build_chromium_command(executable: str, url: str, *,
                           width: int = 1000, height: int = 700,
                           x: int | None = None, y: int | None = None) -> list[str]:
    cmd = [
        executable,
        f"--app={url}",
        f"--window-size={int(width)},{int(height)}",
        "--disable-features=Translate,TranslateUI",
    ]
    if x is not None and y is not None:
        cmd.append(f"--window-position={int(x)},{int(y)}")
    return cmd


def _firefox_command(url: str, *, width: int = 1000, height: int = 700) -> list[str] | None:
    exe = _which(["firefox", "firefox.exe"])
    if not exe:
        return None
    return [exe, "--new-window", url, "-width", str(int(width)), "-height", str(int(height))]


def _open_system_browser(url: str) -> str:
    if sys.platform.startswith("linux"):
        xdg = shutil.which("xdg-open")
        if xdg:
            subprocess.Popen([xdg, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "browser"
    webbrowser.open(url)
    return "browser"


def open_roster_url(url: str, *, width: int = 1000, height: int = 700,
                    x: int | None = None, y: int | None = None) -> str:
    """Open ``url`` in Chrome/Edge ``--app=``, else Firefox, else the system browser.

    Returns a short mode name: ``app``, ``firefox``, or ``browser``.
    """
    exe = _chromium_executable()
    if exe:
        cmd = build_chromium_command(exe, url, width=width, height=height, x=x, y=y)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if proc.poll() is None:
                return "app"
        except OSError:
            pass
    ff = _firefox_command(url, width=width, height=height)
    if ff:
        try:
            proc = subprocess.Popen(ff, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if proc.poll() is None:
                return "firefox"
        except OSError:
            pass
    return _open_system_browser(url)
