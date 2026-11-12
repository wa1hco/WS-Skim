# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Human version plus git hash, e.g. ``0.1.4 (51b9fd9)``.

Installed copies have no ``.git``; the release build writes the hash to a
``BUILD`` file at the install root (see ``tools/dev/stage_release.py``).
A git checkout asks git directly.  Kept free of Qt/numpy imports.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent


def git_hash(root: Path = ROOT) -> str:
    """Short git hash, ``+`` suffixed if the tree is modified; '' if unknown."""
    build = root / "BUILD"
    if build.is_file():
        return build.read_text(encoding="utf-8").strip()
    try:
        h = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        return h + ("+" if dirty else "")
    except Exception:
        return ""


def version_string(root: Path = ROOT) -> str:
    """``0.1.4 (51b9fd9)``, or just ``0.1.4`` when no hash is available."""
    h = git_hash(root)
    return f"{__version__} ({h})" if h else __version__
