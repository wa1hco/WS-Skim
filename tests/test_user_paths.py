# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""User settings and log locations, plus one-time migration."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from map144_app import user_paths


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    if sys.platform.startswith("win"):
        monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_paths._reset_migration_flag()
    return home


def test_linux_defaults(isolated_home):
    if sys.platform.startswith("win"):
        pytest.skip("Linux path layout")
    assert user_paths.config_dir() == isolated_home / ".config" / "ws-skim"
    assert user_paths.log_dir() == isolated_home / ".local" / "state" / "ws-skim" / "logs"


def test_windows_defaults(isolated_home):
    if not sys.platform.startswith("win"):
        pytest.skip("Windows path layout")
    assert user_paths.config_dir() == isolated_home / "AppData" / "Roaming" / "WS-Skim"
    assert user_paths.log_dir() == isolated_home / "AppData" / "Local" / "WS-Skim" / "logs"


def test_migrates_in_tree_logs_and_legacy_settings(isolated_home, tmp_path, monkeypatch):
    root = tmp_path / "program"
    (root / "MSK144" / "logs").mkdir(parents=True)
    (root / "MSK144" / "logs" / "map144_old.log").write_text("kept-log\n")
    (root / "map144.py").write_text("# program\n")
    (root / "scripts").mkdir()
    monkeypatch.setattr(user_paths, "program_root", lambda start=None: root)

    if sys.platform.startswith("win"):
        legacy = isolated_home / "AppData" / "Roaming" / "RadioIQ"
        legacy.mkdir(parents=True)
        (legacy / "RadioIQVisualizer.ini").write_text("[General]\nmin_level=-80\n")
    else:
        legacy = isolated_home / ".config" / "RadioIQ"
        legacy.mkdir(parents=True)
        (legacy / "RadioIQVisualizer.conf").write_text("[General]\nmin_level=-80\n")
        old_map = isolated_home / ".config" / "map144"
        old_map.mkdir()
        (old_map / "sdrangel_layout.conf").write_text("layout\n")

    user_paths.migrate_user_data(root)

    assert (user_paths.log_dir() / "map144_old.log").read_text() == "kept-log\n"
    settings = user_paths.settings_file()
    assert settings.is_file()
    assert "min_level=-80" in settings.read_text()
    if not sys.platform.startswith("win"):
        assert (user_paths.config_dir() / "sdrangel_layout.conf").read_text() == "layout\n"

    # Second run does not overwrite a newer settings file.
    settings.write_text("[General]\nmin_level=-50\n")
    user_paths._reset_migration_flag()
    user_paths.migrate_user_data(root)
    assert "min_level=-50" in settings.read_text()


def test_redirect_missing_console(isolated_home, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    user_paths.redirect_missing_console()
    try:
        print("hello from pythonw", flush=True)
        assert sys.stderr is sys.stdout
        assert "hello from pythonw" in (user_paths.log_dir() / "console.log").read_text()
    finally:
        sys.stdout.close()


def test_redirect_leaves_real_console_alone(isolated_home):
    before = (sys.stdout, sys.stderr)
    user_paths.redirect_missing_console()
    assert (sys.stdout, sys.stderr) == before
    assert not (user_paths.log_dir() / "console.log").exists()


def test_jt9_dialog_does_not_block_headless(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from map144_app.jt9_guide import JT9_MISSING_HTML, show_jt9_missing_dialog
    show_jt9_missing_dialog()  # must return immediately
    assert "wsjt.sourceforge.io" in JT9_MISSING_HTML
    assert "MAP144_" not in JT9_MISSING_HTML
