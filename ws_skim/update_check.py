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
"""Check GitHub for a newer WS Skim release, download it, and install it.

No Qt here; ``update_ui`` adds the dialogs.

An installed copy has a ``RELEASE`` file holding its tag (written by
``tools/dev/stage_release.py``).  A copy without one (a git checkout, or
an install from before this feature) never offers updates.

Which releases count: a copy installed from a prerelease (``v0.1.4-test.2``)
is offered prereleases and full releases; a copy installed from a full
release (``v0.1.4``) is offered full releases only.

Updating downloads the installer for this platform, checks it against the
release's ``SHA256SUMS.txt``, then starts a small helper script that waits
for WS Skim to exit, runs the installer over this copy, and starts WS Skim
again.  Settings and logs live outside the program folder, so they stay.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

GITHUB_REPO = "wa1hco/WS-Skim"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
USER_AGENT = "WS-Skim-update-check"

WINDOWS_ASSET = "WS-Skim-Setup.exe"
LINUX_ASSET = "ws-skim-linux-install.sh"
SUMS_ASSET = "SHA256SUMS.txt"

ROOT = Path(__file__).resolve().parent.parent

_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$")


# ── Versions ────────────────────────────────────────────────────────────────

def tag_key(tag: str) -> tuple | None:
    """Sort key for a release tag, or None if it is not ``vX.Y.Z[-suffix]``.

    ``v0.1.4-test.1`` < ``v0.1.4-test.2`` < ``v0.1.4-test.10`` < ``v0.1.4``
    < ``v0.1.5-rc.1``: a suffix marks a prerelease of that version, and
    numeric parts of the suffix compare as numbers.
    """
    m = _TAG.match((tag or "").strip())
    if not m:
        return None
    base = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    suffix = m.group(4)
    if not suffix:
        return base + (1, ())
    parts = tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p.lower())
        for p in re.split(r"[.]", suffix)
    )
    return base + (0, parts)


def is_prerelease_tag(tag: str) -> bool:
    key = tag_key(tag)
    return key is not None and key[3] == 0


def installed_tag(root: Path = ROOT) -> str:
    """The release tag this copy was installed from, or '' if unknown."""
    f = root / "RELEASE"
    try:
        return f.read_text(encoding="utf-8").strip() if f.is_file() else ""
    except OSError:
        return ""


# ── Releases ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Release:
    tag: str
    name: str
    prerelease: bool
    html_url: str
    notes: str
    installer_name: str
    installer_url: str
    installer_size: int
    sums_url: str


def platform_asset(platform: str | None = None) -> str | None:
    """Installer file name for this OS, or None where there is no installer."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return WINDOWS_ASSET
    if platform.startswith("linux"):
        return LINUX_ASSET
    return None


def parse_releases(data: list, asset_name: str) -> list[Release]:
    """Turn the GitHub releases JSON into Releases that carry our installer."""
    out = []
    for r in data or []:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        tag = r.get("tag_name") or ""
        if tag_key(tag) is None:
            continue
        assets = {a.get("name"): a for a in r.get("assets") or [] if isinstance(a, dict)}
        inst = assets.get(asset_name)
        sums = assets.get(SUMS_ASSET)
        if not inst or not sums:
            continue          # installers not attached (yet): not installable
        out.append(Release(
            tag=tag,
            name=r.get("name") or tag,
            prerelease=bool(r.get("prerelease")) or is_prerelease_tag(tag),
            html_url=r.get("html_url") or RELEASES_PAGE,
            notes=r.get("body") or "",
            installer_name=asset_name,
            installer_url=inst.get("browser_download_url") or "",
            installer_size=int(inst.get("size") or 0),
            sums_url=sums.get("browser_download_url") or "",
        ))
    return out


