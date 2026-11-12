#!/usr/bin/env python3
# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail unless pyproject.toml version equals ws_skim.__version__."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _field(path: Path, pattern: str, label: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    if not match:
        raise SystemExit(f"{label} not found in {path}")
    return match.group(1)


def main() -> int:
    py_ver = _field(
        ROOT / "pyproject.toml",
        r'(?m)^version\s*=\s*"([^"]+)"',
        "version",
    )
    init_ver = _field(
        ROOT / "ws_skim" / "__init__.py",
        r'(?m)^__version__\s*=\s*"([^"]+)"',
        "__version__",
    )
    print(f"pyproject.toml version={py_ver}  ws_skim.__version__={init_ver}")
    if py_ver != init_ver:
        print("ERROR: version mismatch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
