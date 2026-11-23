# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Glue between the roster, WS Skim's own Flex slice and WSJT-X for WS Skim.

Double-click a station in the roster → :func:`request`:

* WS Skim tunes ITS OWN slice to the station's dial (FT8 / FT4 standard
  dial that holds the station's audio offset) and keeps it there;
* :class:`~ws_skim.skim_link.SkimLink` asks WSJT-X for WS Skim to call the
  station as soon as WSJT-X decodes it.

Nothing here sets transmit on any slice or keys the radio.
"""
from __future__ import annotations

import logging
import sys
import threading

from . import wsjtx_udp as wu
from .skim_wsjtx import load_record

log = logging.getLogger("ws_skim.click_to_work")
_LINK_LOCK = threading.Lock()


def standard_dials_hz(mode: str, band: str) -> list[int]:
    """WS Skim's FT8 / FT4 dials on ``band`` (HF from the Band list, VHF table)."""
    out = list(wu.standard_dials(mode, band))
    try:
        from .ui import BANDS_BY_MODE
    except Exception:          # no Qt (tests of the pure parts)
        BANDS_BY_MODE = {}
    for _label, f_mhz, _src in BANDS_BY_MODE.get(wu.mode_name(mode), ()):
        hz = int(round(float(f_mhz) * 1e6))
        if wu.band_label(hz) == band and hz not in out:
            out.append(hz)
    return out


def plan_dial(freq_hz: int, mode: str) -> tuple[int, int]:
    """(dial Hz, audio offset Hz) for a station heard at RF ``freq_hz``:
    the standard dial that puts it 100..3500 Hz up, else a whole kHz
    about 1500 Hz below it."""
    rf = int(freq_hz)
    best = None
    for dial in standard_dials_hz(mode, wu.band_label(rf)):
        df = rf - dial
        if 100 <= df <= 3500 and (best is None or abs(df - 1500) < abs(best[1] - 1500)):
            best = (dial, df)
    if best is None:
        dial = (rf - 1500) // 1000 * 1000
        best = (dial, rf - dial)
    return int(best[0]), int(best[1])


def link(app, create: bool = True):
    """WS Skim's SkimLink, started on first use from the saved setup."""
    lk = getattr(app, "_skim_link", None)
    if lk is not None or not create:
        return lk
    with _LINK_LOCK:
        lk = getattr(app, "_skim_link", None)
        if lk is not None:
            return lk
        return _start_link(app)


def _start_link(app):
    rec = load_record()
    if not rec or not sys.platform.startswith("linux"):
        return None
    from .skim_link import SkimLink
    try:
        lk = SkimLink(group=rec.get("udp_group") or wu.DEFAULT_HOST,
                      port=int(rec.get("udp_port") or wu.DEFAULT_PORT))
        lk.start()
    except OSError as exc:
        log.warning("Could not listen for WSJT-X for WS Skim: %s", exc)
        return None
    app._skim_link = lk
    return lk


def apply_dax_audio(app) -> None:
    """Put the set-up DAX audio channel on WS Skim's slice (same letter only)."""
    rc = getattr(app, "radio_client", None)
    rec = load_record()
    if rc is None or not rec or not hasattr(rc, "set_dax_audio"):
        return
    rc.set_dax_audio(rec.get("dax_channel"), rec.get("letter"))


def request(app, body: dict) -> dict:
    """Roster double-click.  Returns ``{"ok": bool, "message": str}``."""
    call = str(body.get("call") or "").strip().upper()
    mode = str(body.get("mode") or "").upper()
    try:
        freq_hz = int(body.get("freq_hz") or 0)
    except (TypeError, ValueError):
        freq_hz = 0
    if not call or not freq_hz:
        return {"ok": False, "message": "That row has no call or frequency."}
    if mode not in ("FT8", "FT4"):
        return {"ok": False, "message": f"Click-to-work is for FT8 and FT4 ({call} is {mode or '?'})."}
    rc = getattr(app, "radio_client", None)
    owned = getattr(rc, "owned", None) if rc is not None else None
    if owned is None or not getattr(rc, "own_pan", False):
        return {"ok": False, "message": "Click-to-work needs the Flex radio with \"WS Skim makes "
                                        "its own panadapter\" ticked (View → Flex Radio)."}
    rec = load_record()
    if not rec:
        return {"ok": False, "message": "Press Set up WSJT-X in View → Flex Radio first."}
    letter = rc.own_slice_letter
    if letter and rec.get("letter") and letter != rec.get("letter"):
        return {"ok": False, "message": f"WS Skim's slice is now {letter} but WSJT-X for WS Skim "
                                        f"is set up for {rec.get('letter')}.  Press Set up WSJT-X again."}
    lk = link(app)
    if lk is None:
        return {"ok": False, "message": "WS Skim could not listen for WSJT-X for WS Skim."}
    if not lk.alive:
        return {"ok": False, "message": lk.work(call, mode)}
    dial, df = plan_dial(freq_hz, mode)
    centre = owned.target_center_mhz
    if centre is not None and abs(dial / 1e6 - centre) * 1000.0 > owned.HOLD_MAX_OFFSET_KHZ:
        return {"ok": False, "message": f"{call} is outside WS Skim's panadapter."}
    rc.hold_slice(dial / 1e6)
    msg = lk.work(call, mode, df)
    log.info("Click-to-work %s %s: slice → %.6f MHz, offset %d Hz", call, mode, dial / 1e6, df)
    return {"ok": True, "message": msg}


def state(app) -> dict:
    """What the roster page and Flex window show."""
    rc = getattr(app, "radio_client", None)
    lk = link(app, create=load_record() is not None)
    st = lk.state() if lk is not None else {"running": False}
    tx_ours = bool(rc is not None and getattr(rc, "tx_on_own_slice", False))
    letter = getattr(rc, "own_slice_letter", None) if rc is not None else None
    warn = ""
    if tx_ours and not st.get("transmitting"):
        warn = (f"Transmit is on WS Skim's slice {letter or ''}.  Click TX on your own "
                "slice before you talk or send CW.")
    return {
        "set_up": load_record() is not None,
        "text": lk.status_text() if lk is not None else "WSJT-X for WS Skim: not set up",
        "event": st.get("event", ""),
        "running": bool(st.get("running")),
        "transmitting": bool(st.get("transmitting")),
        "tx_on_skim_slice": tx_ours,
        "warning": warn,
    }


def stop(app) -> None:
    lk = getattr(app, "_skim_link", None)
    if lk is not None:
        lk.stop()
        app._skim_link = None
