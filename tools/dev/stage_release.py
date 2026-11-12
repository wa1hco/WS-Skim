#!/usr/bin/env python3
# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the files that go into a WS Skim installer.

Copies the committed tree (``git archive HEAD``) into ``--out``, leaving
out developer-only paths, and writes ``BUILD`` with the short git hash so
an installed copy (which has no ``.git``) can show ``0.1.4 (51b9fd9)``.

Usage:
    python tools/dev/stage_release.py --out build/stage [--tag v0.1.4-test]

With ``--tag``, fails unless the tag is ``v<version>`` or
``v<version>-<suffix>`` for the version in ``ws_skim/__init__.py``.
Prints the version on stdout for the build scripts to pick up.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Top-level paths users never need.  experiments/ stays: processing.py
# imports experiments.gpu_tfmf at runtime.
EXCLUDE_TOP = {
    ".github", ".vscode", ".claude", ".gitignore", ".gitattributes",
    "docs", "tests", "CLAUDE.md", "ws-skim.code-workspace",
    "packages_map144.svg", "classes_map144.svg", "decoded.txt",
}


def app_version(root: Path = ROOT) -> str:
    text = (root / "ws_skim" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("__version__ not found in ws_skim/__init__.py")
    return m.group(1)


def check_tag(tag: str, version: str) -> None:
    if not re.fullmatch(rf"v{re.escape(version)}(-[0-9A-Za-z.]+)?", tag):
        raise SystemExit(
            f"tag {tag!r} does not match app version {version!r} "
            f"(expected v{version} or v{version}-<suffix>)"
        )


def git_short_hash(root: Path = ROOT) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"], text=True
    ).strip()


def stage(out: Path, root: Path = ROOT) -> list[str]:
    """Extract HEAD into ``out`` minus EXCLUDE_TOP; return top-level names."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    blob = subprocess.check_output(["git", "-C", str(root), "archive", "--format=tar", "HEAD"])
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        members = [
            m for m in tar.getmembers()
            if m.name.split("/", 1)[0] not in EXCLUDE_TOP
        ]
        try:
            tar.extractall(out, members=members, filter="data")
        except TypeError:  # Python without extraction filters (< 3.10.12)
            tar.extractall(out, members=members)  # trusted: our own git tree
    (out / "BUILD").write_text(git_short_hash(root) + "\n", encoding="utf-8")
    return sorted(p.name for p in out.iterdir())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tag", default="")
    args = ap.parse_args(argv)

    version = app_version()
    if args.tag:
        check_tag(args.tag, version)
    names = stage(args.out.resolve())
    print(f"staged {len(names)} top-level entries into {args.out}", file=sys.stderr)
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
