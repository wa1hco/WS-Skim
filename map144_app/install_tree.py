# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Where a downloaded WS Skim lives on Linux, and how a new copy replaces it.

A release download is unpacked wherever the browser put it (often
Downloads). **Install WS Skim** copies that tree to one fixed place,
``$XDG_DATA_HOME/ws-skim`` (``~/.local/share/ws-skim`` when unset), so the
download can be deleted. A git checkout is a developer copy and is never
copied: its icons point at the checkout.

Replacing a copy is a swap, not an overwrite: the new tree is built next
to the old one, then two renames put it in place, so a failure leaves the
old copy working. The previous copy is kept at ``ws-skim.previous``.
Settings and logs are not in the tree. An existing ``.venv`` is carried
over so an update does not have to download every package again.

Stdlib only, and no window library, so the installer script can run it
before Qt is installed.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

INSTALL_DIR_NAME = "ws-skim"
PREVIOUS_SUFFIX = ".previous"
_SKIP = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".git", ".venv", "MSK144/logs",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "*.egg-info",
)


def data_home() -> Path:
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def install_dir() -> Path:
    """The one place a release copy lives on this PC."""
    return data_home() / INSTALL_DIR_NAME


def previous_dir(dest: Path | None = None) -> Path:
    """The copy an update replaced, kept so the previous tree is still there."""
    d = Path(dest) if dest is not None else install_dir()
    return d.with_name(d.name + PREVIOUS_SUFFIX)


def is_git_tree(root: Path) -> bool:
    return (Path(root) / ".git").exists()


def is_program_tree(root: Path) -> bool:
    root = Path(root)
    return (root / "map144.py").is_file() and (
        root / "scripts" / "start-ws-skim.sh"
    ).is_file()


def _same(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def swap_in(new_tree: Path, dest: Path, *, keep_previous: bool = True) -> Path:
    """Put ``new_tree`` at ``dest``. The old copy's ``.venv`` comes along.

    With ``keep_previous`` the old copy is left at ``<dest>.previous`` (one
    only); otherwise it is deleted. Returns ``dest``.
    """
    new_tree = Path(new_tree)
    dest = Path(dest)
    if not is_program_tree(new_tree):
        raise ValueError(f"not a WS Skim program folder: {new_tree}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    old_venv = dest / ".venv"
    if old_venv.is_dir() and not (new_tree / ".venv").exists():
        shutil.copytree(old_venv, new_tree / ".venv", symlinks=True)
    parked: Path | None = None
    if dest.exists():
        if keep_previous:
            parked = previous_dir(dest)
            if parked.exists():
                shutil.rmtree(parked)
        else:
            parked = Path(tempfile.mkdtemp(prefix=f".{dest.name}-old-", dir=dest.parent))
            parked.rmdir()
        os.replace(dest, parked)
    try:
        os.replace(new_tree, dest)
    except OSError:
        if parked is not None and parked.exists() and not dest.exists():
            os.replace(parked, dest)
        raise
    if parked is not None and not keep_previous:
        shutil.rmtree(parked, ignore_errors=True)
    return dest


def install_copy(source: Path, dest: Path | None = None) -> Path:
    """Copy an unpacked release tree to the install place. Returns the place.

    Running it from the installed copy itself changes nothing.
    """
    source = Path(source).resolve()
    target = Path(dest) if dest is not None else install_dir()
    if not is_program_tree(source):
        raise ValueError(f"not a WS Skim program folder: {source}")
    if _same(source, target):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-new-", dir=target.parent))
    try:
        tree = stage / "tree"
        shutil.copytree(source, tree, symlinks=True, ignore=_SKIP)
        swap_in(tree, target, keep_previous=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return target


def main(argv: list[str] | None = None) -> int:
    """Used by scripts/add-ws-skim-to-desktop.sh: print where the program is."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: install_tree.py <unpacked folder>\n")
        return 2
    source = Path(args[0])
    if is_git_tree(source):
        print(source.resolve())
        return 0
    try:
        print(install_copy(source))
    except (OSError, ValueError) as e:
        sys.stderr.write(f"{e}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
