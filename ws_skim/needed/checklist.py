# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# Copied into WS Skim from WS Hub (https://github.com/wa1hco/WS-Hub)
# ``wims.engine.goal_rules`` (checklist / badge wording) for the
# needed-station roster. Award and contest menus are not included.
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
"""Just-operating checklist: why a heard station is needed or not."""
from __future__ import annotations

from dataclasses import dataclass

from ws_skim.needed.cty import continent_name
from ws_skim.needed.places import canonical_place, place_name

CHECKLIST_KINDS = (
    {"id": "dxcc", "label": "New DXCC entity", "available": True, "tip": "A country or DXCC entity you do not already have in the log."},
    {"id": "continent", "label": "New continent", "available": True, "needs_cty": True, "tip": "A continent you do not already have in the log."},
    {"id": "cq", "label": "New CQ zone", "available": True, "needs_cty": True, "tip": "A CQ zone you do not already have in the log."},
    {"id": "itu", "label": "New ITU zone", "available": True, "needs_cty": True, "tip": "An ITU zone you do not already have in the log."},
    {"id": "grid", "label": "New grid", "available": True, "tip": "A grid square you do not already have in the log. Four characters, such as FN42."},
    {"id": "state", "label": "New state or province", "available": True, "tip": "A US state, DC, or Canadian province you do not already have in the log."},
    {"id": "call", "label": "New call", "available": True, "tip": "A callsign you do not already have in the log."},
)
KIND_BY_ID = {row["id"]: row for row in CHECKLIST_KINDS}
KIND_ORDER = tuple(row["id"] for row in CHECKLIST_KINDS)

# One badge when several lines are new. A new call is last (not a multiplier).
NEED_LABEL_PRIORITY = ("grid", "state", "dxcc", "cq", "itu", "continent", "call")
NEED_LABEL = {
    "grid": "grid", "state": "state", "dxcc": "DXCC", "cq": "CQ zone",
    "itu": "ITU zone", "continent": "continent", "call": "call",
    "was": "state", "vucc": "grid",
}
SCOPES = (
    {"id": "overall", "label": "overall"},
    {"id": "band", "label": "per band"},
    {"id": "mode", "label": "per mode"},
)
SCOPE_IDS = frozenset(row["id"] for row in SCOPES)

# WSJT-X factory color categories: New DXCC and New DXCC on Band.
DEFAULT_COLOR_CATEGORIES = {
    "new_dxcc": True, "new_dxcc_band": True,
    "new_grid": False, "new_grid_band": False,
    "new_call": False, "new_call_band": False,
}
_COLOR_KEYS = tuple(DEFAULT_COLOR_CATEGORIES)
_PLACE_KINDS = ("dxcc", "continent", "cq", "itu", "grid", "state", "call")
_MULT_KINDS = frozenset({"dxcc", "continent", "cq", "itu", "grid", "state"})
_NEVER_CONFIRMED = "Worked before, never confirmed."


@dataclass
class Verdict:
    is_dupe: bool
    is_new_mult: bool
    need_reason: str = ""
    mult_reason: str = ""
    points: int = 0
    chase: str = ""
    need_label: str = ""

    @property
    def is_needed(self) -> bool:
        return not self.is_dupe


def kind_available(kind: str) -> bool:
    row = KIND_BY_ID.get(kind)
    return bool(row and row["available"])


