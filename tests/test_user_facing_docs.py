# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""User-facing docs do not ask anyone to type CLI flags, env vars, or python -m."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_FORBIDDEN = (
    re.compile(r"python\s+-m\b", re.I),
    re.compile(r"\bPYTHONPATH\b"),
    re.compile(r"\bMAP144_[A-Z0-9_]+\b"),
    re.compile(r"\$env:[A-Za-z0-9_]+"),
    re.compile(r"^export\s+[A-Z0-9_]+", re.M),
)


def _user_facing(text: str) -> str:
    parts = re.split(r"(?m)^#{2,3} Developer\s*$", text, maxsplit=1)
    return parts[0]


def _check(path: Path) -> None:
    text = _user_facing(path.read_text(encoding="utf-8"))
    for pat in _FORBIDDEN:
        m = pat.search(text)
        assert not m, f"{path} user-facing text contains {pat.pattern!r}: {m.group(0)!r}"


def test_install_and_readme_are_double_click_for_users():
    install = ROOT / "INSTALL.md"
    readme = ROOT / "README.md"
    linux = ROOT / "README-Linux.md"
    windows = ROOT / "README-Windows.md"
    for path in (install, readme, linux, windows):
        assert path.is_file(), path
        _check(path)
    user_install = _user_facing(install.read_text(encoding="utf-8"))
    assert "Install WS Skim" in user_install
    assert "Install-WsSkim.cmd" in user_install
    assert "Allow Launching" in user_install
    assert "sudo apt" not in user_install
    assert "./scripts/install.sh" not in user_install
    assert "scripts/install-linux.sh" not in user_install
