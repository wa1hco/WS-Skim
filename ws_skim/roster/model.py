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


"""In-memory decode list and one-row-per-station roster (Hub-style)."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ws_skim.needed.bands import band_label_khz
from ws_skim.needed.checklist import ChecklistRules, operating_checklist
from ws_skim.needed.cty import CtyIndex, find_cty_dat
from ws_skim.needed.logs import (
    LogStore, discover_adif_paths, load_first, poll_changed, reload_path,
)
from ws_skim.needed.message import interpret

TTL_S = 300.0
MAX_DECODES = 2000


def _base_call(call: str | None) -> str:
    text = (call or "").strip().upper()
    if not text:
        return ""
    return text.split("/")[0]


def _grid4(grid: str | None) -> str:
    g = (grid or "").strip().upper()
    if len(g) >= 4 and g[:2].isalpha() and g[2:4].isdigit():
        return g[:4]
    return ""


def _bearing_deg(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class DecodeRecord:
    epoch: float
    utc_time: str
    mode: str
    radio_khz: float
    df_hz: int | None
    message: str
    snr: int | None = None
    theta_deg: float | None = None
    wav_path: str | None = None
    call: str | None = None
    to_call: str | None = None
    grid: str | None = None
    is_cq: bool = False
    message_tail: str = ""
    band: str = "?"

    def __post_init__(self):
        is_cq, dx, to, grid, tail = interpret(self.message)
        if not self.call:
            self.call = dx
        if not self.to_call:
            self.to_call = to
        if not self.grid:
            self.grid = _grid4(grid) or None
        self.is_cq = bool(is_cq)
        self.message_tail = tail or ""
        if self.band in {"", "?"}:
            self.band = band_label_khz(self.radio_khz) if self.radio_khz else "?"


class DecodeList:
    """Headless list of recent decodes. Stands in for the old Qt table.

    ``engine.decode_panel`` is this object: ``add_decode``, ``recent_count``,
    ``clear``, and a no-op ``recolor`` so the 15 s age timer stays harmless.
    """

    def __init__(self, on_decode=None, on_clear=None, max_rows: int = MAX_DECODES):
        self._rows: list[DecodeRecord] = []
        self._on_decode = on_decode
        self._on_clear = on_clear
        self.max_rows = max_rows

    def __len__(self):
        return len(self._rows)

    def add_decode(self, *, epoch: float, utc_time: str, mode: str, radio_khz: float,
                   df_hz: int | None = None, message: str, snr: int | None = None,
                   theta_deg: float | None = None, wav_path: str | None = None,
                   now: float | None = None) -> DecodeRecord:
        rec = DecodeRecord(
            epoch=float(epoch), utc_time=str(utc_time or ""), mode=str(mode or ""),
            radio_khz=float(radio_khz or 0.0), df_hz=df_hz, message=str(message or ""),
            snr=int(snr) if snr is not None else None,
            theta_deg=theta_deg, wav_path=wav_path,
        )
        self._rows.append(rec)
        overflow = len(self._rows) - self.max_rows
        if overflow > 0:
            del self._rows[:overflow]
        if self._on_decode is not None:
            self._on_decode(rec, now=now)
        return rec

    def recent_count(self, seconds: float, mode: str | None = None,
                     now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        n = 0
        for rec in self._rows:
            if now - rec.epoch > seconds:
                continue
            if mode is not None and rec.mode != mode:
                continue
            n += 1
        return n

    def clear(self) -> None:
        self._rows.clear()
        if self._on_clear is not None:
            self._on_clear()

    def recolor(self, now: float | None = None) -> None:  # noqa: ARG002
        return

    def records(self) -> list[DecodeRecord]:
        return list(self._rows)


def add_queue_record(panel: DecodeList, result: dict, calling_freq_mhz: float) -> DecodeRecord:
    """Add one decoded record from ``engine._decode_queue``."""
    from ws_skim.band_activity_window import record_time

    mode = str(result.get("mode") or "MSK144")
    radio_khz = float(result.get("radio_khz", 0.0) or 0.0)
    if mode == "MSK144" or result.get("audio_hz") is None:
        df = int(round((radio_khz - float(calling_freq_mhz) * 1000.0) * 1000.0))
    else:
        df = int(round(float(result["audio_hz"])))
    epoch = record_time(result)
    snr = result.get("jt9_snr")
    utc = str(result.get("utc_time") or time.strftime("%H:%M:%S", time.gmtime(epoch)))
    return panel.add_decode(
        epoch=epoch, utc_time=utc, mode=mode, radio_khz=radio_khz, df_hz=df,
        message=str(result.get("message") or "?"),
        snr=int(snr) if snr is not None else None,
        theta_deg=result.get("theta_deg"), wav_path=result.get("wav_path"),
    )


@dataclass
class StationEntry:
    rec: DecodeRecord
    last_seen: float
    grid: str = ""
    grid_remembered: bool = False


class RosterSession:
    """Live station roster + worked-before log + checklist."""

    def __init__(self, *, my_call: str = "", my_grid: str = "",
                 checklist=None, log_path: str = "", cty_path: str | None = None,
                 ttl: float = TTL_S, station_getter=None):
        self.my_call = (my_call or "").strip().upper()
        self.my_grid = _grid4(my_grid)
        self._station_getter = station_getter
        self.ttl = float(ttl)
        self.log = LogStore()
        self._entries: dict[tuple[str, str], StationEntry] = {}
        self.decodes = DecodeList(on_decode=self._on_decode, on_clear=self._entries.clear)
        path = cty_path or find_cty_dat()
        self.cty = CtyIndex.load(path) if path else None
        self.rules = ChecklistRules(
            checklist if checklist is not None else operating_checklist(),
            cty=self.cty, log_label=self.log.log_label,
        )
        if log_path:
            reload_path(log_path, self.log, title=PathLabel(log_path))
        else:
            self.discover_and_load()
        self.rules.log_label = self.log.log_label

    def discover_and_load(self, extra: list[str] | None = None) -> None:
        cands = discover_adif_paths(extra=extra)
        load_first(cands, self.log)
        self.rules.log_label = self.log.log_label

    def set_log(self, path: str, *, title: str | None = None) -> bool:
        ok = reload_path(path, self.log, title=title or PathLabel(path))
        self.rules.log_label = self.log.log_label
        self.rules._cache = None
        return ok

    def poll_log(self) -> bool:
        if not poll_changed(self.log):
            return False
        if not self.log.path:
            return False
        ok = reload_path(self.log.path, self.log, title=self.log.log_label)
        self.rules.log_label = self.log.log_label
        self.rules._cache = None
        return ok

    def set_checklist(self, items) -> None:
        from ws_skim.needed.checklist import normalize_checklist
        self.rules.items = normalize_checklist(items)
        self.rules._cache = None

    def set_station(self, my_call: str = "", my_grid: str = "") -> None:
        if my_call:
            self.my_call = my_call.strip().upper()
        if my_grid:
            self.my_grid = _grid4(my_grid)

    def clear(self) -> None:
        self.decodes.clear()
        self._entries.clear()

    def _on_decode(self, rec: DecodeRecord, now: float | None = None) -> None:
        self.observe(rec, now=now)

    def observe(self, rec: DecodeRecord, now: float | None = None) -> None:
        now = rec.epoch if now is None else float(now)
        call = (rec.call or "").strip().upper()
        if not call:
            return
        grid = _grid4(rec.grid)
        key = (call, grid)
        if grid:
            # A later report with a grid is its own row (rover).
            ent = self._entries.get(key)
            if ent is None:
                # Absorb a previous no-grid row for this call.
                bare = self._entries.pop((call, ""), None)
                ent = StationEntry(rec=rec, last_seen=now, grid=grid)
                if bare is not None and not bare.grid:
                    pass
                self._entries[key] = ent
            else:
                ent.rec = rec
                ent.last_seen = now
                ent.grid = grid
                ent.grid_remembered = False
            return
        # No grid on this decode: update the newest existing row for the call.
        existing = [(k, e) for k, e in self._entries.items() if k[0] == call]
        if existing:
            k, ent = max(existing, key=lambda item: item[1].last_seen)
            ent.rec = rec
            ent.last_seen = now
            if ent.grid:
                ent.grid_remembered = True
            return
        self._entries[(call, "")] = StationEntry(rec=rec, last_seen=now, grid="")

    def _prune(self, now: float) -> None:
        drop = [k for k, e in self._entries.items() if now - e.last_seen > self.ttl]
        for k in drop:
            del self._entries[k]

    def snapshot(self, now: float | None = None) -> dict:
        """JSON close to Hub's ``roster_to_dict`` plus log / checklist."""
        now = time.time() if now is None else float(now)
        if self._station_getter is not None:
            try:
                call, grid = self._station_getter()
                self.set_station(call or "", grid or "")
            except Exception:
                pass
        try:
            self.poll_log()
        except Exception:
            pass
        self._prune(now)
        my = _base_call(self.my_call)
        cands = []
        needed = 0
        unconfirmed = 0
        not_needed = 0
        live_bands = []
        seen_bands = set()
        for (call, _g), ent in self._entries.items():
            rec = ent.rec
            grid = ent.grid or _grid4(rec.grid)
            verdict = self.rules.classify(
                self.log, call, rec.band, rec.mode, grid or None,
            )
            is_needed = not verdict.is_dupe
            if is_needed:
                needed += 1
                if verdict.chase == "unconfirmed":
                    unconfirmed += 1
            else:
                not_needed += 1
            place = self.cty.country_fields(call, grid) if self.cty is not None else {
                "country": "", "continent": "", "cq_zone": None,
            }
            az = dist = None
            bearing_source = None
            if grid and self.my_grid:
                try:
                    from ws_skim.geo import grid_to_latlon
                    de = grid_to_latlon(self.my_grid)
                    dx = grid_to_latlon(grid)
                    if de is not None and dx is not None:
                        az = round(_bearing_deg(de[0], de[1], dx[0], dx[1]))
                        dist = round(_haversine_km(de[0], de[1], dx[0], dx[1]))
                        bearing_source = "grid"
                except Exception:
                    pass
            if az is None and self.cty is not None and self.my_grid:
                try:
                    from ws_skim.geo import grid_to_latlon
                    de = grid_to_latlon(self.my_grid)
                    mid = self.cty.centroid(call)
                    if de is not None and mid is not None:
                        az = round(_bearing_deg(de[0], de[1], mid[0], mid[1]))
                        dist = round(_haversine_km(de[0], de[1], mid[0], mid[1]))
                        bearing_source = "dxcc"
                except Exception:
                    pass
            to = rec.to_call or ""
            tail = rec.message_tail or ""
            is_signoff = any(tok.upper() in {"73", "RR73", "RRR"} for tok in tail.split())
            is_calling_us = bool(
                my and not verdict.is_dupe and not is_signoff and not rec.is_cq
                and to and to.upper() not in {"CQ", "QRZ", "DE"}
                and _base_call(to) == my
            )
            freq_hz = int(round(rec.radio_khz * 1000.0)) if rec.radio_khz else None
            if rec.band and rec.band not in seen_bands:
                seen_bands.add(rec.band)
                live_bands.append(rec.band)
            cands.append({
                "id": f"{call}|{grid}",
                "call": call,
                "country": place.get("country") or "",
                "continent": place.get("continent") or "",
                "cq_zone": place.get("cq_zone"),
                "to_call": to,
                "msg": tail,
                "message_tail": tail,
                "message": rec.message,
                "is_qsy": False,
                "grid": grid or None,
                "grid_remembered": bool(ent.grid_remembered),
                "grid_assumed": False,
                "grid_tip": "From an earlier decode." if ent.grid_remembered else "",
                "grid_stale": False,
                "band": rec.band,
                "mode": rec.mode,
                "instance": "SKIM",
                "snr": rec.snr,
                "freq_hz": freq_hz,
                "az": az,
                "az_ant": None,
                "delta_az": None,
                "distance_km": dist,
                "bearing_source": bearing_source,
                "score": 1.0 if is_needed else 0.0,
                "is_needed": is_needed,
                "chase": verdict.chase or "",
                "need_label": verdict.need_label or "",
                "is_cq": rec.is_cq,
                "is_dupe": verdict.is_dupe,
                "is_new_mult": verdict.is_new_mult,
                "need_reason": verdict.need_reason or "",
                "mult_reason": verdict.mult_reason or "",
                "is_rover": False,
                "is_calling_us": is_calling_us,
                "is_armed": False,
                "ts": ent.last_seen,
                "age": round(now - ent.last_seen, 1),
                "wav_path": rec.wav_path,
            })
        live_bands.sort()
        return {
            "roster": {
                "count": len(cands),
                "needed": needed,
                "unconfirmed": unconfirmed,
                "not_needed": not_needed,
                "candidates": cands,
                "live_bands": live_bands,
            },
            "log": self.log.public(),
            "logs": [
                {"path": c.path, "title": c.title, "kind": c.kind}
                for c in discover_adif_paths(extra=[self.log.path] if self.log.path else None)
            ],
            "checklist": self.rules.public(),
            "station": {"call": self.my_call, "grid": self.my_grid},
            "now": now,
        }


def PathLabel(path: str) -> str:
    from pathlib import Path as _P
    p = _P(path)
    return p.name