def colors_to_checklist(categories, highlight_by_mode: bool = False) -> dict:
    """Colors categories → one checklist line per kind.

    When both overall and per-band are on, the line is per band: that still
    marks the first one and also marks a new band.
    """
    flags = {key: False for key in _COLOR_KEYS}
    for key, val in (categories or {}).items():
        if key in flags:
            flags[key] = bool(val)
    requested: dict[str, set[str]] = {"dxcc": set(), "grid": set(), "call": set()}
    pairs = (
        ("new_dxcc", "dxcc", "overall"), ("new_dxcc_band", "dxcc", "band"),
        ("new_grid", "grid", "overall"), ("new_grid_band", "grid", "band"),
        ("new_call", "call", "overall"), ("new_call_band", "call", "band"),
    )
    for cat, kind, scope in pairs:
        if not flags.get(cat):
            continue
        if highlight_by_mode and kind == "call":
            requested[kind].add("mode")
        else:
            requested[kind].add(scope)
    out = {}
    for kind in KIND_ORDER:
        out[kind] = {"enabled": False, "scope": "overall"}
    for kind, scopes in requested.items():
        if not scopes:
            continue
        if "mode" in scopes:
            scope = "mode"
        elif "band" in scopes:
            scope = "band"
        else:
            scope = "overall"
        out[kind] = {"enabled": True, "scope": scope}
    return out


def operating_checklist() -> dict:
    """Default for standalone Skim: new DXCC and new DXCC on band."""
    return colors_to_checklist(DEFAULT_COLOR_CATEGORIES)


def default_checklist() -> dict:
    """DXCC overall starts on (Hub's empty checklist). Prefer operating_checklist()."""
    out = {}
    for kind in KIND_ORDER:
        out[kind] = {"enabled": kind == "dxcc" and kind_available(kind), "scope": "overall"}
    return out


def normalize_checklist(raw) -> dict:
    base = operating_checklist()
    if raw is None:
        return base
    items: dict = {}
    if isinstance(raw, dict):
        # Either {kind: {enabled, scope}} or a colors-style dict.
        if any(k in raw for k in _COLOR_KEYS) and not any(k in raw for k in KIND_ORDER):
            return colors_to_checklist(raw)
        items = raw
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict) and row.get("kind"):
                items[str(row["kind"])] = row
    else:
        return base
    out = {kind: dict(base[kind]) for kind in KIND_ORDER}
    for kind in KIND_ORDER:
        row = items.get(kind)
        if not isinstance(row, dict):
            continue
        if "enabled" in row:
            out[kind]["enabled"] = bool(row["enabled"]) and kind_available(kind)
        scope = str(row.get("scope") or out[kind]["scope"])
        if scope in SCOPE_IDS:
            out[kind]["scope"] = scope
    return out


def _upper(value) -> str:
    return (value or "").strip().upper()


def _grid4(grid: str | None) -> str | None:
    g = _upper(grid)
    if len(g) >= 4 and g[:2].isalpha() and g[2:4].isdigit():
        return g[:4]
    return None


def _row_mode(row: dict) -> str:
    return _upper(row.get("submode")) or _upper(row.get("mode"))


def _row_state(row: dict) -> str | None:
    found = canonical_place(row.get("state"))
    if found:
        return found
    return canonical_place(row.get("exchange"))


def _entity(call: str, dxcc: str | None, lookup) -> str | None:
    num = (dxcc or "").strip()
    if num:
        return f"dxcc:{num}"
    if lookup is None or not call:
        return None
    try:
        name = lookup(call)
    except Exception:
        return None
    if not name:
        return None
    return f"name:{name}"


def _entity_keys(call: str, dxcc, lookup) -> set[str]:
    keys: set[str] = set()
    one = _entity(call, dxcc, None)
    if one:
        keys.add(one)
    named = _entity(call, None, lookup)
    if named:
        keys.add(named)
    return keys


def _pretty_entity(keys: set[str]) -> str:
    names = [k.split(":", 1)[1] for k in sorted(keys) if k.startswith("name:")]
    if names:
        return names[0]
    nums = [k.split(":", 1)[1] for k in sorted(keys) if k.startswith("dxcc:")]
    if nums:
        return f"DXCC {nums[0]}"
    return "this entity"


def _need_rank(kind: str) -> int:
    try:
        return NEED_LABEL_PRIORITY.index(kind)
    except ValueError:
        return len(NEED_LABEL_PRIORITY)


def _row_confirmed(row: dict) -> bool:
    return bool(row.get("confirmed"))


