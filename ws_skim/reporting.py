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
"""Reporting — WSJT-X UDP export and PSKReporter upload for decoded spots.

Two output protocols mirror what WSJT-X produces:

WSJT-X UDP (224.0.0.73:2237 by default, the WS Hub multicast group)
    One emulated WSJT-X instance per (source, band, mode, dial), each with
    its own id (``SKIM-FLEX-2m-FT8``), Heartbeat (type 0, every 15 s),
    Status (type 1, on change and with each heartbeat), and Decode
    (type 2) whose mode field is the WSJT-X character and whose
    delta_frequency is the audio offset from that instance's dial.
    Close (type 6) and Clear (type 3) are sent on shutdown.  The wire
    format follows NetworkMessage.hpp from the WSJT-X source.  WS Hub
    ingests these datagrams the same way it ingests a real WSJT-X
    instance.

PSKReporter (https://pskreporter.info)
    Spots are accumulated and uploaded via HTTPS POST to
    https://www.pskreporter.info/cgi-bin/pskr/upload every
    PSKREPORTER_INTERVAL_S seconds (default 300 / 5 minutes).  The upload
    uses the PSKReporter simple XML format accepted by the REST endpoint.

Message parsing
    MSK144 messages follow the same free-text format as WSJT-X:
        CQ <CALL> <GRID>        — CQ with optional grid
        <DE> <TO> <GRID|report> — standard exchange
        <DE> <TO> RRR | 73 | RR73
    _parse_msk144() extracts (de_call, to_call, grid) best-effort.
    Unparseable messages are still reported via UDP with message=raw text
    and de_call='' so GridTracker can display them.

Usage
    reporter = Reporter(settings)          # settings: QSettings-like dict
    reporter.start()                       # opens UDP socket, starts threads
    reporter.report_decode(decode_dict)    # from displays.py decode drain
    reporter.stop()                        # flush PSKReporter, close socket
"""

import queue
import re
import socket
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────────────

from .wsjtx_udp import (  # noqa: E402
    MAGIC as WSJTX_MAGIC,
    SCHEMA as WSJTX_SCHEMA,
    DEFAULT_ID as WSJTX_ID,
    MSG_HEARTBEAT, MSG_STATUS, MSG_DECODE, MSG_CLEAR, MSG_CLOSE,
    HEARTBEAT_INTERVAL_S,
    DEFAULT_HOST as WSJTX_DEFAULT_HOST,
    DEFAULT_PORT as WSJTX_DEFAULT_PORT,
    MULTICAST_TTL,
    build_heartbeat, build_status, build_decode, build_clear, build_close,
    instance_id, choose_dial, rf_dial_audio, mode_name, mode_spec,
    standard_dials, in_passband, is_multicast_ip, band_label, source_tag,
)

PSKREPORTER_INTERVAL_S  = 300   # 5 minutes — pskreporter asks for ≥5 min
DX_DUPE_INTERVAL_S      = 1200  # 20 minutes — informal DX cluster etiquette
DX_FREQ_CHANGE_KHZ      = 3.0   # re-spot if station moved ≥ this far (kHz)

PSKREPORTER_HOST = "report.pskreporter.info"
PSKREPORTER_PORT = 4739
PSKREPORTER_ENTERPRISE  = 30351
PSKREPORTER_SENDER_TMPL = 0x50e3   # template ID for spot data sets
PSKREPORTER_RECV_TMPL   = 0x50e2   # template ID for receiver options set


# ── MSK144 message parser ────────────────────────────────────────────────────

_CALL_RE  = r'[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](?:/[A-Z0-9]+)?'
_GRID_RE  = r'[A-R]{2}[0-9]{2}(?:[A-X]{2})?'
_REPORT_RE = r'[+-][0-9]{2}|RRR|RR73|73|RR'

_CQ_RE  = re.compile(
    rf'^CQ\s+(?:DX\s+)?({_CALL_RE})(?:\s+({_GRID_RE}))?$', re.IGNORECASE)
_STD_RE = re.compile(
    rf'^({_CALL_RE})\s+({_CALL_RE})(?:\s+({_GRID_RE}|{_REPORT_RE}))?$',
    re.IGNORECASE)


