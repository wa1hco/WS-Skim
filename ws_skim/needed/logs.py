# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


"""Discover and poll the ADIF log used for worked-before."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ws_skim.needed.adif import Qso, load_qsos
from ws_skim.needed.paths import data_home, installed_dirs, wrig_instance_dirs


@dataclass
class LogCandidate:
    path: str
    title: str
    kind: str  # wsjtx | wrig | chosen | other


@dataclass
class LogStore:
    """In-memory copy of the selected ADIF, as row dicts for the checklist."""

    log_label: str = "the log"
    path: str = ""
    why: str = "No log found yet. Stations will look needed until a log is loaded."
    error: str | None = None
    _rows: list[dict] = field(default_factory=list)
    _gen: int = 0
    _mtime: float | None = None
    qso_count: int = 0

    def cached_rows(self) -> list[dict]:
        return self._rows

    def generation(self) -> int:
        return self._gen

    def load(self, qsos: list[Qso], *, path: str, label: str, why: str,
             error: str | None = None, mtime: float | None = None) -> None:
        self._rows = [q.as_row() for q in qsos]
        self._gen += 1
        self.path = path
        self.log_label = label
        self.why = why
        self.error = error
        self._mtime = mtime
        self.qso_count = len(qsos)

    def clear(self, why: str) -> None:
        self.load([], path="", label="the log", why=why, error=None, mtime=None)

    def public(self) -> dict:
        return {
            "path": self.path,
            "title": self.log_label,
            "why": self.why,
            "error": self.error or "",
            "count": self.qso_count,
        }


def discover_adif_paths(
    *,
    extra: list[str | Path] | None = None,
    data: Path | None = None,
    config: Path | None = None,
) -> list[LogCandidate]:
    """WSJT-X wsjtx_log.adi, wrig instance ADIF files, then any extra paths."""
    found: list[LogCandidate] = []
    seen: set[str] = set()

    def add(path: Path, title: str, kind: str) -> None:
        try:
            if not path.is_file():
                return
            key = str(path.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        found.append(LogCandidate(path=key, title=title, kind=kind))

    for folder in installed_dirs(data):
        add(folder / "wsjtx_log.adi", f"WSJT-X ({folder.name})", "wsjtx")
    # Default locations even if the folder listing missed them.
    add((data or data_home()) / "WSJT-X" / "wsjtx_log.adi", "WSJT-X", "wsjtx")
    for folder in wrig_instance_dirs(config):
        # The usual name, then any other .adi in the instance folder.
        add(folder / "wsjtx_log.adi", f"wrig ({folder.name})", "wrig")
        try:
            for adi in sorted(folder.glob("*.adi")):
                add(adi, f"wrig ({folder.name}: {adi.name})", "wrig")
        except OSError:
            pass
    for raw in extra or []:
        path = Path(raw)
        add(path, path.name, "chosen")
    return found


def load_first(candidates: list[LogCandidate], store: LogStore) -> LogCandidate | None:
    """Load the first readable candidate into ``store``. Return it, or None."""
    if not candidates:
        store.clear("No log found yet. Stations will look needed until a log is loaded.")
        return None
    for cand in candidates:
        qsos, problem = load_qsos(cand.path)
        if problem:
            continue
        try:
            mtime = Path(cand.path).stat().st_mtime
        except OSError:
            mtime = None
        store.load(
            qsos, path=cand.path, label=cand.title,
            why=f"Using {cand.title} ({len(qsos)} contacts).",
            mtime=mtime,
        )
        return cand
    store.clear("A log was found, but it could not be read.")
    store.error = "unreadable"
    return None


def reload_path(path: str | Path, store: LogStore, *, title: str | None = None) -> bool:
    """Load one ADIF path. True when the file was readable."""
    p = Path(path)
    qsos, problem = load_qsos(p)
    label = title or p.name
    if problem:
        store.clear(f"{p} could not be read ({problem}).")
        store.error = problem
        store.path = str(p)
        store.log_label = label
        return False
    try:
        mtime = p.stat().st_mtime
        shown = str(p.resolve())
    except OSError:
        mtime = None
        shown = str(p)
    store.load(
        qsos, path=shown, label=label,
        why=f"Using {label} ({len(qsos)} contacts).",
        mtime=mtime,
    )
    return True


def poll_changed(store: LogStore) -> bool:
    """True when the selected file's mtime moved (caller should reload)."""
    if not store.path:
        return False
    try:
        mtime = Path(store.path).stat().st_mtime
    except OSError:
        return False
    if store._mtime is None or mtime != store._mtime:
        return True
    return False