def _lotw_index():
    from ws_skim.needed.lotw import current
    return current()


def _row_is_confirmed(row: dict, lotw) -> bool:
    if _row_confirmed(row):
        return True
    return bool(lotw is not None and lotw.confirms_row(row))


def _lotw_entity_keys(call: str, dxcc: str | None, lookup) -> set[str]:
    keys: set[str] = set()
    if dxcc:
        keys.add(f"dxcc:{dxcc}")
    named = _entity(call, None, lookup)
    if named:
        keys.add(named)
    return keys


def _apply_lotw_places(built, lotw, lookup, *, grid_kind: str, state_kind: str) -> None:
    if lotw is None or not getattr(lotw, "records", None):
        return
    from ws_skim.needed.lotw import modes_in_group
    for rec in lotw.records:
        _note_station(built, rec.call, True)
        extras = [m for m in modes_in_group(rec.group) if m and m != rec.mode]

        def add(kind: str, key, extras=extras) -> None:
            _remember(built, kind, key, rec.band, rec.mode, rec.call, True)
            if not extras:
                return
            slot = built.setdefault(kind + "_mode_ok", set())
            for mode in extras:
                slot.add((key, mode))

        if rec.grid:
            add(grid_kind, rec.grid)
        if rec.state:
            add(state_kind, rec.state)
        for key in _lotw_entity_keys(rec.call, rec.dxcc, lookup):
            add("dxcc", key)


def _note_station(built: dict, call: str, confirmed: bool) -> None:
    call_u = _upper(call)
    if not call_u:
        return
    built.setdefault("_worked_calls", set()).add(call_u)
    if confirmed:
        built.setdefault("_confirmed_calls", set()).add(call_u)


def _known_nonconfirmer(idx: dict, call: str) -> bool:
    call_u = _upper(call)
    if not call_u or call_u not in (idx.get("_worked_calls") or ()):
        return False
    return call_u not in (idx.get("_confirmed_calls") or ())


def _norm_band(band: str) -> str:
    return (band or "").strip().lower()


def _blank_buckets(built: dict, kind: str, *, scopes: bool = True) -> None:
    built[kind] = set()
    built[kind + "_calls"] = {}
    built[kind + "_ok"] = set()
    if not scopes:
        return
    for suffix in ("_band", "_mode"):
        built[kind + suffix] = set()
        built[kind + suffix + "_calls"] = {}
        built[kind + suffix + "_ok"] = set()


def _remember(built: dict, kind: str, key, band: str, mode: str,
              call: str, confirmed: bool) -> None:
    call_u = _upper(call)
    band_u = _norm_band(band)
    mode_u = _upper(mode)

    def note(bucket, token) -> None:
        built.setdefault(bucket, set()).add(token)
        slot = built.setdefault(bucket + "_calls", {}).setdefault(token, set())
        if call_u:
            slot.add(call_u)
        if confirmed:
            built.setdefault(bucket + "_ok", set()).add(token)

    note(kind, key)
    if band_u:
        note(f"{kind}_band", (key, band_u))
    if mode_u:
        note(f"{kind}_mode", (key, mode_u))


def _where(scope: str, band: str, mode: str) -> str:
    band_u = _norm_band(band)
    mode_u = _upper(mode)
    if scope == "band" and band_u:
        return f" on {band_u}"
    if scope == "mode" and mode_u:
        return f" on {mode_u}"
    return ""


def _place_word(kind: str) -> str:
    if kind == "cq":
        return "CQ zone"
    if kind == "itu":
        return "ITU zone"
    return "continent"


def _uncertain_sentence(notes: list[str]) -> str:
    return ". ".join(notes) + "."


def _with_uncertain(reason: str, notes: list[str]) -> str:
    if not notes:
        return reason
    extra = _uncertain_sentence(notes)
    if "zone uncertain" in reason:
        return reason
    text = reason.rstrip()
    if text.endswith("."):
        return text + " " + extra
    return text + ". " + extra


