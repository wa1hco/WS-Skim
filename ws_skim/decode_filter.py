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
"""FT8 / FT4 false-decode filter: drop decodes that cannot be real.

jt9 at depth 3 (WSJT-X "Deep") finds weak signals WSJT-X misses, but a
few of its decodes are noise forced into a message.  Each finished
period's decodes go through these checks before they are shown or sent
to WS Hub; the ones that fail are counted, logged and written to
``rejected.jsonl`` instead.

``ap``
    A priori decodes that assume a QSO partner (jt9 ``a2``..``a8``) and
    anything naming jt9's default calls K1ABC / W9XYZ.  WS Skim is in no
    QSO, so these can only be wrong.  ``a1`` (CQ) decodes are kept.
``call``
    A callsign that is not shaped like one (prefix, area digit, suffix) or
    whose prefix is not allocated in the country file (``cty.dat``).
``grid``
    A grid far from the sender's country: farther than the country's
    radius (:data:`DEFAULT_RADIUS_KM`, larger for big countries) from its
    ``cty.dat`` centre and any of its :data:`EXTRA_CENTRES`.  /MM and /AM
    calls are not checked.
``dup``
    The same message twice in one period (two overlapping jt9 sections):
    one is kept, the best SNR.

Free text (anything that does not parse as a standard message) is left
alone.  Pure functions; the country file loads once, on first use.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Reasons, in the order they are checked and reported.
REASONS = ("ap", "call", "grid", "dup")

#: jt9's built-in MyCall / DxCall: never real on the air.
DEFAULT_CALLS = frozenset({"K1ABC", "W9XYZ"})

CTY_PATH = Path(__file__).resolve().parent / "data" / "cty.dat"

#: A country's decodes may carry a grid this far from its cty.dat centre.
DEFAULT_RADIUS_KM = 3500.0
#: Bigger countries (cty.dat entity name -> km).
RADIUS_KM = {
    "United States": 3600.0,
    "Canada": 5000.0,
    "European Russia": 3500.0,
    "Asiatic Russia": 5500.0,
    "Kaliningrad": 3500.0,
    "Brazil": 3500.0,
    "Australia": 3200.0,
    "China": 3200.0,
    "Argentina": 3000.0,
    "Chile": 3000.0,
    "Indonesia": 3500.0,
    "Kazakhstan": 2800.0,
    "Antarctica": 4000.0,
}
#: Other centres a country's operators commonly sit at without a prefix
#: change (US calls in Alaska / Hawaii, Russians on either side of the Urals).
_US_FAMILY = ("United States", "Alaska", "Hawaii", "Puerto Rico", "Guam",
              "US Virgin Islands", "American Samoa", "Mariana Islands")
EXTRA_CENTRES = {
    **{n: tuple(m for m in _US_FAMILY if m != n) for n in _US_FAMILY},
    "European Russia": ("Asiatic Russia",),
    "Asiatic Russia": ("European Russia",),
    "Kaliningrad": ("European Russia", "Asiatic Russia"),
}
#: Grids this far south are Antarctica, where operators often use their
#: home calls: no grid check.
ANTARCTIC_LAT = -60.0
#: Calls with these suffixes are at sea or in the air: no grid check.
MOBILE_SUFFIXES = frozenset({"MM", "AM"})
#: Portable / power suffixes that do not change the country.
PLAIN_SUFFIXES = frozenset({"P", "M", "R", "A", "B", "QRP", "QRPP", "LH", "J", "N"})

_CALL_RE = re.compile(r"([A-Z0-9]*?)([0-9]+)([A-Z]+)")
_GRID_RE = re.compile(r"[A-R]{2}[0-9]{2}")
_REPORT_RE = re.compile(r"R?[+-][0-9]{2}")
_TOKEN_CALLISH = re.compile(r"(?=.*[0-9])(?=.*[A-Z])[A-Z0-9/]{3,}")
_CQ_MOD_RE = re.compile(r"[A-Z]{1,4}|[0-9]{3}")
_ACKS = frozenset({"RRR", "RR73", "73"})


# ── Country file ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entity:
    name: str
    prefix: str
    lat: float
    lon: float          # degrees east


class CountryFile:
    """Prefix and exact-call lookup in a ``cty.dat`` (CT format)."""

    def __init__(self, text: str):
        self.prefixes: dict[str, Entity] = {}
        self.exact: dict[str, Entity] = {}
        self.by_name: dict[str, Entity] = {}
        ent = None
        for raw in text.splitlines():
            if not raw.strip():
                continue
            if not raw.startswith((" ", "\t")):
                f = [x.strip() for x in raw.split(":")]
                if len(f) < 8:
                    continue
                ent = Entity(name=f[0], prefix=f[7].lstrip("*"),
                             lat=float(f[4]), lon=-float(f[5]))
                self.by_name.setdefault(ent.name, ent)
                continue
            if ent is None:
                continue
            for tok in raw.strip().rstrip(";").split(","):
                tok = re.sub(r"\(.*?\)|\[.*?\]|\{.*?\}|~.*?~", "", tok.strip())
                lat_lon = re.search(r"<([-0-9.]+)/([-0-9.]+)>", tok)
                tok = re.sub(r"<.*?>", "", tok)
                if not tok:
                    continue
                e = ent
                if lat_lon:
                    e = Entity(ent.name, ent.prefix, float(lat_lon.group(1)),
                               -float(lat_lon.group(2)))
                if tok.startswith("="):
                    self.exact[tok[1:]] = e
                else:
                    self.prefixes[tok] = e
        self._max_len = max((len(p) for p in self.prefixes), default=0)

    def match_prefix(self, text: str) -> Entity | None:
        """Longest cty.dat prefix that ``text`` starts with."""
        for n in range(min(len(text), self._max_len), 0, -1):
            e = self.prefixes.get(text[:n])
            if e is not None:
                return e
        return None

    def lookup(self, call: str) -> Entity | None:
        """Country of ``call`` (exact entries first, then the prefix)."""
        call = call.upper()
        if call in self.exact:
            return self.exact[call]
        base, prefix_part, _mobile = split_call(call)
        if base is None:
            return None
        if call.split("/")[0] in self.exact and prefix_part is None:
            return self.exact[call.split("/")[0]]
        return self.match_prefix(prefix_part or base)


@lru_cache(maxsize=1)
def country_file() -> CountryFile:
    return CountryFile(CTY_PATH.read_text(encoding="latin-1"))


# ── Callsigns ──────────────────────────────────────────────────────────────

def split_call(call: str) -> tuple[str | None, str | None, bool]:
    """``(home call, location prefix or None, mobile)`` of a decoded call.

    ``W1ABC/P`` -> ``("W1ABC", None, False)``; ``PJ4/K1ABC`` and
    ``K1ABC/KH6`` -> ``("K1ABC", "PJ4"/"KH6", False)``; ``W1ABC/MM`` ->
    mobile.  A call with more than one non-suffix part besides the home
    call is not understood: ``(None, None, False)``.
    """
    parts = [p for p in call.upper().split("/") if p]
    mobile = False
    keep = []
    for i, p in enumerate(parts):
        if i > 0 and p in MOBILE_SUFFIXES:
            mobile = True
        elif i > 0 and (p in PLAIN_SUFFIXES or p.isdigit()):
            continue
        else:
            keep.append(p)
    if len(keep) == 1:
        return keep[0], None, mobile
    if len(keep) == 2:
        a, b = keep
        home, loc = (b, a) if len(a) < len(b) else (a, b)
        if len(loc) == len(home):
            home, loc = keep[0], keep[1]
        return home, loc, mobile
    return None, None, mobile


_LOOSE_CALL_RE = re.compile(r"[A-Z0-9]{0,4}[0-9][A-Z]{1,5}")


def is_standard_call(call: str) -> bool:
    """A call FT8/FT4 sends in its compact 28-bit form: a 1-2 character
    prefix with a letter (``W``, ``Y2``, ``3D``), one area digit, 1-3
    suffix letters.  Other calls (``210WAI``,
    ``PC40EUDXF``) travel as plain text or hashed."""
    # WSJT-X's rule: the area digit is the third character (``Y27VQU`` is
    # prefix Y2, area 7), or the second when the third is a letter (``W1AW``).
    if not 3 <= len(call) <= 6 or not call.isalnum():
        return False
    i = 2 if call[2].isdigit() else 1
    prefix, suffix = call[:i], call[i + 1:]
    return (call[i].isdigit() and bool(re.search("[A-Z]", prefix))
            and 1 <= len(suffix) <= 3 and suffix.isalpha())


def nonstandard_shape_ok(call: str) -> bool:
    """A plain-text call: up to 11 characters, letters after the last
    digit (``210WAI``, ``PC40EUDXF``); random text (``OZ5O72JEAB4``,
    ``GIUACM0ZCDR``) fails."""
    return len(call) <= 11 and bool(_LOOSE_CALL_RE.fullmatch(call))


def call_shape_ok(call: str) -> bool:
    """A home call shaped like one: prefix (1-3 characters, at least one
    letter), area digit(s), suffix letters; ``K1ABC``, ``3D2Y``, ``9A1A``,
    ``TM50EU`` yes; ``0R4ZGT`` passes here and fails the prefix lookup."""
    if not 3 <= len(call) <= 10:
        return False
    m = _CALL_RE.fullmatch(call)
    if not m:
        return False
    prefix, digits, suffix = m.groups()
    if not prefix or len(prefix) > 3 or not re.search("[A-Z]", prefix):
        return False
    return len(digits) <= 4 and len(suffix) <= 4


def location_prefix_ok(loc: str) -> bool:
    """The location part of a compound call: ``KH6``, ``PJ4``, ``VP2E``, ``F``."""
    return bool(re.fullmatch(r"[A-Z0-9]{1,4}", loc)) and bool(re.search("[A-Z]", loc))


def call_problem(call: str, cty: CountryFile | None = None, *,
                 sender: bool = True) -> str | None:
    """Why ``call`` cannot be real (None if it can).  Hashed calls pass.

    Every call must be shaped like one.  A standard (compact) call must
    also have an allocated prefix when it is the ``sender``: a station
    may call a miscopied call (``KC7UG`` answering ``Y95OWI``), but a
    false decode scrambles the sender too.  Plain-text calls are CRC
    protected text, so only their shape is checked.
    """
    c = call.strip().upper()
    if c.startswith("<") and c.endswith(">"):
        c = c[1:-1]
        if not c or c == "...":
            return None
    cty = cty or country_file()
    if c in cty.exact:
        return None
    home, loc, _mobile = split_call(c)
    if home is None:
        return f"{call}: not a callsign"
    if loc is not None and not location_prefix_ok(loc):
        return f"{call}: bad location prefix"
    if loc is None and "/" not in c and not is_standard_call(home):
        return None if nonstandard_shape_ok(home) else f"{call}: not a callsign"
    if not call_shape_ok(home):
        return f"{call}: not a callsign"
    if sender and cty.lookup(c) is None:
        return f"{call}: prefix not allocated"
    return None


# ── Messages ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Parsed:
    calls: tuple[str, ...]        # every callsign in the message
    sender: str | None            # whose grid it is
    grid: str | None


def _is_callish(tok: str) -> bool:
    if tok.startswith("<") and tok.endswith(">"):
        return True
    return bool(_TOKEN_CALLISH.fullmatch(tok))


def _is_grid(tok: str) -> bool:
    return bool(_GRID_RE.fullmatch(tok)) and tok != "RR73"


def _is_tail(tok: str) -> bool:
    return tok in _ACKS or bool(_REPORT_RE.fullmatch(tok)) or _is_grid(tok)


def parse_message(msg: str) -> Parsed | None:
    """Calls and grid of a standard FT8/FT4 message, None for free text.

    ``CQ [DX|NA|TEST|nnn] CALL [GRID]``, ``CALL1 CALL2 [R] [GRID|report|
    RRR|RR73|73]`` and the Fox ``CALL1 RR73; CALL2 <CALL3> report``.
    """
    msg = msg.strip().rstrip("?").strip()
    toks = msg.split()
    if not toks:
        return None
    if ";" in msg:                                   # Fox / Hound multi-message
        calls = tuple(t.rstrip(";") for t in toks
                      if _is_callish(t.rstrip(";")) and not _is_tail(t.rstrip(";")))
        return Parsed(calls, None, None) if calls else None
    if toks[0] in ("CQ", "QRZ"):
        rest = toks[1:]
        if len(rest) >= 2 and not _is_callish(rest[0]) and _CQ_MOD_RE.fullmatch(rest[0]):
            rest = rest[1:]                          # CQ DX / CQ NA / CQ 290
        if not rest or not _is_callish(rest[0]):
            return None
        if len(rest) == 1:
            return Parsed((rest[0],), rest[0], None)
        if len(rest) == 2 and _is_grid(rest[1]):
            return Parsed((rest[0],), rest[0], rest[1])
        return None
    if len(toks) < 2 or len(toks) > 4 or not (_is_callish(toks[0]) and _is_callish(toks[1])):
        return None
    tail = toks[2:]
    if tail and tail[0] == "R" and len(tail) == 2:
        tail = tail[1:]
    if len(tail) > 1 or (tail and not _is_tail(tail[0])):
        return None
    grid = tail[0] if tail and _is_grid(tail[0]) else None
    return Parsed((toks[0], toks[1]), toks[1], grid)


# ── Grids ──────────────────────────────────────────────────────────────────

def grid_latlon(grid: str) -> tuple[float, float]:
    """Centre (lat, lon east) of a 4-character Maidenhead square."""
    g = grid.upper()
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2 + 1.0
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3]) * 1 + 0.5
    return lat, lon


def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 6371.0 * math.acos(max(-1.0, min(1.0, c)))


def grid_problem(sender: str, grid: str, cty: CountryFile | None = None) -> str | None:
    """Why ``grid`` cannot be ``sender``'s (None if it can, or unknown)."""
    s = sender.strip().upper()
    if s.startswith("<"):
        return None                                  # hashed: unknown country
    cty = cty or country_file()
    _home, _loc, mobile = split_call(s)
    if mobile:
        return None
    ent = cty.lookup(s)
    if ent is None:
        return None                                  # the call check reports it
    lat, lon = grid_latlon(grid)
    if lat <= ANTARCTIC_LAT:
        return None
    centres = [ent] + [cty.by_name[n] for n in EXTRA_CENTRES.get(ent.name, ())
                       if n in cty.by_name]
    for e in centres:
        if distance_km(lat, lon, e.lat, e.lon) <= RADIUS_KM.get(e.name, DEFAULT_RADIUS_KM):
            return None
    d = distance_km(lat, lon, ent.lat, ent.lon)
    limit = RADIUS_KM.get(ent.name, DEFAULT_RADIUS_KM)
    return f"{grid} is {d:.0f} km from {ent.name} (limit {limit:.0f})"