def _parse_msk144(message: str):
    """Return (de_call, to_call, grid) from an MSK144 message string.

    de_call is the transmitting station, to_call is the addressed station.
    grid may be None.  Returns ('', '', None) if unparseable.
    """
    msg = message.strip().upper()
    m = _CQ_RE.match(msg)
    if m:
        return m.group(1), 'CQ', m.group(2)   # de=caller, to=CQ, grid=optional

    m = _STD_RE.match(msg)
    if m:
        to_call, de_call = m.group(1), m.group(2)
        grid_or_rpt = m.group(3) if m.lastindex >= 3 else None
        grid = grid_or_rpt if (grid_or_rpt and re.match(_GRID_RE, grid_or_rpt)) else None
        return de_call, to_call, grid

    return '', '', None


# ── PSKReporter XML builder ───────────────────────────────────────────────────

# ── PSKReporter IPFIX helpers ─────────────────────────────────────────────────

def _ipfix_varlen(data: bytes) -> bytes:
    """IPFIX variable-length field: 1-byte length prefix (or 3-byte if ≥255)."""
    n = len(data)
    if n < 255:
        return struct.pack('B', n) + data
    return struct.pack('>BH', 255, n) + data


def _ipfix_ef(ie_id: int, length: int) -> bytes:
    """Enterprise field specifier: IE id with enterprise bit + length + enterprise number."""
    return struct.pack('>HHI', ie_id | 0x8000, length, PSKREPORTER_ENTERPRISE)


def _ipfix_sf(ie_id: int, length: int) -> bytes:
    """Standard IANA field specifier: IE id + length (no enterprise number)."""
    return struct.pack('>HH', ie_id, length)


def _ipfix_header(total_len: int, export_time: int, seq: int, obs_id: int) -> bytes:
    return struct.pack('>HHIII', 10, total_len, export_time, seq, obs_id)


def _ipfix_pad(body: bytes) -> bytes:
    """Pad body to 4-byte boundary."""
    rem = len(body) % 4
    return body + bytes((4 - rem) % 4)


def _build_psk_datagram(spots: list, my_call: str, my_grid: str,
                         obs_id: int, seq: int,
                         send_templates: bool = True,
                         software: str = "map144") -> bytes:
    """Build a single IPFIX UDP datagram matching WSJT-X PSKReporter.cpp exactly.

    Structure (all in one datagram):
        IPFIX header
        [optional] Sender template set   (set ID 2)
        [optional] Receiver options template set (set ID 3)
        Receiver data set                (template ID 0x50e2)
        Sender data set                  (template ID 0x50e3)

    Key details from WSJT-X source:
        - informationSource = 1 (REPORTER_SOURCE_AUTOMATIC)
        - Receiver options template scope field count = 0
        - All sets in one datagram so PSKReporter can correlate templates/data
    """
    VARLEN = 0xFFFF
    payload = b''

    if send_templates:
        # Sender template set (set ID 2)
        sender_tmpl = (
            struct.pack('>HH', PSKREPORTER_SENDER_TMPL, 7)  # link_id, field_count
            + _ipfix_ef(0x0001, VARLEN)   # senderCallsign
            + _ipfix_ef(0x0005, 5)        # frequency (5 bytes)
            + _ipfix_ef(0x0006, 1)        # sNR (1 byte signed)
            + _ipfix_ef(0x000a, VARLEN)   # mode
            + _ipfix_ef(0x0003, VARLEN)   # senderLocator
            + _ipfix_ef(0x000b, 1)        # informationSource
            + _ipfix_sf(150, 4)           # dateTimeSeconds (IANA IE 150)
        )
        sender_set = struct.pack('>HH', 2, 0) + sender_tmpl
        sender_set = _ipfix_pad(sender_set)
        sender_set = struct.pack('>HH', 2, len(sender_set)) + sender_set[4:]

        # Receiver options template set (set ID 3), scope field count = 0
        recv_tmpl = (
            struct.pack('>HHH', PSKREPORTER_RECV_TMPL, 5, 0)  # link_id, field_count, scope_count=0
            + _ipfix_ef(0x0002, VARLEN)   # receiverCallsign
            + _ipfix_ef(0x0004, VARLEN)   # receiverLocator
            + _ipfix_ef(0x0008, VARLEN)   # decodingSoftware
            + _ipfix_ef(0x0009, VARLEN)   # antennaInformation
            + _ipfix_ef(0x000d, VARLEN)   # rigInformation
        )
        recv_tmpl_set = struct.pack('>HH', 3, 0) + recv_tmpl
        recv_tmpl_set = _ipfix_pad(recv_tmpl_set)
        recv_tmpl_set = struct.pack('>HH', 3, len(recv_tmpl_set)) + recv_tmpl_set[4:]

        payload += sender_set + recv_tmpl_set

    # Receiver data set (our station info)
    recv_data = (
        _ipfix_varlen(my_call.encode('ascii', errors='replace'))
        + _ipfix_varlen(my_grid.encode('ascii', errors='replace'))
        + _ipfix_varlen(software.encode('ascii'))
        + _ipfix_varlen(b'')   # antennaInformation
        + _ipfix_varlen(b'')   # rigInformation
    )
    recv_set = struct.pack('>HH', PSKREPORTER_RECV_TMPL, 0) + recv_data
    recv_set = _ipfix_pad(recv_set)
    recv_set = struct.pack('>HH', PSKREPORTER_RECV_TMPL, len(recv_set)) + recv_set[4:]
    payload += recv_set

    # Sender data set (spots)
    records = b''
    for sp in spots:
        call = sp['de_call'].encode('ascii', errors='replace')
        freq = struct.pack('>Q', int(sp['freq_hz']))[-5:]   # high 5 bytes big-endian
        snr  = max(-127, min(127, int(sp.get('snr') or 0)))
        mode = sp.get('mode', 'MSK144').encode('ascii')
        grid = (sp.get('de_grid') or '').encode('ascii', errors='replace')
        utc  = int(sp['utc_epoch'])
        records += (
            _ipfix_varlen(call)
            + freq
            + struct.pack('b', snr)
            + _ipfix_varlen(mode)
            + _ipfix_varlen(grid)
            + struct.pack('B', 1)        # informationSource = 1 (REPORTER_SOURCE_AUTOMATIC)
            + struct.pack('>I', utc)
        )
    sender_set = struct.pack('>HH', PSKREPORTER_SENDER_TMPL, 0) + records
    sender_set = _ipfix_pad(sender_set)
    sender_set = struct.pack('>HH', PSKREPORTER_SENDER_TMPL, len(sender_set)) + sender_set[4:]
    payload += sender_set

    now = int(time.time())
    return _ipfix_header(16 + len(payload), now, seq, obs_id) + payload


