# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Layout and icon checks for the scripts/ + tools/dev/ tree."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LINUX_SCRIPTS = (
    "scripts/install.sh",
    "scripts/run.sh",
    "scripts/run-router.sh",
    "scripts/install-desktop.sh",
    "packaging/linux/build-installer.sh",
)

WINDOWS_SCRIPTS = (
    "scripts/windows/install.ps1",
    "scripts/windows/run.bat",
    "scripts/windows/run-router.bat",
    "scripts/windows/run-b210.bat",
)

PNG_SIZES = {
    "scripts/assets/ws-skim.png": 256,
    "scripts/assets/ws-skim-128.png": 128,
    "scripts/assets/ws-skim-64.png": 64,
    "scripts/assets/ws-skim-48.png": 48,
}

ICO_SIZES = {(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}

# These used to live at the repo root and must not come back.
RELOCATED_FROM_ROOT = LINUX_SCRIPTS + WINDOWS_SCRIPTS + (
    "tools/dev/generate_msk144.py",
    "tools/dev/compare_decoders.py",
    "tools/dev/analyze_msk144.py",
)


def test_linux_scripts_exist_and_are_executable():
    for rel in LINUX_SCRIPTS:
        path = ROOT / rel
        assert path.is_file(), f"missing {rel}"
        if sys.platform != "win32":  # Windows has no executable bit
            assert path.stat().st_mode & stat.S_IXUSR, f"{rel} is not executable"


def test_windows_scripts_exist():
    for rel in WINDOWS_SCRIPTS:
        path = ROOT / rel
        assert path.is_file(), f"missing {rel}"


def test_root_launchers_were_moved():
    for name in (
        "install.sh",
        "install.ps1",
        "run.sh",
        "run.bat",
        "run-router.sh",
        "run-router.bat",
        "run-b210.bat",
        "install_gui_deps.sh",
        "generate_msk144.py",
        "compare_decoders.py",
        "analyze_msk144.py",
    ):
        assert not (ROOT / name).exists(), f"{name} should not remain at the repo root"
    # App entry points stay at the root.
    assert (ROOT / "map144.py").is_file()
    assert (ROOT / "router_app.py").is_file()
    for rel in RELOCATED_FROM_ROOT:
        assert (ROOT / rel).is_file(), f"missing relocated {rel}"


def test_icon_files_exist_with_expected_sizes():
    pytest.importorskip("PIL")
    from PIL import Image

    master = ROOT / "assets" / "icons" / "ws-skim.png"
    assert master.is_file(), "missing master icon assets/icons/ws-skim.png"
    with Image.open(master) as im:
        assert im.size == (1024, 1024), f"master icon size {im.size}, expected 1024x1024"

    for rel, px in PNG_SIZES.items():
        path = ROOT / rel
        assert path.is_file(), f"missing {rel}"
        with Image.open(path) as im:
            assert im.size == (px, px), f"{rel} size {im.size}, expected {px}x{px}"

    ico = ROOT / "scripts" / "windows" / "assets" / "ws-skim.ico"
    assert ico.is_file(), "missing scripts/windows/assets/ws-skim.ico"
    with Image.open(ico) as im:
        ico_file = getattr(im, "ico", None)
        assert ico_file is not None, "Pillow did not parse the ICO directory"
        sizes = set(ico_file.sizes())
        missing = ICO_SIZES - sizes
        assert not missing, f"ICO missing sizes {sorted(missing)}; got {sorted(sizes)}"


def test_icon_generator_exists():
    path = ROOT / "tools" / "dev" / "make_ws_skim_icons.py"
    assert path.is_file()
    if sys.platform != "win32":
        assert path.stat().st_mode & stat.S_IXUSR, f"{path} is not executable"