# ── One period ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rejection:
    decode: object
    reason: str          # one of REASONS
    detail: str


def ap_problem(d) -> str | None:
    """A priori decode that assumes a QSO partner, or jt9's default calls."""
    ap = int(getattr(d, "ap_type", 0) or 0)
    if ap >= 2:
        return f"a{ap} a priori decode (assumes a QSO partner)"
    # Plain tokens only: a hashed "<K1ABC>" is some other call whose hash
    # matched jt9's MyCall hash.
    toks = {t.rstrip(";") for t in str(d.message).upper().split() if not t.startswith("<")}
    hit = toks & DEFAULT_CALLS
    if hit:
        return f"jt9 default call {sorted(hit)[0]}"
    return None


def check_decode(d, cty: CountryFile | None = None) -> tuple[str, str] | None:
    """``(reason, detail)`` if decode ``d`` cannot be real, else None."""
    why = ap_problem(d)
    if why:
        return "ap", why
    p = parse_message(str(d.message))
    if p is None:
        return None
    cty = cty or country_file()
    for c in p.calls:
        why = call_problem(c, cty, sender=(c == p.sender))
        if why:
            return "call", why
    if p.grid and p.sender:
        why = grid_problem(p.sender, p.grid, cty)
        if why:
            return "grid", why
    return None