# ── Reporter class ────────────────────────────────────────────────────────────

class Reporter:
    """Manages WSJT-X UDP broadcast and PSKReporter uploads.

    Instantiate once, call start(), feed report_decode() from the GUI
    decode-drain loop, call stop() on shutdown.
    """

    def __init__(self):
        # Settings — populated by apply_settings() from the UI
        self.my_call   = ''
        self.my_grid   = ''

        self.wsjtx_enabled = False
        self.wsjtx_host    = WSJTX_DEFAULT_HOST
        self.wsjtx_port    = WSJTX_DEFAULT_PORT

        self.pskreporter_enabled = False
        self.dial_freq_hz        = 0    # updated by report_freq()

        self.dx_enabled = False
        self.dx_host    = 'dxc.ve7cc.net'
        self.dx_port    = 7373

        # PSKReporter IPFIX state
        import random
        self._psk_obs_id  = random.randint(1, 0xFFFFFFFF)
        self._psk_seq     = 0
        self._psk_sock    = None

        # DX cluster state
        self._dx_sock     = None
        self._dx_lock     = threading.Lock()
        self._dx_logged_in = False
        self._dx_connect_inflight = False  # only mutated under _dx_lock
        self._dx_dupe_cache: dict[str, tuple[float, float]] = {}  # call → (last_time, last_freq_khz)
        self._dx_log_path = (Path(__file__).parent.parent
                             / 'MSK144' / 'detections' / 'dx_spots.log')

        # Emulated WSJT-X instances: id -> {dial_hz, mode, rx_df}
        # One instance per (source, band, mode, dial).  Guarded by _udp_lock.
        self._udp_lock   = threading.Lock()
        self._instances: dict[str, dict] = {}
        self.source      = ''
        self.center_hz   = 0
        self.bandwidth_hz = 0

        # Runtime state
        self._sock       = None
        self._running    = False
        self._decode_q   = queue.SimpleQueue()
        self._psk_spots  = []           # pending PSKReporter spots
        self._psk_lock   = threading.Lock()
        self._last_psk_upload = time.time()  # don't upload until first spots arrive

        # Stats (read by the UI)
        self.stat_udp_sent      = 0
        self.stat_psk_uploaded  = 0
        self.stat_psk_queued    = 0
        self.stat_last_psk_time = ''
        self.stat_dx_status     = 'disabled'
        self.stat_dx_sent       = 0
        self.stat_last_error    = ''

        self._hb_thread   = None
        self._work_thread = None

    # ── Public API ────────────────────────────────────────────────────────────

    def apply_settings(self, my_call: str, my_grid: str,
                       wsjtx_enabled: bool, wsjtx_host: str, wsjtx_port: int,
                       pskreporter_enabled: bool,
                       dx_enabled: bool = False, dx_host: str = 'dxc.ve7cc.net',
                       dx_port: int = 7373):
        self.my_call   = my_call.strip().upper()
        self.my_grid   = my_grid.strip().upper()
        self.wsjtx_enabled = wsjtx_enabled
        self.wsjtx_host    = wsjtx_host.strip()
        self.wsjtx_port    = int(wsjtx_port)
        self.pskreporter_enabled = pskreporter_enabled

        # Reconnect DX cluster if settings changed
        dx_changed = (dx_enabled != self.dx_enabled or
                      dx_host.strip() != self.dx_host or
                      int(dx_port) != self.dx_port)
        self.dx_enabled = dx_enabled
        self.dx_host    = dx_host.strip()
        self.dx_port    = int(dx_port)
        if dx_changed:
            self._dx_disconnect()
        if self.dx_enabled and self.my_call:
            # Run on a daemon thread — _dx_connect blocks on banner / login
            # recv() and would otherwise stall whichever thread called
            # apply_settings (typically the GUI thread).
            threading.Thread(target=self._dx_connect,
                             daemon=True, name='dx-connect').start()

        self._open_udp_socket(reopen=True)
        self._send_all_heartbeats()
        self._send_all_status()

    def start(self):
        if self._running:
            return
        self._running = True
        self._open_udp_socket()

        self._hb_thread = threading.Thread(target=self._heartbeat_loop,
                                           daemon=True, name='reporter-hb')
        self._hb_thread.start()
        # Send heartbeat immediately so WS Hub / GridTracker registers
        # each instance before the first decode arrives.
        self._send_all_heartbeats()
        self._send_all_status()

        self._work_thread = threading.Thread(target=self._work_loop,
                                             daemon=True, name='reporter-work')
        self._work_thread.start()

    def stop(self):
        self._running = False
        # Unblock work thread
        self._decode_q.put(None)
        if self._work_thread:
            self._work_thread.join(timeout=3.0)
        # WSJT-X sends Clear then Close immediately before a graceful exit.
        self._send_shutdown()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._dx_disconnect()
        # Final PSKReporter flush
        if self.pskreporter_enabled and self.my_call:
            self._upload_pskreporter()

    def report_decode(self, decode: dict):
        """Called from the GUI decode-drain loop for every successful decode."""
        self._decode_q.put(decode)

    def report_freq(self, freq_hz: int):
        """Record the IQ centre.  Does not emit Status for that frequency:
        Status dials are the per-mode standard / section dials, not the
        panadaptor centre.
        """
        self.center_hz = int(freq_hz)
        self.dial_freq_hz = int(freq_hz)
        self._refresh_passband_instances()

    def set_context(self, source: str | None = None,
                    center_hz: int | None = None,
                    bandwidth_hz: int | None = None):
        """Tell the reporter which radio and passband are live.

        Called from the display loop.  Standard dials that fall inside
        the passband are announced as receive-only WSJT-X instances so
        WS Hub can show them before the first decode arrives.
        """
        changed = False
        if source is not None and source != self.source:
            self.source = str(source)
            changed = True
        if center_hz is not None and int(center_hz) != self.center_hz:
            self.center_hz = int(center_hz)
            self.dial_freq_hz = int(center_hz)
            changed = True
        if bandwidth_hz is not None and int(bandwidth_hz) != self.bandwidth_hz:
            self.bandwidth_hz = int(bandwidth_hz)
            changed = True
        if changed:
            self._refresh_passband_instances()

    def instance_ids(self) -> list[str]:
        """Live emulated WSJT-X ids, for the read-only GUI list."""
        with self._udp_lock:
            return list(self._instances)

    # ── Internal threads ──────────────────────────────────────────────────────

    def _dx_connect(self):
        """Open TCP connection to DX cluster and log in with callsign.

        Performs the slow banner / login I/O *outside* `_dx_lock` so other
        threads (including the GUI) can call `_dx_disconnect()` or check
        state without waiting for a stalled remote.
        """
        # Pre-check: skip if already connected or another connect is running.
        with self._dx_lock:
            if self._dx_sock is not None or self._dx_connect_inflight:
                return
            self._dx_connect_inflight = True
            self.stat_dx_status = f"connecting: {self.dx_host}:{self.dx_port}"

        s = None
        err = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect((self.dx_host, self.dx_port))
            # Bounded timeout across banner / login round-trip so a slow
            # cluster server cannot hang us indefinitely; switch to blocking
            # mode after login for the read loop.
            s.settimeout(10)
            s.recv(4096)  # banner (may include login prompt)
            s.sendall((self.my_call + '\r\n').encode('ascii'))
            s.recv(4096)  # login response
            s.settimeout(None)
        except Exception as e:
            err = e
            if s is not None:
                try: s.close()
                except Exception: pass
                s = None

        with self._dx_lock:
            self._dx_connect_inflight = False
            if not self.dx_enabled:
                # User disabled DX cluster while we were connecting — discard.
                if s is not None:
                    try: s.close()
                    except Exception: pass
                self.stat_dx_status = 'disabled'
                return
            if err is not None:
                self.stat_dx_status = f"error: {err}"
                self.stat_last_error = f"DX cluster: {err}"
                self._dx_sock = None
                self._dx_logged_in = False
                return
            self._dx_sock = s
            self._dx_logged_in = True
            self.stat_dx_status = f"connected: {self.dx_host}:{self.dx_port}"
        print(f"[reporter] DX cluster: connected to {self.dx_host}:{self.dx_port}", flush=True)

    def _dx_disconnect(self):
        with self._dx_lock:
            if self._dx_sock is not None:
                try:
                    self._dx_sock.close()
                except Exception:
                    pass
                self._dx_sock = None
                self._dx_logged_in = False
            self.stat_dx_status = 'disabled'

    def _dx_log(self, tag: str, detail: str):
        """Append one line to MSK144/detections/dx_spots.log.

        Best-effort: any I/O error is swallowed so logging never disrupts
        the reporting thread.
        """
        try:
            self._dx_log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            with open(self._dx_log_path, 'a') as f:
                f.write(f"{ts} {tag} {detail}\n")
        except Exception:
            pass

    def _dx_send_spot(self, freq_khz: float, dx_call: str, comment: str):
        """Send one DX spot. Reconnects once if the connection has dropped."""
        if not (self.dx_enabled and self.my_call):
            return
        cmd = f"DX {freq_khz:.3f} {dx_call} {comment}\r\n"
        for attempt in range(2):
            with self._dx_lock:
                if self._dx_sock is None:
                    break
                try:
                    self._dx_sock.sendall(cmd.encode('ascii'))
                    self.stat_dx_sent += 1
                    self._dx_log('SENT', cmd.rstrip())
                    return
                except OSError:
                    try:
                        self._dx_sock.close()
                    except Exception:
                        pass
                    self._dx_sock = None
                    self._dx_logged_in = False
                    self.stat_dx_status = 'reconnecting...'
            if attempt == 0:
                self._dx_connect()

    def _open_udp_socket(self, reopen: bool = False):
        """Create (or recreate) the datagram socket.  Multicast destinations
        get TTL=1 (LAN) and loopback enabled so a WS Hub on this PC hears us.
        """
        if self._sock is not None and not reopen:
            return
        if reopen and self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if is_multicast_ip(self.wsjtx_host):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                                MULTICAST_TTL)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            self._sock = sock
        except OSError as e:
            self.stat_last_error = f"UDP socket: {e}"

    def _udp_send(self, pkt: bytes):
        if not (self.wsjtx_enabled and self.my_call and self._sock):
            return
        try:
            self._sock.sendto(pkt, (self.wsjtx_host, self.wsjtx_port))
            self.stat_udp_sent += 1
        except OSError as e:
            self.stat_last_error = f"UDP send: {e}"

    def _register_instance(self, mid: str, dial_hz: int, mode: str,
                           rx_df: int = 1500) -> bool:
        """Add or update an instance.  Returns True if Status should be sent."""
        mode_n = mode_name(mode)
        with self._udp_lock:
            prev = self._instances.get(mid)
            rec = {"dial_hz": int(dial_hz), "mode": mode_n, "rx_df": int(rx_df)}
            self._instances[mid] = rec
            return prev != rec

    def _retire_instances(self, ids: list[str]):
        """Send Clear+Close and drop instances that are no longer in the passband
        or belonged to a previous source."""
        for mid in ids:
            self._udp_send(build_clear(id=mid))
            self._udp_send(build_close(id=mid))
            with self._udp_lock:
                self._instances.pop(mid, None)

    def _refresh_passband_instances(self):
        """Announce every standard dial that sits in the current passband.

        Instances whose dial has left the passband (a band change) or
        whose id does not match the current source are closed so WS Hub
        does not keep a stale 2m instance after a QSY to 6m.
        """
        if not (self.center_hz and self.bandwidth_hz):
            return
        tag = source_tag(self.source)
        prefix = f"SKIM-{tag}-"
        with self._udp_lock:
            stale = [
                mid for mid, rec in self._instances.items()
                if not mid.startswith(prefix)
                or not in_passband(rec["dial_hz"], self.center_hz, self.bandwidth_hz)
            ]
        if stale:
            self._retire_instances(stale)
        from .wsjtx_udp import MODES as _MODES
        for mode in _MODES:
            for dial in standard_dials(mode):
                if not in_passband(dial, self.center_hz, self.bandwidth_hz):
                    continue
                mid = instance_id(self.source, dial, mode)
                if self._register_instance(mid, dial, mode):
                    self._send_heartbeat(mid)
                    self._send_status(mid, decoding=False)

    def _send_heartbeat(self, mid: str):
        self._udp_send(build_heartbeat(id=mid))

    def _send_status(self, mid: str, *, decoding: bool = False):
        with self._udp_lock:
            rec = self._instances.get(mid)
        if not rec:
            return
        pkt = build_status(rec["dial_hz"], self.my_call, self.my_grid,
                           mode=rec["mode"], id=mid, decoding=decoding,
                           rx_df=rec.get("rx_df", 1500),
                           tx_df=rec.get("rx_df", 1500))
        self._udp_send(pkt)

    def _send_all_heartbeats(self):
        with self._udp_lock:
            ids = list(self._instances)
        for mid in ids:
            self._send_heartbeat(mid)

    def _send_all_status(self, *, decoding: bool = False):
        with self._udp_lock:
            ids = list(self._instances)
        for mid in ids:
            self._send_status(mid, decoding=decoding)

    def _send_shutdown(self):
        with self._udp_lock:
            ids = list(self._instances)
        # Leave UDP enabled long enough to emit Clear + Close.
        for mid in ids:
            self._udp_send(build_clear(id=mid))
            self._udp_send(build_close(id=mid))

    def _heartbeat_loop(self):
        while self._running:
            self._send_all_heartbeats()
            self._send_all_status(decoding=False)
            for _ in range(HEARTBEAT_INTERVAL_S * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _work_loop(self):
        while self._running:
            # PSKReporter upload timer
            now = time.time()
            if (self.pskreporter_enabled and self.my_call
                    and now - self._last_psk_upload >= PSKREPORTER_INTERVAL_S):
                self._upload_pskreporter()

            try:
                decode = self._decode_q.get(timeout=5.0)
            except queue.Empty:
                continue
            if decode is None:
                break

            self._handle_decode(decode)

    def _handle_decode(self, decode: dict):
        msg       = decode.get('message', '')
        radio_khz = float(decode.get('radio_khz', 0.0))
        freq_hz   = int(round(radio_khz * 1000))
        snr       = decode.get('jt9_snr')

        de_call, to_call, grid = _parse_msk144(msg)
        mode = mode_name(decode.get("mode") or "MSK144")

        # ── WSJT-X UDP ────────────────────────────────────────────────────────
        # One instance per (source, band, mode, dial).  Status dial is the
        # section / calling frequency; Decode delta_frequency is RF − dial.
        if self.wsjtx_enabled and self.my_call and self._sock:
            rf_hz, dial_hz, audio_hz = rf_dial_audio(decode, freq_hz or self.center_hz)
            mid = instance_id(self.source, dial_hz, mode)
            rx_df = int(round(audio_hz or 1500))
            self._register_instance(mid, dial_hz, mode, rx_df=rx_df)
            self._send_status(mid, decoding=True)
            payload = dict(decode)
            payload.setdefault("mode", mode)
            self._udp_send(build_decode(payload, self.my_call, dial_hz, id=mid))
            self._send_status(mid, decoding=False)

        # ── PSKReporter ───────────────────────────────────────────────────────
        # Skip self-spots and spots without a grid (PSKReporter requires both)
        if (self.pskreporter_enabled and self.my_call and de_call
                and de_call.upper() != self.my_call.upper()
                and grid):
            spot = {
                'de_call':   de_call,
                'de_grid':   grid,
                'freq_hz':   freq_hz,
                'snr':       snr,
                'mode':      decode.get('mode', 'MSK144'),
                'utc_epoch': int(time.time()),
            }
            with self._psk_lock:
                self._psk_spots.append(spot)
                self.stat_psk_queued = len(self._psk_spots)

        # ── DX Cluster ────────────────────────────────────────────────────────
        # Skip self-spots; suppress duplicates within DX_DUPE_INTERVAL_S (20 min)
        # to respect the informal DX cluster etiquette against flooding with the
        # same callsign every 15-30 seconds when a local station is running CQ.
        if (self.dx_enabled and self.my_call and de_call
                and de_call.upper() != self.my_call.upper()):
            _now      = time.time()
            _key      = de_call.upper()
            _last_t, _last_f = self._dx_dupe_cache.get(_key, (0.0, 0.0))
            _freq_moved = abs(radio_khz - _last_f) >= DX_FREQ_CHANGE_KHZ
            if _now - _last_t >= DX_DUPE_INTERVAL_S or _freq_moved:
                freq_khz = radio_khz
                snr_str  = f"{snr:+d}dB" if snr is not None else ""
                grid_str = grid or ""
                comment  = f"{decode.get('mode', 'MSK144')} {grid_str} {snr_str}".strip()
                self._dx_send_spot(freq_khz, de_call, comment)
                self._dx_dupe_cache[_key] = (_now, radio_khz)
            else:
                # Dedup fired — record the would-be spot so we can confirm
                # the cluster is NOT seeing duplicates from us.
                age = int(_now - _last_t)
                self._dx_log('SUPPRESSED',
                             f"DX {radio_khz:.3f} {de_call} (age={age}s)")
                # Prune stale entries so the cache doesn't grow unbounded
                # during a long session with many unique callsigns.
                if len(self._dx_dupe_cache) > 500:
                    cutoff = _now - DX_DUPE_INTERVAL_S
                    self._dx_dupe_cache = {
                        k: v for k, v in self._dx_dupe_cache.items()
                        if v[0] >= cutoff
                    }

    def _psk_next_seq(self) -> int:
        self._psk_seq += 1
        return self._psk_seq

    def _upload_pskreporter(self):
        with self._psk_lock:
            if not self._psk_spots:
                return
            spots = list(self._psk_spots)
            self._psk_spots.clear()
            self.stat_psk_queued = 0

        self._last_psk_upload = time.time()
        try:
            if self._psk_sock is None:
                self._psk_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            host = socket.gethostbyname(PSKREPORTER_HOST)

            # Always send templates in the same datagram as data — UDP has no
            # ordering guarantee and we only upload every 5 min, so the overhead
            # is negligible and PSKReporter always gets templates before data.
            dgram = _build_psk_datagram(spots, self.my_call, self.my_grid,
                                         self._psk_obs_id, self._psk_next_seq(),
                                         send_templates=True)
            self._psk_sock.sendto(dgram, (host, PSKREPORTER_PORT))

            self.stat_psk_uploaded += len(spots)
            self.stat_last_psk_time = datetime.now(timezone.utc).strftime('%H:%M:%S')
            print(f"[reporter] PSKReporter: sent {len(spots)} spots via IPFIX UDP", flush=True)
            for sp in spots:
                print(f"  spot: {sp['de_call']:12s}  {sp['freq_hz']:12d} Hz  "
                      f"{sp.get('mode','?'):8s}  SNR {sp.get('snr','?'):>4}  "
                      f"grid {sp.get('de_grid') or '----'}  "
                      f"t={datetime.fromtimestamp(sp['utc_epoch'], tz=timezone.utc).strftime('%H:%M:%S')}",
                      flush=True)
        except Exception as e:
            self.stat_last_error = f"PSKReporter: {e}"
            print(f"[reporter] PSKReporter upload failed: {e}", flush=True)
            # Re-queue spots so they're not lost
            with self._psk_lock:
                self._psk_spots = spots + self._psk_spots
                self.stat_psk_queued = len(self._psk_spots)

