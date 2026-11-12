#!/usr/bin/env python3
# WS Skim — regenerate the shipped desktop icons from one master image.
# Copyright (C) 2026 Jeff Millar, WA1HCO
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate every WS Skim icon file the release ships.

The master is ``assets/icons/ws-skim.png``: the 1024 px WS-series icon
(white "WS" over "SKIM" on the shared blue rounded square).

Outputs:

- ``scripts/assets/ws-skim.png`` (256 px) plus ``-48``, ``-64``, ``-128``
- ``scripts/windows/assets/ws-skim.ico`` (16, 32, 48, 64, 128, 256)

Run from anywhere (needs Pillow): ``python3 tools/dev/make_ws_skim_icons.py``
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "assets" / "icons" / "ws-skim.png"
PNG_DIR = ROOT / "scripts" / "assets"
ICO = ROOT / "scripts" / "windows" / "assets" / "ws-skim.ico"

PNG_SIZES = {
    "ws-skim.png": 256,
    "ws-skim-128.png": 128,
    "ws-skim-64.png": 64,
    "ws-skim-48.png": 48,
}
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _scaled(master: Image.Image, px: int) -> Image.Image:
    return master.resize((px, px), Image.LANCZOS)


def main() -> int:
    master = Image.open(MASTER).convert("RGBA")
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    ICO.parent.mkdir(parents=True, exist_ok=True)
    for name, px in PNG_SIZES.items():
        # optimize=True with no timestamps keeps the bytes stable run to run.
        _scaled(master, px).save(PNG_DIR / name, format="PNG", optimize=True)
        print(f"wrote {PNG_DIR / name} ({px}px)")
    _scaled(master, 256).save(ICO, format="ICO", sizes=ICO_SIZES)
    print(f"wrote {ICO} ({', '.join(str(s[0]) for s in ICO_SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