def filter_period(decodes, cty: CountryFile | None = None) -> tuple[list, list[Rejection]]:
    """Split one period's decodes (one mode) into (kept, rejected).

    Kept decodes keep their order; of several decodes of the same message
    the best SNR is kept and the others are ``dup``.
    """
    cty = cty or country_file()
    rejected: list[Rejection] = []
    ok = []
    for d in decodes:
        why = check_decode(d, cty)
        if why:
            rejected.append(Rejection(d, *why))
        else:
            ok.append(d)
    best: dict[str, object] = {}
    for d in ok:
        key = " ".join(str(d.message).split())
        b = best.get(key)
        if b is None or d.snr_db > b.snr_db:
            best[key] = d
    kept = []
    for d in ok:
        key = " ".join(str(d.message).split())
        if best[key] is d:
            kept.append(d)
        else:
            b = best[key]
            rejected.append(Rejection(d, "dup", f"same message at {b.rf_hz:.0f} Hz"))
    return kept, rejected


def counts(rejected) -> dict[str, int]:
    out = {r: 0 for r in REASONS}
    for x in rejected:
        out[x.reason] = out.get(x.reason, 0) + 1
    return out


def summary(rejected) -> str:
    """``filtered 3: ap 1, call 1, grid 0, dup 1``."""
    c = counts(rejected)
    return f"filtered {len(rejected)}: " + ", ".join(f"{r} {c[r]}" for r in REASONS)
