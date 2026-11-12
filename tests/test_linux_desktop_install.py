# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Linux double-click installer: desktop file, exec bits, non-git copy."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Linux desktop installer",
)

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "scripts" / "Install WS Skim.desktop"
INSTALLER = ROOT / "scripts" / "add-ws-skim-to-desktop.sh"


def test_installer_file_is_named_and_executable():
    text = DESKTOP.read_text(encoding="utf-8")
    assert DESKTOP.name.startswith("Install WS Skim")
    assert "Name=Install WS Skim\n" in text
    assert "add-ws-skim-to-desktop.sh" in text
    assert "/bin/bash" in text
    assert "file://" in text
    assert "python" not in text.lower()
    assert os.access(DESKTOP, os.X_OK)
    assert os.access(INSTALLER, os.X_OK)
    listed = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts/Install WS Skim.desktop"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.stdout.strip():
        assert listed.stdout.startswith("100755"), listed.stdout


def test_missing_python_uses_a_dialog_and_names_the_package():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        seen = tmp / "zenity.txt"
        bindir = _bindir(tmp, ["chmod", "dirname"])
        zenity = bindir / "zenity"
        zenity.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > "{seen}"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        zenity.chmod(0o755)
        env = _quiet_env(tmp)
        env["PATH"] = str(bindir)
        env["DISPLAY"] = ":99"
        proc = subprocess.run(
            ["/bin/bash", str(INSTALLER)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        shown = seen.read_text(encoding="utf-8")
    assert "Python was not found" in shown
    assert "python3" in shown
    assert "Install WS Skim" in shown
    assert "python -m" not in shown
    assert "PYTHONPATH" not in shown
    assert "MAP144_" not in shown


def test_install_repairs_exec_bits_and_writes_trusted_icons():
    targets = [INSTALLER, ROOT / "scripts" / "start-ws-skim.sh", DESKTOP]
    saved = {path: path.stat().st_mode for path in targets}
    try:
        for path in targets:
            path.chmod(saved[path] & ~0o111)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            (home / "Desktop").mkdir(parents=True)
            gio_log = tmp / "gio.txt"
            bindir = _bindir(tmp, ["gio", "update-desktop-database"])
            for name, body in (
                ("gio", f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{gio_log}"\n'),
                ("update-desktop-database", "#!/bin/sh\nexit 0\n"),
            ):
                tool = bindir / name
                if tool.is_symlink() or tool.exists():
                    tool.unlink()
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)
            for name in ("python3", "python"):
                link = bindir / name
                if link.exists() or link.is_symlink():
                    link.unlink()
                os.symlink(sys.executable, link)
            env = _quiet_env(home)
            env["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
            proc = subprocess.run(
                ["/bin/bash", str(INSTALLER)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert "Double-click WS Skim" in proc.stdout
            for path in targets:
                assert os.access(path, os.X_OK), path
            desk = home / "Desktop" / "WS Skim.desktop"
            menu = home / ".local" / "share" / "applications" / "WS Skim.desktop"
            assert desk.is_file()
            assert menu.is_file()
            assert os.access(desk, os.X_OK)
            text = desk.read_text(encoding="utf-8")
            assert "Name=WS Skim\n" in text
            assert "/bin/bash" in text
            assert "start-ws-skim.sh" in text
            gio = gio_log.read_text(encoding="utf-8")
            assert "metadata::trusted" in gio
            assert "true" in gio
            assert "WS Skim.desktop" in gio
    finally:
        for path, mode in saved.items():
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_download_is_copied_home_and_icons_point_there():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        home = tmp / "home"
        desk = home / "Desktop"
        menu = home / "data" / "applications"
        desk.mkdir(parents=True)
        menu.mkdir(parents=True)
        download = _fake_download(home / "Downloads", "ws-skim-0.1.4-linux")
        gio_log = tmp / "gio.txt"
        proc = _run_installer(download, home, tmp, gio_log)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        installed = home / "data" / "ws-skim"
        assert (installed / "map144.py").is_file()
        assert os.access(installed / "scripts" / "start-ws-skim.sh", os.X_OK)
        assert "delete the downloaded folder" in proc.stdout
        assert str(installed) in proc.stdout
        for folder in (desk, menu):
            entry = (folder / "WS Skim.desktop").read_text(encoding="utf-8")
            assert f"Path={installed}\n" in entry
            assert f"{installed}/scripts/start-ws-skim.sh" in entry
            assert "ws-skim.png" in entry
            assert "Downloads" not in entry
        previous = home / "data" / "ws-skim.previous"
        # First install has no previous copy.
        assert not previous.exists()

        stale = installed / "only-in-old.txt"
        stale.write_text("old\n")
        newer = _fake_download(home / "Downloads", "ws-skim-0.1.5-linux")
        proc = _run_installer(newer, home, tmp, gio_log)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not stale.exists()
        assert previous.is_dir()
        assert (previous / "only-in-old.txt").read_text() == "old\n"


def _fake_download(parent: Path, name: str) -> Path:
    tree = parent / name
    tree.mkdir(parents=True)
    shutil.copy2(ROOT / "map144.py", tree / "map144.py")
    shutil.copytree(
        ROOT / "scripts", tree / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        ROOT / "map144_app", tree / "map144_app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return tree


def _run_installer(tree: Path, home: Path, tmp: Path, gio_log: Path):
    bindir = _bindir(tmp, ["gio", "update-desktop-database"])
    for name, body in (
        ("gio", f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{gio_log}"\n'),
        ("update-desktop-database", "#!/bin/sh\nexit 0\n"),
    ):
        tool = bindir / name
        if tool.is_symlink() or tool.exists():
            tool.unlink()
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    for name in ("python3", "python"):
        link = bindir / name
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(sys.executable, link)
    env = _quiet_env(home)
    env["XDG_DATA_HOME"] = str(home / "data")
    env["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
    return subprocess.run(
        ["/bin/bash", str(tree / "scripts" / "add-ws-skim-to-desktop.sh")],
        cwd=tree, env=env, capture_output=True, text=True, timeout=120,
    )


def _quiet_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    env["WS_SKIM_SKIP_VENV"] = "1"
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("XDG_DATA_HOME", None)
    env.pop("XDG_DESKTOP_DIR", None)
    return env


def _bindir(tmp: Path, names: list[str]) -> Path:
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    for name in names:
        src = shutil.which(name)
        if not src:
            continue
        dest = bindir / name
        if not dest.exists():
            os.symlink(src, dest)
    return bindir
