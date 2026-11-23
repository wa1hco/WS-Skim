# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Needed badges, show-menu filter, ADIF poll, cty zones, roster JSON shape."""
from __future__ import annotations

import pytest

from ws_skim.needed.lotw import use_paths


@pytest.fixture(autouse=True)
def _isolate_lotw():
    """Do not read this PC's GridTracker LoTW file during unit tests."""
    use_paths([])
    try:
        yield
    finally:
        use_paths(None)


from ws_skim.needed.adif import Qso, load_qsos
from ws_skim.needed.bands import band_label_khz
from ws_skim.needed.checklist import ChecklistRules, operating_checklist
from ws_skim.needed.cty import CtyIndex, find_cty_dat
from ws_skim.needed.filter import row_visible, visible_decode, normalize_need_filter
from ws_skim.needed.logs import LogStore, poll_changed, reload_path
from ws_skim.needed.message import interpret
from ws_skim.roster.model import RosterSession

T0 = 1_700_000_000.0


def _cty():
    path = find_cty_dat()
    assert path is not None
    return CtyIndex.load(path)


def _store(qsos):
    store = LogStore()
    store.load(qsos, path="mem", label="the log", why="test")
    return store


def _qso(call, band="20m", mode="FT8", grid=None, confirmed=False, dxcc=None, state=None):
    return Qso(id=f"{call}|{band}|{mode}|{grid}", call=call, band=band, mode=mode,
               grid=grid, confirmed=confirmed, dxcc=dxcc, state=state)


def test_operating_checklist_is_dxcc_per_band():
    items = operating_checklist()
    assert items["dxcc"]["enabled"] is True and items["dxcc"]["scope"] == "band"
    assert items["grid"]["enabled"] is False
    assert items["call"]["enabled"] is False


def test_cty_zones_for_known_calls():
    cty = _cty()
    ja = cty.country_fields("JA1ABC", "PM95")
    assert ja["country"] == "Japan" and ja["continent"] == "AS" and ja["cq_zone"] == 25
    us = cty.describe("K1ABC", "FN42")
    assert us.continent == "NA" and us.cq == 5 and us.known
    assert cty.entity_name("G4ABC")  # United Kingdom of some kind


def test_badge_new_dxcc_and_already_have():
    cty = _cty()
    rules = ChecklistRules(operating_checklist(), cty=cty, log_label="the log")
    store = _store([_qso("W1AW", band="20m", grid="FN31", confirmed=True)])  # United States
    # Japan on 20m is a new DXCC entity on that band.
    v = rules.classify(store, "JA1ABC", "20m", "FT8", "PM95")
    assert v.chase == "needed" and v.need_label == "DXCC" and not v.is_dupe
    assert "Japan" in v.need_reason and "20m" in v.need_reason
    # Another US station on 20m is already have (per band).
    v2 = rules.classify(store, "K1ABC", "20m", "FT8", "FN42")
    assert v2.is_dupe and v2.chase == ""
    assert "United States" in v2.need_reason or "already" in v2.need_reason.lower()
    # Same US entity on 40m is needed (per band).
    v3 = rules.classify(store, "K2AA", "40m", "FT8", "FN42")
    assert v3.chase == "needed" and v3.need_label == "DXCC"
    assert "40m" in v3.need_reason


def test_badge_unconfirmed_when_log_says_not_confirmed():
    cty = _cty()
    rules = ChecklistRules(operating_checklist(), cty=cty, log_label="the log",
                           count_unconfirmed=True)
    store = _store([_qso("JA1ABC", band="20m", grid="PM95", confirmed=False)])
    # A different Japanese station on 20m: place is worked, not confirmed.
    v = rules.classify(store, "JA2XYZ", "20m", "FT8", "PM95")
    assert v.chase == "unconfirmed" and v.need_label == ""
    assert not v.is_dupe  # still needed
    assert "not confirmed" in v.need_reason


def test_badge_grid_when_that_line_is_on():
    cty = _cty()
    items = operating_checklist()
    items["grid"] = {"enabled": True, "scope": "overall"}
    items["dxcc"] = {"enabled": False, "scope": "band"}
    rules = ChecklistRules(items, cty=cty, log_label="the log")
    store = _store([_qso("W1AW", grid="FN31", confirmed=True)])
    v = rules.classify(store, "K1ABC", "20m", "FT8", "FN42")
    assert v.chase == "needed" and v.need_label == "grid"
    assert "FN42" in v.need_reason
    v2 = rules.classify(store, "K2AA", "20m", "FT8", "FN31")
    assert v2.is_dupe
    assert "FN31" in v2.need_reason


