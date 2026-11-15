# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""App identity: bundled icons resolve and the launcher matches WM_CLASS."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ws_skim.app_identity import (
    APP_DISPLAY_NAME,
    APP_ID,
    DESKTOP_FILE_NAME,
    WINDOWS_APP_USER_MODEL_ID,
    apply,
    icon_paths,
    install_root,
    prepare_process,
    window_icon,
)

ROOT = Path(__file__).resolve().parent.parent


def test_install_root_is_the_checkout():
    assert install_root() == ROOT
    assert (install_root() / "ws_skim.py").is_file()


def test_icon_files_resolve_from_the_tree_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = icon_paths()
    assert paths, "no bundled icon files were found"
    names = {p.name for p in paths}
    assert "ws-skim.png" in names
    assert "ws-skim-128.png" in names
    assert "ws-skim-64.png" in names
    assert "ws-skim-48.png" in names
    assert "ws-skim.ico" in names
    for path in paths:
        assert path.is_file()
        assert path.is_absolute()
        assert path.is_relative_to(ROOT)
        assert not path.is_relative_to(tmp_path)


def test_desktop_script_startup_wm_class_matches_app_id():
    script = (ROOT / "scripts" / "install-desktop.sh").read_text(encoding="utf-8")
    assert f"StartupWMClass={APP_ID}" in script
    assert APP_ID == "ws-skim"
    assert DESKTOP_FILE_NAME == APP_ID


def test_inno_shortcuts_use_the_same_app_user_model_id():
    iss = (ROOT / "packaging" / "windows" / "ws-skim.iss").read_text(encoding="utf-8")
    assert f'AppUserModelID: "{WINDOWS_APP_USER_MODEL_ID}"' in iss
    assert iss.count(f'AppUserModelID: "{WINDOWS_APP_USER_MODEL_ID}"') >= 2
    assert WINDOWS_APP_USER_MODEL_ID == "wa1hco.WSSkim"


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt5")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app


def test_window_icon_loads_bundled_sizes(qapp):
    icon = window_icon()
    assert not icon.isNull()
    sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
    # PNGs are 48, 64, 128, 256; the .ico also carries 16 and 32.
    assert {48, 64, 128, 256}.issubset({w for w, _h in sizes})


def test_apply_sets_qt_identity_and_icon(qapp):
    apply(qapp)
    assert qapp.applicationName() == APP_ID
    assert qapp.applicationDisplayName() == APP_DISPLAY_NAME
    desktop = qapp.desktopFileName()
    assert desktop in (DESKTOP_FILE_NAME, DESKTOP_FILE_NAME + ".desktop")
    assert not qapp.windowIcon().isNull()


def test_prepare_process_sets_resource_name():
    prepare_process()
    assert os.environ.get("RESOURCE_NAME") == APP_ID
    # The Windows AppUserModelID call is a no-op (and must not raise)
    # on this platform; the constant is still the one Inno Setup writes.
    if sys.platform == "win32":
        pytest.importorskip("ctypes")