def _scope_bucket(kind: str, scope: str) -> str:
    if scope == "band":
        return f"{kind}_band"
    if scope == "mode":
        return f"{kind}_mode"
    return kind


def _scope_token(scope: str, key, band: str, mode: str):
    if scope == "band":
        return (key, _norm_band(band))
    if scope == "mode":
        return (key, _upper(mode))
    return key


def _scoped(idx: dict, kind: str, scope: str, key, band: str, mode: str):
    if scope == "band":
        band_u = _norm_band(band)
        if not band_u:
            return None
        return (key, band_u) in idx[f"{kind}_band"]
    if scope == "mode":
        mode_u = _upper(mode)
        if not mode_u:
            return None
        return (key, mode_u) in idx[f"{kind}_mode"]
    return key in idx[kind]


def _mark(idx: dict, kind: str, scope: str, key, band: str, mode: str, call: str):
    flag = _scoped(idx, kind, scope, key, band, mode)
    if flag is None:
        return None
    if not flag:
        return "new"
    token = _scope_token(scope, key, band, mode)
    bucket = _scope_bucket(kind, scope)
    if token in (idx.get(bucket + "_ok") or ()):
        return "confirmed"
    calls = (idx.get(bucket + "_calls") or {}).get(token) or set()
    if _upper(call) and _upper(call) in calls:
        return "same"
    return "unconfirmed"


def _mark_any(idx, kind, scope, keys, band, mode, call):
    saw = False
    opened = False
    same = False
    for key in keys:
        mark = _mark(idx, kind, scope, key, band, mode, call)
        if mark is None:
            continue
        saw = True
        if mark == "confirmed":
            return "confirmed"
        if mark == "same":
            same = True
        elif mark == "unconfirmed":
            opened = True
    if not saw:
        return None
    if same:
        return "same"
    if opened:
        return "unconfirmed"
    return "new"


def _open_text(kind: str, shown, scope: str, band: str, mode: str) -> str:
    where = _where(scope, band, mode)
    if kind == "dxcc":
        what = f"DXCC entity {shown}"
    elif kind == "grid":
        what = f"Grid {shown}"
    elif kind == "state":
        what = str(shown)
    elif kind == "continent":
        what = f"Continent {shown}"
    elif kind == "cq":
        what = f"CQ zone {shown}"
    elif kind == "itu":
        what = f"ITU zone {shown}"
    else:
        what = str(shown)
    return f"{what}{where} is worked but not confirmed."


def _need(kind: str, shown: str, scope: str, band: str, mode: str, label: str) -> str:
    where = ""
    band_u = _norm_band(band)
    mode_u = _upper(mode)
    if scope == "band" and band_u:
        where = f" on {band_u}"
    elif scope == "mode" and mode_u:
        where = f" on {mode_u}"
    if kind == "dxcc":
        what = f"new DXCC entity {shown}"
    elif kind == "grid":
        what = f"new grid {shown}"
    elif kind == "state":
        what = f"new state or province {shown}"
    else:
        what = f"new call {shown}"
    return f"Needed: {what}{where} is not in {label}."


def _confirm_tail(include: bool) -> str:
    if include:
        return " A worked place that is not confirmed still counts as needed."
    return " A worked place that is not confirmed stays off the needed list."


