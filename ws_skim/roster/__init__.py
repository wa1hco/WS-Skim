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


"""Browser roster: session, local HTTP + SSE server, and window opener."""

from .model import DecodeList, RosterSession, add_queue_record
from .server import RosterServer, DEFAULT_PORTS
from .browser import open_roster_url, should_open_browser

__all__ = [
    "DecodeList", "RosterSession", "add_queue_record",
    "RosterServer", "DEFAULT_PORTS",
    "open_roster_url", "should_open_browser",
]