def pick_update(current_tag: str, releases: list[Release],
                include_prereleases: bool | None = None) -> Release | None:
    """The newest release that is newer than ``current_tag``, or None."""
    cur = tag_key(current_tag)
    if cur is None:
        return None
    if include_prereleases is None:
        include_prereleases = is_prerelease_tag(current_tag)
    best = None
    for r in releases:
        if r.prerelease and not include_prereleases:
            continue
        key = tag_key(r.tag)
        if key is None or key <= cur:
            continue
        if best is None or key > tag_key(best.tag):
            best = r
    return best


def _get(url: str, timeout: float) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT,
                                "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=timeout) as resp:   # noqa: S310 (fixed https URL)
        return resp.read()


def fetch_releases(asset_name: str, timeout: float = 10.0) -> list[Release]:
    return parse_releases(json.loads(_get(RELEASES_API, timeout)), asset_name)


def check_for_update(root: Path = ROOT, timeout: float = 10.0,
                     include_prereleases: bool | None = None) -> Release | None:
    """Ask GitHub for a newer release for this copy.  Raises on network errors."""
    current = installed_tag(root)
    asset = platform_asset()
    if not current or not asset:
        return None
    return pick_update(current, fetch_releases(asset, timeout), include_prereleases)


# ── Download and install ────────────────────────────────────────────────────

def expected_sha256(sums_text: str, name: str) -> str | None:
    """The SHA-256 for ``name`` in a ``sha256sum``-style listing."""
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def download(release: Release, dest_dir: Path | None = None,
             progress: Callable[[int, int], None] | None = None,
             timeout: float = 30.0) -> Path:
    """Download and verify the installer; return its path.

    Raises ValueError if the checksum is missing or does not match.
    """
    dest_dir = Path(dest_dir or tempfile.mkdtemp(prefix="ws-skim-update-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    want = expected_sha256(_get(release.sums_url, timeout).decode("utf-8", "replace"),
                           release.installer_name)
    if not want:
        raise ValueError(f"{SUMS_ASSET} has no entry for {release.installer_name}")
    path = dest_dir / release.installer_name
    h = hashlib.sha256()
    req = Request(release.installer_url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp, open(path, "wb") as out:  # noqa: S310
        total = int(resp.headers.get("Content-Length") or release.installer_size or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != want:
        path.unlink(missing_ok=True)
        raise ValueError(f"checksum mismatch for {release.installer_name}")
    return path


def _windows_script(installer: Path, root: Path, pid: int, log: Path) -> str:
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    return "\r\n".join([
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && '
        "(ping -n 2 127.0.0.1 >nul & goto wait)",
        f'"{installer}" /SILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{log}"',
        f'start "" "{pythonw}" "{root / "ws_skim.py"}"',
        "",
    ])


def _linux_script(installer: Path, root: Path, pid: int, log: Path) -> str:
    q = lambda p: "'" + str(p).replace("'", "'\\''") + "'"   # noqa: E731
    return "\n".join([
        "#!/usr/bin/env bash",
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done",
        f"bash {q(installer)} --dest {q(root)} >> {q(log)} 2>&1",
        f"setsid {q(root / 'scripts' / 'run.sh')} >/dev/null 2>&1 < /dev/null &",
        "",
    ])


def start_install(installer: Path, root: Path = ROOT, pid: int | None = None,
                  log: Path | None = None, platform: str | None = None) -> Path:
    """Start the helper that installs once WS Skim (``pid``) has exited.

    The caller quits the app right after this returns.  Returns the helper
    script's path.
    """
    platform = platform or sys.platform
    pid = pid or os.getpid()
    log = log or (installer.parent / "update.log")
    if platform.startswith("win"):
        script = installer.parent / "ws-skim-update.cmd"
        script.write_text(_windows_script(installer, root, pid, log), encoding="utf-8")
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags,
                         close_fds=True)
    else:
        script = installer.parent / "ws-skim-update.sh"
        script.write_text(_linux_script(installer, root, pid, log), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["bash", str(script)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    return script