class ChecklistRules:
    """Just-operating checklist. Badge names the most specific new line."""

    def __init__(self, items=None, *, title: str = "Checklist", dxcc_lookup=None,
                 log_label: str = "the log", cty=None, count_unconfirmed: bool = True):
        self.items = normalize_checklist(items)
        self.title = title or "Checklist"
        self.cty = cty
        if dxcc_lookup is None and cty is not None:
            dxcc_lookup = cty.entity_name
        self.dxcc_lookup = dxcc_lookup
        self.log_label = log_label or "the log"
        self.count_unconfirmed = bool(count_unconfirmed)

    def _line_on(self, kind: str) -> bool:
        row = self.items.get(kind) or {}
        if not row.get("enabled") or not kind_available(kind):
            return False
        if KIND_BY_ID[kind].get("needs_cty") and self.cty is None:
            return False
        return True

    def plain_summary(self) -> str:
        parts = []
        held = []
        for kind in KIND_ORDER:
            row = self.items[kind]
            if not row["enabled"] or not kind_available(kind):
                continue
            if KIND_BY_ID[kind].get("needs_cty") and self.cty is None:
                held.append(KIND_BY_ID[kind]["label"])
                continue
            scope_label = next(
                (s["label"] for s in SCOPES if s["id"] == row["scope"]), row["scope"],
            )
            parts.append(f"{KIND_BY_ID[kind]['label']} ({scope_label})")
        if not parts:
            line = "Nothing is checked, so no station will show as needed."
        else:
            line = (
                "Needed when a station matches " + ", ".join(parts) + "."
                + _confirm_tail(self.count_unconfirmed)
            )
        if self.items["dxcc"]["enabled"] and self.dxcc_lookup is None:
            line += " DXCC uses country numbers stored in the log."
        if held:
            line += " " + ", ".join(held) + " is off because the country list was not found."
            if len(held) > 1:
                line = line.replace(" is off because", " are off because", 1)
        return line

    def _index(self, store) -> dict:
        gen = store.generation() if hasattr(store, "generation") else 0
        lotw = _lotw_index()
        cache = getattr(self, "_cache", None)
        if cache and cache[0] == gen and cache[1] is store and cache[2] == lotw.token:
            return cache[3]
        built = {"_worked_calls": set(), "_confirmed_calls": set()}
        for kind in _PLACE_KINDS:
            _blank_buckets(built, kind)
        for row in store.cached_rows():
            band = row.get("band") or ""
            mode = _row_mode(row)
            call = _upper(row.get("call"))
            confirmed = _row_is_confirmed(row, lotw)
            _note_station(built, call, confirmed)
            if call:
                _remember(built, "call", call, band, mode, call, confirmed)
            g = _grid4(row.get("grid"))
            if g:
                _remember(built, "grid", g, band, mode, call, confirmed)
            st = _row_state(row)
            if st:
                _remember(built, "state", st, band, mode, call, confirmed)
            for key in _entity_keys(row.get("call") or "", row.get("dxcc"), self.dxcc_lookup):
                _remember(built, "dxcc", key, band, mode, call, confirmed)
            place = self._describe(
                row.get("call") or "",
                row.get("grid"),
                cqz=row.get("cqz"),
                ituz=row.get("ituz"),
                cont=row.get("cont"),
            )
            if place is None:
                continue
            if place.continent:
                _remember(built, "continent", place.continent, band, mode, call, confirmed)
            if place.cq is not None and not place.cq_uncertain:
                _remember(built, "cq", place.cq, band, mode, call, confirmed)
            if place.itu is not None and not place.itu_uncertain:
                _remember(built, "itu", place.itu, band, mode, call, confirmed)
        _apply_lotw_places(
            built, lotw, self.dxcc_lookup, grid_kind="grid", state_kind="state",
        )
        self._cache = (gen, store, lotw.token, built)
        return built

    def _describe(self, call, grid, *, cqz=None, ituz=None, cont=None):
        if self.cty is None:
            return None
        return self.cty.describe(call, grid, cqz=cqz, ituz=ituz, cont=cont)

    def classify(self, store, call: str, band: str, mode: str | None, grid: str | None,
                 *, exchange: str | None = None, state: str | None = None) -> Verdict:
        idx = self._index(store)
        call_u = _upper(call)
        mode_u = _upper(mode)
        place = self._describe(call_u, grid)
        unknowns: list[str] = []
        worked_notes: list[str] = []
        uncertain: list[str] = []
        checked = 0
        needs: list[tuple[str, str, str]] = []
        first_open = None
        saw_same = False
        saw_confirmed = False
        quiet = _known_nonconfirmer(idx, call_u)
        for kind in KIND_ORDER:
            if not self._line_on(kind):
                continue
            checked += 1
            scope = self.items[kind]["scope"]
            hit = self._hit(
                kind, scope, idx, call_u, band, mode_u, grid, state, exchange, place,
            )
            tag = hit[0]
            if tag == "need":
                needs.append((kind, hit[1], hit[2]))
            elif tag == "unconfirmed" and first_open is None:
                first_open = hit[1]
            elif tag == "same":
                saw_same = True
                worked_notes.append(hit[1])
            elif tag == "worked":
                saw_confirmed = True
                worked_notes.append(hit[1])
            elif tag == "unknown":
                unknowns.append(hit[1])
            elif tag == "uncertain":
                uncertain.append(hit[1])
        if needs:
            kind, reason, mult_reason = min(needs, key=lambda item: _need_rank(item[0]))
            return Verdict(
                False, kind in _MULT_KINDS,
                need_reason=_with_uncertain(reason, uncertain),
                mult_reason=mult_reason if kind in _MULT_KINDS else "",
                points=1,
                chase="needed",
                need_label=NEED_LABEL.get(kind, ""),
            )
        if first_open is not None and quiet:
            return Verdict(
                True, False,
                need_reason=_with_uncertain(_NEVER_CONFIRMED, uncertain),
                points=1,
            )
        if first_open is not None:
            include = bool(self.count_unconfirmed)
            return Verdict(
                not include, False,
                need_reason=_with_uncertain(first_open, uncertain),
                points=1,
                chase="unconfirmed",
            )
        if checked and not worked_notes and len(unknowns) + len(uncertain) >= checked:
            if uncertain:
                return Verdict(
                    True, False,
                    need_reason=_uncertain_sentence(uncertain),
                    points=1,
                )
            return Verdict(
                True, False,
                need_reason="WS Skim cannot tell yet: " + ", ".join(unknowns) + ".",
                points=1,
            )
        if not checked:
            return Verdict(
                True, False,
                need_reason="Nothing is checked, so no station will show as needed.",
                points=1,
            )
        if worked_notes:
            note = "Not needed: " + "; ".join(worked_notes) + "."
            if saw_same and not saw_confirmed and quiet:
                note += " " + _NEVER_CONFIRMED
        else:
            note = f"Not needed: nothing checked is new in {self.log_label}."
        return Verdict(
            True, False,
            need_reason=_with_uncertain(note, uncertain),
            points=1,
        )

    def _hit(self, kind, scope, idx, call, band, mode, grid, state, exchange, place):
        label = self.log_label
        where = _where(scope, band, mode)
        if kind == "call":
            if not call:
                return ("unknown", "callsign", "")
            worked = _scoped(idx, "call", scope, call, band, mode)
            if worked is None:
                return ("unknown", "callsign", "")
            if worked:
                return ("worked", f"call {call}{where} is already in {label}", "")
            return ("need", _need("call", call, scope, band, mode, label), "")
        if kind == "grid":
            g = _grid4(grid)
            if not g:
                return ("unknown", "grid", "")
            return self._place_mark(
                "grid", scope, idx, g, band, mode, call,
                have=f"grid {g}{where} is already in {label}",
                need=_need("grid", g, scope, band, mode, label),
                mult=f"New mult: grid {g}{where} in {label}.",
                open_text=_open_text("grid", g, scope, band, mode),
                unknown="grid",
            )
        if kind == "state":
            abbr = canonical_place(state) or canonical_place(exchange)
            if not abbr:
                return ("unknown", "state", "")
            name = place_name(abbr)
            return self._place_mark(
                "state", scope, idx, abbr, band, mode, call,
                have=f"{name}{where} is already in {label}",
                need=_need("state", name, scope, band, mode, label),
                mult=f"New mult: {name}{where} in {label}.",
                open_text=_open_text("state", name, scope, band, mode),
                unknown="state",
            )
        if kind in {"continent", "cq", "itu"}:
            return self._place_hit(kind, scope, idx, band, mode, place, call)
        keys = _entity_keys(call, None, self.dxcc_lookup)
        if not keys:
            return ("unknown", "DXCC entity", "")
        mark = _mark_any(idx, "dxcc", scope, keys, band, mode, call)
        if mark is None:
            return ("unknown", "DXCC entity", "")
        shown = _pretty_entity(keys)
        return self._tag_mark(
            mark,
            have=f"DXCC entity {shown}{where} is already in {label}",
            need=_need("dxcc", shown, scope, band, mode, label),
            mult=f"New mult: DXCC entity {shown}{where} in {label}.",
            open_text=_open_text("dxcc", shown, scope, band, mode),
            unknown="DXCC entity",
        )

    def _place_mark(self, kind, scope, idx, key, band, mode, call, *,
                    have, need, mult, open_text, unknown):
        mark = _mark(idx, kind, scope, key, band, mode, call)
        return self._tag_mark(
            mark, have=have, need=need, mult=mult,
            open_text=open_text, unknown=unknown,
        )

    def _tag_mark(self, mark, *, have, need, mult, open_text, unknown):
        if mark is None:
            return ("unknown", unknown, "")
        if mark == "new":
            return ("need", need, mult)
        if mark == "unconfirmed":
            return ("unconfirmed", open_text, "")
        if mark == "same":
            return ("same", have, "")
        return ("worked", have, "")

    def _place_hit(self, kind, scope, idx, band, mode, place, call):
        label = self.log_label
        where = _where(scope, band, mode)
        if place is None:
            return ("unknown", _place_word(kind), "")
        if kind == "continent":
            code = place.continent
            if not code:
                return ("unknown", "continent", "")
            shown = continent_name(code)
            key = code
            need = f"Need: continent {shown}{where} in {label}."
            mult = f"New mult: continent {shown}{where} in {label}."
            have = f"continent {shown}{where} is already in {label}"
        elif kind == "cq":
            if place.cq_uncertain:
                return ("uncertain", "CQ zone uncertain", "")
            if place.cq is None:
                return ("unknown", "CQ zone", "")
            key = place.cq
            need = f"Need: CQ zone {key}{where} in {label}."
            mult = f"New mult: CQ zone {key}{where} in {label}."
            have = f"CQ zone {key}{where} is already in {label}"
            shown = key
        else:
            if place.itu_uncertain:
                return ("uncertain", "ITU zone uncertain", "")
            if place.itu is None:
                return ("unknown", "ITU zone", "")
            key = place.itu
            need = f"Need: ITU zone {key}{where} in {label}."
            mult = f"New mult: ITU zone {key}{where} in {label}."
            have = f"ITU zone {key}{where} is already in {label}"
            shown = key
        if kind == "continent":
            open_text = _open_text("continent", shown, scope, band, mode)
            unknown = "continent"
        elif kind == "cq":
            open_text = _open_text("cq", key, scope, band, mode)
            unknown = "CQ zone"
        else:
            open_text = _open_text("itu", key, scope, band, mode)
            unknown = "ITU zone"
        return self._place_mark(
            kind, scope, idx, key, band, mode, call,
            have=have, need=need, mult=mult,
            open_text=open_text, unknown=unknown,
        )

    def public(self) -> dict:
        items = []
        for kind in KIND_ORDER:
            row = self.items[kind]
            meta = KIND_BY_ID[kind]
            items.append({
                "kind": kind,
                "label": meta["label"],
                "enabled": bool(row["enabled"]),
                "scope": row["scope"],
                "available": kind_available(kind) and not (
                    meta.get("needs_cty") and self.cty is None
                ),
                "tip": meta["tip"],
            })
        return {
            "items": items,
            "summary": self.plain_summary(),
            "count_unconfirmed": self.count_unconfirmed,
            "log_label": self.log_label,
        }
