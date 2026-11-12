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
"""Environment variables with the ``WS_SKIM_`` prefix.

The app was called map144, and station setups and test scripts use
``MAP144_*`` variables.  ``getenv("JT9")`` reads ``WS_SKIM_JT9`` first and
falls back to ``MAP144_JT9``, so both spellings keep working.
"""
from __future__ import annotations

import os

PREFIXES = ("WS_SKIM_", "MAP144_")


def getenv(name: str, default: str | None = None) -> str | None:
    """Return ``WS_SKIM_<name>``, else ``MAP144_<name>``, else *default*."""
    for prefix in PREFIXES:
        value = os.environ.get(prefix + name)
        if value is not None:
            return value
    return default
