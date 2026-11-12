# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Create the Linux venv, check jt9, and write trusted Desktop / app-menu icons.

Stdlib only for the shortcut path, so the installer can write icons even
when the venv step is skipped in tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SHORTCUT_NAME = "WS Skim"


def _root_from_argv_or_here(arg: str | None = None) -> Path:
    if arg:
        return Path(arg).resolve()
    return Path(__file__).resolve().parents[1]


def desktop_dir() -> Path:
    home = Path.home()
    xdg = (os.environ.get("XDG_DESKTOP_DIR") or "").strip()
    if xdg:
        return Path(xdg)
    for candidate in (home / "Desktop", home / "desktop"):
        if candidate.is_dir():
            return candidate
    return home / "Desktop"


def applications_dir() -> Path:
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "applications"
    return Path.home() / ".local" / "share" / "applications"


def find_icon_path(root: Path) -> Path | None:
    for name in ("ws-skim.png", "ws-skim-128.png", "ws-skim-64.png"):
        path = root / "scripts" / "assets" / name
        if path.is_file():
            return path
    return None


def freedesktop_exec(argv: list[str]) -> str:
    """Exec= value GLib can store and a file manager can launch."""
    parts: list[str] = []
    for arg in argv:
        if any(ch in arg for ch in ' \t\n"\\$`'):
            desktop = (
                arg.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("$", "\\$")
                .replace("`", "\\`")
            )
            parts.append('"' + desktop.replace("\\", "\\\\") + '"')
        else:
            parts.append(arg)
    return " ".join(parts)


def linux_desktop_entry_text(root: Path) -> str:
    icon = find_icon_path(root)
    icon_line = f"Icon={icon}" if icon else "Icon=utilities-terminal"
    script = root / "scripts" / "start-ws-skim.sh"
    exec_line = freedesktop_exec(["/bin/bash", str(script)])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={SHORTCUT_NAME}\n"
        "GenericName=Wideband MSK144 skimmer\n"
        "Comment=WS Skim meteor-scatter decoder\n"
        f"Exec={exec_line}\n"
        f"Path={root}\n"
        f"{icon_line}\n"
        "Terminal=false\n"
        "Categories=HamRadio;Network;\n"
        "StartupNotify=true\n"
    )


def mark_linux_launcher_trusted(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | 0o111)
    except OSError:
        return
    if not sys.platform.startswith("linux"):
        return
    gio = shutil.which("gio")
    if not gio:
        return
    subprocess.run(
        [gio, "set", str(path), "metadata::trusted", "true"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _refresh_applications_menu(menu: Path) -> None:
    tool = shutil.which("update-desktop-database")
    if not tool or not menu.is_dir():
        return
    subprocess.run(
        [tool, str(menu)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write_linux_shortcuts(root: Path) -> Path:
    """Write trusted Desktop and app-menu entries named WS Skim."""
    content = linux_desktop_entry_text(root)
    desk = desktop_dir()
    desk.mkdir(parents=True, exist_ok=True)
    path = desk / f"{SHORTCUT_NAME}.desktop"
    path.write_text(content, encoding="utf-8")
    mark_linux_launcher_trusted(path)
    menu = applications_dir()
    try:
        menu.mkdir(parents=True, exist_ok=True)
        menu_path = menu / f"{SHORTCUT_NAME}.desktop"
        menu_path.write_text(content, encoding="utf-8")
        mark_linux_launcher_trusted(menu_path)
        _refresh_applications_menu(menu)
    except OSError:
        pass
    return path


def venv_python(root: Path) -> Path:
    if sys.platform.startswith("win"):
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _enable_system_site_packages(venv_dir: Path) -> None:
    cfg = venv_dir / "pyvenv.cfg"
    if not cfg.is_file():
        return
    text = cfg.read_text(encoding="utf-8")
    if "include-system-site-packages = false" in text:
        cfg.write_text(
            text.replace(
                "include-system-site-packages = false",
                "include-system-site-packages = true",
            ),
            encoding="utf-8",
        )


def ensure_venv(root: Path, python: str | None = None) -> Path:
    """Create or update ``root/.venv`` and install requirements.txt.

    Uses ``--system-site-packages`` so an apt ``python3-uhd`` package is
    visible. Does not install GPU extras or UHD.
    """
    root = Path(root)
    venv_dir = root / ".venv"
    py = python or sys.executable
    if not venv_dir.is_dir():
        _create_venv(py, venv_dir)
    else:
        _enable_system_site_packages(venv_dir)
    exe = venv_python(root)
    if not exe.is_file():
        raise RuntimeError(f"venv python missing: {exe}")
    _ensure_pip(exe)
    req = root / "requirements.txt"
    subprocess.run([str(exe), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    if req.is_file():
        subprocess.run([str(exe), "-m", "pip", "install", "-r", str(req)], check=True)
    return exe


def _create_venv(python: str, venv_dir: Path) -> None:
    try:
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
        if venv_python(venv_dir.parent).is_file():
            return
    except Exception:
        shutil.rmtree(venv_dir, ignore_errors=True)
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(venv_dir)


def _ensure_pip(exe: Path) -> None:
    probe = subprocess.run(
        [str(exe), "-m", "pip", "--version"],
        capture_output=True, check=False,
    )
    if probe.returncode == 0:
        return
    boot = subprocess.run(
        [str(exe), "-m", "ensurepip", "--upgrade"],
        capture_output=True, check=False,
    )
    if boot.returncode == 0:
        return
    import tempfile
    import urllib.request
    url = "https://bootstrap.pypa.io/get-pip.py"
    with tempfile.TemporaryDirectory(prefix="ws-skim-get-pip-") as td:
        script = Path(td) / "get-pip.py"
        with urllib.request.urlopen(url, timeout=60) as resp:
            script.write_bytes(resp.read())
        subprocess.run([str(exe), str(script)], check=True)


def jt9_warning(root: Path) -> str | None:
    """Return a user-facing warning if jt9 is missing, else None."""
    exe = venv_python(root)
    runner = str(exe) if exe.is_file() else sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        proc = subprocess.run(
            [runner, "-c", "from map144_app.detection import find_jt9; print(find_jt9() or '')"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    if (proc.stdout or "").strip():
        return None
    from map144_app.jt9_guide import JT9_INSTALL_HINT
    return JT9_INSTALL_HINT


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("root", nargs="?", default=None)
    parser.add_argument("--venv", action="store_true")
    parser.add_argument("--shortcuts", action="store_true", default=True)
    parser.add_argument("--no-shortcuts", action="store_false", dest="shortcuts")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_from_argv_or_here(args.root)
    if args.venv and os.environ.get("WS_SKIM_SKIP_VENV") != "1":
        try:
            ensure_venv(root)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
            sys.stderr.write(f"{e}\n")
            return 1
    if args.shortcuts:
        write_linux_shortcuts(root)
    warning = jt9_warning(root)
    if warning:
        print(f"JT9_MISSING:{warning}")
    else:
        print("JT9_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
