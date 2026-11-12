# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Installer pieces that can be checked without a clean machine.

The full install on clean Linux and Windows runners is in
.github/workflows/installers.yml.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "dev"))

import stage_release  # noqa: E402

from map144_app import __version__  # noqa: E402
from map144_app import buildinfo  # noqa: E402

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs bash",
)
needs_git = pytest.mark.skipif(
    not (ROOT / ".git").exists() or shutil.which("git") is None,
    reason="needs a git checkout",
)


# ── version / tag ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tag", [f"v{__version__}", f"v{__version__}-test", f"v{__version__}-rc.1"])
def test_check_tag_accepts_matching(tag):
    stage_release.check_tag(tag, __version__)


@pytest.mark.parametrize("tag", [__version__, "v9.9.9", f"v{__version__}x", f"v{__version__}-"])
def test_check_tag_rejects_mismatch(tag):
    with pytest.raises(SystemExit):
        stage_release.check_tag(tag, __version__)


def test_app_version_matches_package():
    assert stage_release.app_version() == __version__


def test_buildinfo_reads_build_file(tmp_path):
    (tmp_path / "BUILD").write_text("abc1234\n", encoding="utf-8")
    assert buildinfo.version_string(tmp_path) == f"{__version__} (abc1234)"


def test_buildinfo_without_hash_is_plain_version(tmp_path):
    # tmp_path is neither a git checkout nor has BUILD.
    assert buildinfo.version_string(tmp_path) == __version__


# ── staging ──────────────────────────────────────────────────────────────

@needs_git
def test_stage_excludes_developer_paths(tmp_path):
    out = tmp_path / "stage"
    names = stage_release.stage(out)
    for gone in ("docs", "tests", ".github", ".claude", "CLAUDE.md"):
        assert gone not in names
    for kept in ("map144.py", "map144_app", "experiments", "scripts", "LICENSE",
                 "NOTICE", "requirements.txt"):
        assert kept in names, kept
    head = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
    assert (out / "BUILD").read_text().strip() == head
    if sys.platform != "win32":
        assert (out / "scripts" / "run.sh").stat().st_mode & stat.S_IXUSR


# ── Linux desktop launcher ───────────────────────────────────────────────

def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env["HOME"] = str(home)
    # No xdg-user-dir on PATH lookups beyond the system's; fine either way.
    return env


@needs_bash
def test_install_desktop_adds_and_removes(tmp_path):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    script = ROOT / "scripts" / "install-desktop.sh"
    subprocess.run(["bash", str(script)], env=_env(home), check=True, capture_output=True)

    menu = home / ".local/share/applications/ws-skim.desktop"
    desk = home / "Desktop/ws-skim.desktop"
    for f in (menu, desk):
        text = f.read_text()
        assert f.stat().st_mode & stat.S_IXUSR
        assert "Name=WS Skim" in text
        assert f'Exec="{ROOT}/scripts/run.sh"' in text
        assert f"Icon={ROOT}/scripts/assets/ws-skim.png" in text

    subprocess.run(["bash", str(script), "--remove"], env=_env(home), check=True,
                   capture_output=True)
    assert not menu.exists() and not desk.exists()


@needs_bash
def test_install_desktop_without_desktop_folder(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install-desktop.sh")],
                       env=_env(home), check=True, capture_output=True, text=True)
    assert (home / ".local/share/applications/ws-skim.desktop").is_file()
    assert "skipped the desktop icon" in r.stdout


# ── Linux self-extracting installer ──────────────────────────────────────

def _make_installer(tmp_path: Path) -> Path:
    """Header + tiny payload, the same way build-installer.sh assembles it."""
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "hello.txt").write_text("hi\n")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tree, arcname=".")
    header = (ROOT / "packaging/linux/installer-header.sh").read_text()
    inst = tmp_path / "ws-skim-linux-install.sh"
    inst.write_bytes(header.replace("@VERSION@", "9.9.9").encode() + buf.getvalue())
    return inst


@needs_bash
def test_linux_installer_extracts_and_updates(tmp_path):
    inst = _make_installer(tmp_path)
    dest = tmp_path / "dest dir"
    run = lambda: subprocess.run(  # noqa: E731
        ["bash", str(inst), "--dest", str(dest), "--extract-only"],
        env=_env(tmp_path), check=True, capture_output=True, text=True)
    run()
    assert (dest / "hello.txt").read_text() == "hi\n"
    assert (dest / ".ws-skim-install").read_text().strip() == "9.9.9"

    # Update keeps the venv and recordings, drops stale program files.
    (dest / ".venv").mkdir()
    (dest / "MSK144").mkdir()
    (dest / "MSK144" / "rec.wav").write_text("x")
    (dest / "stale.py").write_text("x")
    run()
    assert (dest / ".venv").is_dir()
    assert (dest / "MSK144" / "rec.wav").exists()
    assert not (dest / "stale.py").exists()


@needs_bash
def test_linux_installer_refuses_foreign_folder(tmp_path):
    inst = _make_installer(tmp_path)
    dest = tmp_path / "someone_elses"
    dest.mkdir()
    (dest / "precious.txt").write_text("keep me")
    r = subprocess.run(["bash", str(inst), "--dest", str(dest), "--extract-only"],
                       env=_env(tmp_path), capture_output=True, text=True)
    assert r.returncode != 0
    assert (dest / "precious.txt").read_text() == "keep me"
    r = subprocess.run(["bash", str(inst), "--dest", str(dest), "--uninstall"],
                       env=_env(tmp_path), capture_output=True, text=True)
    assert r.returncode != 0
    assert (dest / "precious.txt").exists()


# ── Windows installer script sanity (no Inno compiler here) ──────────────

def test_inno_script_references_existing_files():
    iss = (ROOT / "packaging/windows/ws-skim.iss").read_text(encoding="utf-8")
    for rel in ("scripts\\windows\\assets\\ws-skim.ico", "scripts\\windows\\run.bat",
                "scripts\\windows\\install.ps1", "tools\\Install-UhdWindows.ps1"):
        assert rel in iss, rel
        assert (ROOT / rel.replace("\\", "/")).is_file(), rel
    assert "OutputBaseFilename=WS-Skim-Setup" in iss
    assert "PrivilegesRequired=lowest" in iss
