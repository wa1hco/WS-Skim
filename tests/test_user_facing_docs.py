# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""User-facing docs never ask anyone to type CLI flags, env vars, or python -m.

Adapted from PR #6.  Developer notes live in docs/dev/ and are exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

USER_FACING = (
    ROOT / "README.md",
    ROOT / "docs" / "flexradio-setup.md",
)

_FORBIDDEN = (
    re.compile(r"python\s+-m\b", re.I),
    re.compile(r"\bPYTHONPATH\b"),
    re.compile(r"\bMAP144_[A-Z0-9_]+\b"),
    re.compile(r"\$env:[A-Za-z0-9_]+"),
    re.compile(r"^export\s+[A-Z0-9_]+", re.M),
    re.compile(r"--bind-client|--log-level"),
)


def test_user_facing_docs_have_no_cli_or_env_instructions():
    for path in USER_FACING:
        text = path.read_text(encoding="utf-8")
        for pat in _FORBIDDEN:
            m = pat.search(text)
            assert not m, f"{path.name}: {pat.pattern!r} matched {m.group(0)!r}"


def test_readme_install_section_is_download_and_run():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = readme.split("## Install", 1)[1].split("\n## ", 1)[0]
    assert "WS-Skim-Setup.exe" in install
    assert "ws-skim-linux-install.sh" in install
    assert "sudo" not in install
