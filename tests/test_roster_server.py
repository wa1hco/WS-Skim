# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Roster HTTP + SSE server (ephemeral port, no browser)."""
from __future__ import annotations

import json
import time
import urllib.request
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


from ws_skim.roster.model import RosterSession
from ws_skim.roster.server import RosterServer, STATIC

T0 = 1_700_000_000.0


@pytest.fixture
def session(tmp_path):
    adi = tmp_path / "wsjtx_log.adi"
    adi.write_text(
        "ADIF<\n<eoh>\n"
        "<call:4>W1AW<band:3>20m<mode:3>FT8<gridsquare:4>FN31"
        "<lotw_qsl_rcvd:1>Y<qso_date:8>20260101<time_on:6>120000<eor>\n"
    )
    return RosterSession(my_call="N1ABC", my_grid="FN42", log_path=str(adi), ttl=300)


@pytest.fixture
def server(session):
    srv = RosterServer(session, port=0)
    srv.start()
    # Wait briefly for the thread to accept.
    time.sleep(0.05)
    try:
        yield srv
    finally:
        srv.stop()


def _get(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.headers, r.read()


def _post(url, body, timeout=2.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def test_static_and_state(server, session):
    assert server.port not in (8787, 8790)
    assert server.host == "127.0.0.1"
    code, headers, body = _get(server.url)
    assert code == 200 and b"Call roster" in body and b"roster.js" in body
    assert "text/html" in headers.get("Content-Type", "")
    code, _, css = _get(server.url + "wims.css")
    assert code == 200 and b"--bg:" in css
    code, _, js = _get(server.url + "roster.js")
    assert code == 200 and b"EventSource" in js
    # Static files really exist for the installer (git archive / package data).
    assert (STATIC / "roster.html").is_file()
    assert (STATIC / "wims.css").is_file()
    assert (STATIC / "roster.js").is_file()

    now = time.time()
    session.decodes.add_decode(
        epoch=now, utc_time="00:00:00", mode="FT8", radio_khz=14075.0,
        message="CQ JA1ABC PM95", snr=-6, now=now,
    )
    code, _, raw = _get(server.url + "api/state")
    data = json.loads(raw.decode())
    assert code == 200
    assert data["log"]["count"] == 1
    cands = data["roster"]["candidates"]
    assert any(c["call"] == "JA1ABC" and c["need_label"] == "DXCC" for c in cands)


def test_post_checklist_and_log(server, session, tmp_path):
    status, out = _post(server.url + "api/checklist", {
        "items": {"dxcc": {"enabled": True, "scope": "band"},
                  "grid": {"enabled": True, "scope": "overall"},
                  "call": {"enabled": False, "scope": "overall"}},
    })
    assert status == 200 and out["ok"] is True
    grid = next(i for i in out["checklist"]["items"] if i["kind"] == "grid")
    assert grid["enabled"] is True

    other = tmp_path / "other.adi"
    other.write_text("ADIF<\n<eoh>\n"
                     "<call:5>JA1AA<band:3>20m<mode:3>FT8<gridsquare:4>PM95"
                     "<lotw_qsl_rcvd:1>Y<qso_date:8>20260102<time_on:6>010000<eor>\n")
    status, out = _post(server.url + "api/log", {"path": str(other)})
    assert status == 200 and out["ok"] is True
    assert out["log"]["count"] == 1
    assert "JA1AA" in out["log"]["why"] or out["log"]["count"] == 1


def test_sse_sends_json_frame(server, session):
    session.decodes.add_decode(
        epoch=time.time(), utc_time="00:00:00", mode="FT8", radio_khz=14075.0,
        message="CQ G4ABC IO91", snr=0,
    )
    req = urllib.request.Request(server.url + "events", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=3) as r:
        assert "text/event-stream" in r.headers.get("Content-Type", "")
        buf = b""
        deadline = time.time() + 2.5
        while time.time() < deadline and b"\n\n" not in buf:
            chunk = r.read(256)
            if not chunk:
                break
            buf += chunk
    assert buf.startswith(b"data: ")
    payload = buf.split(b"\n\n", 1)[0][6:]
    data = json.loads(payload.decode())
    assert "roster" in data and "log" in data
