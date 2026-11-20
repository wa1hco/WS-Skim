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


"""Operate roster show-menu: which heard stations stay on the page.

Copied from WS Hub ``wims.engine.roster_filter``; the message parser is local.
"""
from __future__ import annotations

from ws_skim.needed.message import interpret

MODES = ("needed", "needed-cq", "needed-73", "all")

_FREE_SIGNOFF = frozenset({"73", "RR73"})
_CHECKED = frozenset({"true", "1", "yes", "on", "checked"})
_UNCHECKED = frozenset({"false", "0", "no", "off", "unchecked"})


def normalize_need_filter(saved) -> str:
    """Map a stored show-menu value, or an old checkbox, to a mode name."""
    if saved is None:
        return "needed"
    if isinstance(saved, bool):
        return "needed" if saved else "all"
    text = str(saved).strip().lower()
    if text in MODES:
        return text
    if text in _CHECKED:
        return "needed"
    if text in _UNCHECKED:
        return "all"
    if text == "":
        return "needed"
    return "needed"


def tail_is_free_signoff(message_tail: str | None) -> bool:
    """True when the Message-column tail is 73 or RR73, optionally plus a grid."""
    text = (message_tail or "").strip()
    if not text:
        return False
    return any(tok.upper() in _FREE_SIGNOFF for tok in text.split())


def row_visible(*, is_needed: bool, is_cq: bool, message_tail: str | None,
                mode: str) -> bool:
    """Whether one roster row stays visible for this show-menu mode."""
    chosen = normalize_need_filter(mode)
    if chosen == "all":
        return True
    if not is_needed:
        return False
    if chosen == "needed":
        return True
    if chosen == "needed-cq":
        return bool(is_cq)
    if chosen == "needed-73":
        return bool(is_cq) or tail_is_free_signoff(message_tail)
    return True


def visible_decode(message: str | None, *, is_needed: bool, mode: str) -> bool:
    is_cq, _dx, _to, _grid, tail = interpret(message)
    return row_visible(
        is_needed=is_needed, is_cq=is_cq, message_tail=tail, mode=mode,
    )