def test_show_menu_filter():
    assert normalize_need_filter(None) == "needed"
    assert normalize_need_filter(False) == "all"
    assert row_visible(is_needed=True, is_cq=True, message_tail="FN42", mode="needed")
    assert not row_visible(is_needed=False, is_cq=True, message_tail="FN42", mode="needed")
    assert row_visible(is_needed=False, is_cq=True, message_tail="FN42", mode="all")
    assert row_visible(is_needed=True, is_cq=True, message_tail="FN42", mode="needed-cq")
    assert not row_visible(is_needed=True, is_cq=False, message_tail="R-10", mode="needed-cq")
    assert row_visible(is_needed=True, is_cq=False, message_tail="RR73 FN42", mode="needed-73")
    assert not row_visible(is_needed=True, is_cq=False, message_tail="R-10", mode="needed-73")
    assert visible_decode("CQ K1ABC FN42", is_needed=True, mode="needed-cq")
    assert not visible_decode("W1AW K1ABC R-12", is_needed=True, mode="needed-cq")


def test_interpret_cq_and_exchange():
    is_cq, dx, to, grid, tail = interpret("CQ DX K1ABC FN42")
    assert is_cq and dx == "K1ABC" and to == "CQ" and grid == "FN42" and "FN42" in tail
    is_cq, dx, to, grid, tail = interpret("W1AW K9XYZ R-12")
    assert not is_cq and dx == "K9XYZ" and to == "W1AW" and tail == "R-12"


def test_adif_poll_and_load(tmp_path):
    adi = tmp_path / "wsjtx_log.adi"
    adi.write_text(
        "ADIF<\n<eoh>\n"
        "<call:4>W1AW<band:3>20m<mode:3>FT8<gridsquare:4>FN31"
        "<qso_date:8>20260101<time_on:6>120000<eor>\n"
    )
    store = LogStore()
    assert reload_path(adi, store, title="test log")
    assert store.qso_count == 1 and store.cached_rows()[0]["call"] == "W1AW"
    assert store.cached_rows()[0]["band"] == "20m"
    assert poll_changed(store) is False
    adi.write_text(adi.read_text() +
                   "<call:5>JA1AA<band:3>20m<mode:3>FT8<gridsquare:4>PM95"
                   "<qso_date:8>20260101<time_on:6>120100<eor>\n")
    import os
    os.utime(adi, (store._mtime + 2, store._mtime + 2))
    assert poll_changed(store) is True
    assert reload_path(adi, store, title="test log")
    assert store.qso_count == 2
    qsos, err = load_qsos(adi)
    assert err is None and len(qsos) == 2


def test_roster_json_shape_and_one_row_per_station(tmp_path):
    adi = tmp_path / "log.adi"
    adi.write_text("ADIF<\n<eoh>\n"
                   "<call:4>W1AW<band:3>20m<mode:3>FT8<gridsquare:4>FN31"
                   "<lotw_qsl_rcvd:1>Y"
                   "<qso_date:8>20260101<time_on:6>120000<eor>\n")
    s = RosterSession(my_call="N1ABC", my_grid="FN42", log_path=str(adi), ttl=300)
    # Two decodes of the same station (second has no grid) collapse to one row.
    s.decodes.add_decode(epoch=T0, utc_time="00:00:00", mode="FT8", radio_khz=14075.0,
                         message="CQ JA1ABC PM95", snr=-8, now=T0)
    s.decodes.add_decode(epoch=T0 + 15, utc_time="00:00:15", mode="FT8", radio_khz=14075.2,
                         message="N1ABC JA1ABC R-10", snr=-5, now=T0 + 15)
    s.decodes.add_decode(epoch=T0 + 15, utc_time="00:00:15", mode="FT4", radio_khz=14080.0,
                         message="CQ K1ABC FN42", snr=3, now=T0 + 15)
    snap = s.snapshot(now=T0 + 20)
    ros = snap["roster"]
    assert set(ros) >= {"count", "needed", "unconfirmed", "not_needed", "candidates", "live_bands"}
    calls = {c["call"]: c for c in ros["candidates"]}
    assert "JA1ABC" in calls and "K1ABC" in calls
    ja = calls["JA1ABC"]
    assert ja["grid"] == "PM95" and ja["is_needed"] and ja["need_label"] == "DXCC"
    assert ja["chase"] == "needed" and "Japan" in ja["need_reason"]
    assert ja["country"] == "Japan" and ja["is_calling_us"] is True
    assert ja["mode"] == "FT8" and ja["message_tail"] == "R-10"
    k = calls["K1ABC"]
    assert k["is_dupe"] and not k["is_needed"]  # US already in the log on 20m
    assert k["need_reason"]
    assert "20m" in ros["live_bands"]
    assert snap["log"]["count"] == 1
    assert snap["checklist"]["items"]
    assert band_label_khz(14075) == "20m"


@pytest.mark.parametrize("msg,grid", [
    ("K1ABC W9XYZ RR73", None),
    ("K1ABC W9XYZ RRR", None),
    ("K1ABC W9XYZ 73", None),
    ("CQ W9XYZ EN52", "EN52"),
    ("K1ABC W9XYZ EN52", "EN52"),
    ("K1ABC W9XYZ R-12", None),
    ("K1ABC RR73; W9XYZ <KA1ABC> -08", None),
])
def test_rr73_is_never_a_grid(msg, grid):
    assert interpret(msg)[3] == grid
