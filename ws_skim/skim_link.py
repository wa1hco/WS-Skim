# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Click-to-work: WS Skim talks to "WSJT-X for WS Skim" over WSJT-X UDP.

WS Skim joins the multicast group that WSJT-X sends to and keeps the
latest Status and recent Decodes from the instance whose id is
``WSJT-X - Skim``.  Double-clicking a station in the roster calls
:meth:`SkimLink.work`:

1. the caller has already tuned WS Skim's slice to the station's dial
   (WSJT-X follows the slice through nCAT);
2. if WSJT-X is in another mode, a Configure message switches it;
3. as soon as WSJT-X itself decodes that station (usually the next
   period), WS Skim echoes that decode back as a Reply.  That is exactly
   what a double-click in WSJT-X's Band Activity does; WSJT-X refuses a
   Reply that does not match one of its own decodes.

WS Skim never keys the radio.  WSJT-X transmits only as its own Enable Tx
and its own settings allow.
"""
from __future__ import annotations

import logging
import socket
import struct
import sys
import threading
import time
from collections import deque

from . import wsjtx_udp as wu
from .needed.message import interpret
from .skim_wsjtx import APP_ID

log = logging.getLogger("ws_skim.skim_link")

#: How long a double-click keeps waiting for WSJT-X to decode the station.
PENDING_S = 150.0
#: How recent a WSJT-X decode may be to answer right away.
FRESH_S = 30.0
#: WSJT-X counts as running while its heartbeats/status keep coming.
ALIVE_S = 40.0


def _base(call: str | None) -> str:
    return (call or "").strip().upper().split("/")[0]


def decode_sender(message: str) -> str:
    """The station that sent ``message`` (CQ caller or second call)."""
    _cq, dx, _to, _grid, _tail = interpret(message or "")
    return (dx or "").upper()


def reply_rank(message: str) -> int:
    """Higher is better to answer: CQ / 73 / RR73 (WSJT-X then enables Tx)."""
    is_cq, _dx, to, _grid, tail = interpret(message or "")
    toks = {t.upper() for t in (tail or "").split()}
    if is_cq or (to or "").upper() == "QRZ":
        return 3
    if toks & {"73", "RR73"}:
        return 2
    return 1


class SkimLink:
    """Listens to WSJT-X for WS Skim and drives click-to-work."""

    def __init__(self, group: str = wu.DEFAULT_HOST, port: int = wu.DEFAULT_PORT,
                 app_id: str = APP_ID, clock=time.monotonic, sock=None):
        self.group = group
        self.port = int(port)
        self.app_id = app_id
        self._clock = clock
        self._sock = sock
        self._own_sock = sock is None
        self._tx = sock               # replies go out from an unbound socket
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.addr = None             # (ip, port) WSJT-X sends from
        self.last_seen = None
        self.status: dict = {}
        self.decodes: deque = deque(maxlen=400)
        self.pending: dict | None = None
        self.last_event = ""
        self.sent: list[bytes] = []  # for tests / diagnostics (last few)

    # ── socket ──────────────────────────────────────────────────────────
    def open(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        # Bind to the group address itself so only multicast arrives here:
        # bound to "" this socket would share (steal) unicast datagrams other
        # programs on this port receive, e.g. WS Hub on 127.0.0.1:2237.
        mcast = wu.is_multicast_ip(self.group)
        s.bind((self.group if mcast and sys.platform.startswith("linux") else "", self.port))
        if mcast:
            joined = 0
            idxs = [i for i, _n in socket.if_nameindex()] if hasattr(socket, "if_nameindex") else []
            for idx in idxs:
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                 struct.pack("4s4si", socket.inet_aton(self.group), b"\0" * 4, idx))
                    joined += 1
                except OSError:
                    pass
            if not joined:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             struct.pack("4s4s", socket.inet_aton(self.group), b"\0" * 4))
        s.settimeout(0.5)
        self._sock = s
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    def start(self) -> None:
        self.open()
        self._thread = threading.Thread(target=self._run, name="skim-wsjtx-link", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._own_sock:
            for so in (self._sock, self._tx):
                try:
                    if so is not None:
                        so.close()
                except OSError:
                    pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65536)
            except socket.timeout:
                self.tick()
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.5)
                continue
            self.feed(data, addr)

    def _send(self, data: bytes) -> bool:
        if self.addr is None or self._tx is None:
            return False
        try:
            self._tx.sendto(data, self.addr)
        except OSError as exc:
            log.warning("Could not send to WSJT-X for WS Skim: %s", exc)
            return False
        self.sent = (self.sent + [data])[-20:]
        return True

    # ── incoming ────────────────────────────────────────────────────────
    def feed(self, data: bytes, addr) -> None:
        msg = wu.parse(data)
        if not msg or msg.get("id") != self.app_id:
            return
        now = self._clock()
        with self._lock:
            self.addr = (addr[0], int(addr[1]))
            self.last_seen = now
            t = msg.get("type")
            if t == wu.MSG_STATUS:
                self.status = msg
            elif t == wu.MSG_CLOSE:
                self.last_seen = None
                self.status = {}
            elif t == wu.MSG_CLEAR:
                self.decodes.clear()
            elif t == wu.MSG_DECODE:
                msg["_at"] = now
                self.decodes.append(msg)
                self._try_pending()

    @property
    def alive(self) -> bool:
        with self._lock:
            return self.last_seen is not None and self._clock() - self.last_seen < ALIVE_S

    # ── click-to-work ───────────────────────────────────────────────────
    def work(self, call: str, mode: str | None = None, df_hz: int | None = None) -> str:
        """Ask WSJT-X for WS Skim to work ``call``; returns what happens next."""
        call = (call or "").strip().upper()
        if not call:
            return "No call sign."
        with self._lock:
            if not self.alive:
                return ("WSJT-X for WS Skim is not running.  Start it from its "
                        "launcher (Flex Radio window → Set up WSJT-X makes one).")
            cur_mode = (self.status.get("mode") or "").upper()
            want = (mode or "").upper()
            if want in ("FT8", "FT4") and cur_mode and want != cur_mode:
                self._send(wu.build_configure(self.app_id, mode=want,
                                              rx_df=int(df_hz) if df_hz else None))
            elif df_hz:
                self._send(wu.build_configure(self.app_id, rx_df=int(df_hz)))
            self.pending = {"call": call, "mode": want, "until": self._clock() + PENDING_S}
            if self._try_pending():
                return self.last_event
            self.last_event = f"Waiting for WSJT-X to decode {call}, then it calls."
            return self.last_event

    def cancel(self) -> None:
        with self._lock:
            self.pending = None

    def tick(self) -> None:
        with self._lock:
            p = self.pending
            if p and self._clock() > p["until"]:
                self.last_event = f"WSJT-X did not decode {p['call']}; gave up."
                self.pending = None

    def _try_pending(self) -> bool:
        p = self.pending
        if not p:
            return False
        now = self._clock()
        if now > p["until"]:
            self.pending = None
            return False
        best = None
        for d in reversed(self.decodes):
            if now - d.get("_at", now) > FRESH_S:
                break
            if _base(decode_sender(d.get("message", ""))) != _base(p["call"]):
                continue
            rank = reply_rank(d.get("message", ""))
            if best is None or rank > best[0]:
                best = (rank, d)
        if best is None:
            return False
        d = best[1]
        if self._send(wu.build_reply(d, self.app_id)):
            self.pending = None
            self.last_event = f"WSJT-X for WS Skim is calling {p['call']}."
            return True
        return False

    # ── for the UI ──────────────────────────────────────────────────────
    def state(self) -> dict:
        with self._lock:
            st = self.status or {}
            return {
                "running": self.alive,
                "transmitting": bool(st.get("transmitting")) if self.alive else False,
                "tx_enabled": bool(st.get("tx_enabled")) if self.alive else False,
                "dx_call": st.get("dx_call") or "",
                "mode": st.get("mode") or "",
                "dial_hz": st.get("dial_frequency"),
                "pending": (self.pending or {}).get("call", ""),
                "event": self.last_event,
            }

    def status_text(self) -> str:
        s = self.state()
        if not s["running"]:
            return "WSJT-X for WS Skim: not running"
        if s["transmitting"]:
            return f"WSJT-X for WS Skim: transmitting to {s['dx_call'] or '…'}"
        if s["pending"]:
            return f"WSJT-X for WS Skim: waiting to decode {s['pending']}"
        if s["tx_enabled"] and s["dx_call"]:
            return f"WSJT-X for WS Skim: working {s['dx_call']}"
        return f"WSJT-X for WS Skim: listening ({s['mode'] or '?'})"
