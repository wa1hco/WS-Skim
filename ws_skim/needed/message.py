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


"""Best-effort (is_cq, dx_call, to_call, grid, message_tail) from a decode."""
from __future__ import annotations

import re

_GRID_RE = re.compile(r"\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b", re.IGNORECASE)
# A callsign token: has a letter and a digit, optional /portable, not a report.
_CALLISH = re.compile(r"^(?:<[A-Z0-9/]+>|<?[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}(?:/[A-Z0-9]+)?|>?)$", re.I)
_REPORT = re.compile(r"^R?[+-][0-9]{1,3}$")
_SIGNOFF = frozenset({"RRR", "RR73", "73"})
_DIRECTED = frozenset({
    "DX", "NA", "SA", "EU", "AF", "AS", "OC", "AN",
    "POTA", "SOTA", "QRP", "TEST", "FD", "WEST", "EAST",
})


def extract_grid(message: str | None) -> str | None:
    """First whole token that is a Maidenhead grid.

    RR73 has the shape of a grid (it is a real square in the Arctic Ocean),
    but in a decode it is always the sign-off, so it never counts as one.
    """
    if not message:
        return None
    for tok in message.split():
        if tok.upper() in _SIGNOFF:
            continue
        m = _GRID_RE.fullmatch(tok)
        if m:
            return m.group(1).upper()[:4]
    return None


def _plain(token: str) -> str | None:
    t = (token or "").strip()
    if t.startswith("<") and t.endswith(">"):
        t = t[1:-1]
    if t in {".", "...", ""}:
        return None
    return t.upper() or None


def _is_call(token: str) -> bool:
    raw = (token or "").strip()
    if not raw or _REPORT.match(raw) or raw.upper() in _SIGNOFF:
        return False
    if raw.startswith("<") and raw.endswith(">"):
        inner = raw[1:-1]
        if inner in {".", "...", ""}:
            return True  # a call slot, but not a station
        raw = inner
    if extract_grid(raw) == raw[:4] and len(raw) in (4, 6):
        return False
    return bool(_CALLISH.match(raw)) and any(c.isdigit() for c in raw) and any(c.isalpha() for c in raw)


def _station(token: str) -> str | None:
    """A real call, or None for <...> / non-calls."""
    raw = (token or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        inner = raw[1:-1]
        if inner in {".", "...", ""}:
            return None
        raw = inner
    if not _is_call(token):
        return None
    return _plain(raw)


def _calling_station(tokens: list[str]) -> tuple[str | None, int | None]:
    """First callsign after CQ/QRZ/DE, skipping directed tags. (call, index)."""
    for i, tok in enumerate(tokens[1:], start=1):
        if tok.upper() in _DIRECTED:
            continue
        if _is_call(tok):
            return _station(tok), i
        # A short directed token without digits (NA, POTA already handled).
        if tok.isalpha() and len(tok) <= 4:
            continue
        if _is_call(tok):
            return _station(tok), i
    return None, None


def _tail_after(tokens: list[str], start: int) -> str:
    if start >= len(tokens):
        return ""
    return " ".join(tokens[start:])


def interpret(message: str | None) -> tuple[bool, str | None, str | None, str | None, str | None]:
    """Return ``(is_cq, dx_call, to_call, grid, message_tail)``."""
    if not message or not message.strip():
        return False, None, None, None, None
    stripped = message.strip()
    grid = extract_grid(stripped)
    if stripped.upper().startswith("QSY"):
        return False, None, stripped, None, stripped
    tokens = stripped.split()
    head = tokens[0].upper()
    if head in {"CQ", "QRZ", "DE"}:
        dx_call, dx_at = _calling_station(tokens)
        tail = _tail_after(tokens, (dx_at + 1) if dx_at is not None else 1)
        return head == "CQ", dx_call, head, grid, tail
    if len(tokens) >= 2 and _is_call(tokens[0]) and _is_call(tokens[1]):
        dx_call = _station(tokens[1])
        to_call = _station(tokens[0])
        if dx_call:
            return False, dx_call, to_call, grid, _tail_after(tokens, 2)
        if to_call:
            return False, None, to_call, None, _tail_after(tokens, 2)
    return False, None, None, grid, stripped
