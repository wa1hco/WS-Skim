# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Install place, copy/swap, and git-checkout handling."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from map144_app import install_tree


def _tree(path: Path, marker: str = "a") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "map144.py").write_text(f"# marker={marker}\n")
    (path / "scripts").mkdir(exist_ok=True)
    (path / "scripts" / "start-ws-skim.sh").write_text("#!/bin/bash\n")
    return path


class InstallPlaceTests(unittest.TestCase):
    def test_xdg_data_home_wins(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/x/data"}):
            self.assertEqual(install_tree.install_dir(), Path("/x/data/ws-skim"))

    def test_default_is_local_share(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": ""}
        ), mock.patch.object(Path, "home", return_value=Path(td)):
            self.assertEqual(
                install_tree.install_dir(),
                Path(td) / ".local" / "share" / "ws-skim",
            )


class CopyTests(unittest.TestCase):
    def test_copy_skips_caches_and_keeps_old_venv(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = _tree(tmp / "dl", "new")
            (src / "map144_app").mkdir()
            (src / "map144_app" / "__pycache__").mkdir()
            dest = _tree(tmp / "data" / "ws-skim", "old")
            (dest / ".venv").mkdir()
            (dest / ".venv" / "kept.txt").write_text("keep")
            out = install_tree.install_copy(src, dest)
            self.assertEqual(out, dest)
            self.assertIn("new", (dest / "map144.py").read_text())
            self.assertFalse((dest / "map144_app" / "__pycache__").exists())
            self.assertEqual((dest / ".venv" / "kept.txt").read_text(), "keep")
            prev = install_tree.previous_dir(dest)
            self.assertTrue(prev.is_dir())
            self.assertIn("old", (prev / "map144.py").read_text())

    def test_running_from_the_installed_copy_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            dest = _tree(Path(td) / "ws-skim")
            self.assertEqual(install_tree.install_copy(dest, dest), dest)
            self.assertTrue((dest / "scripts" / "start-ws-skim.sh").is_file())

    def test_refuses_a_folder_that_is_not_the_program(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                install_tree.install_copy(Path(td), Path(td) / "ws-skim")

    def test_git_checkout_is_not_copied(self):
        with tempfile.TemporaryDirectory() as td:
            src = _tree(Path(td) / "clone")
            (src / ".git").mkdir()
            out = io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch.dict(
                os.environ, {"XDG_DATA_HOME": str(Path(td) / "data")}
            ):
                self.assertEqual(install_tree.main([str(src)]), 0)
            self.assertEqual(out.getvalue().strip(), str(src.resolve()))
            self.assertFalse((Path(td) / "data").exists())


if __name__ == "__main__":
    unittest.main()
