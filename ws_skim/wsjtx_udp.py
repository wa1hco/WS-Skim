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
"""WSJT-X UDP Network Message protocol — builders, identity, and a parser.

Wire format follows ``Network/NetworkMessage.hpp`` in the official WSJT-X
source (schema 3 / Qt_5_4; field encodings used here are identical to
schema 2 / Qt_5_2).  WS Skim emits Heartbeat, Status, Decode, Clear, and
Close so WS Hub can ingest a skimmer the same way it ingests a real
WSJT-X instance.

utf8 fields are QByteArray: quint32 length + bytes.  Empty is length 0;
null is 0xFFFFFFFF.  QTime is quint32 milliseconds since midnight.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAGIC = 0xADBCCBDA
SCHEMA = 3
# Schema 2 and 3 encode the field types we use identically.  WSJT-X starts
# outgoing datagrams at schema 2 until a server heartbeat negotiates; we
# send schema 3 (the current maximum) because this client does not listen
# for negotiation and every field here is valid in both.
MAX_SCHEMA = 3

MSG_HEARTBEAT = 0
MSG_STATUS = 1
MSG_DECODE = 2
MSG_CLEAR = 3
MSG_CLOSE = 6

QUINT32_MAX = 0xFFFFFFFF
HEARTBEAT_INTERVAL_S = 15  # NetworkMessage::pulse

# Default destination: the WS Hub / WSJT-X multicast group on port 2237.
DEFAULT_HOST = "224.0.0.73"
DEFAULT_PORT = 2237
MULTICAST_TTL = 1

# Fallback id used only by the low-level builders when the caller omits one.
DEFAULT_ID = "SKIM"


# ── Mode table (Status uses the name; Decode uses the character) ─────────
# Characters verified in official WSJT-X MainWindow::replyToCQ /
# processMessage (mainwindow.cpp): FT8 ~, FT4 +, MSK144 & (also ^),
# Q65 :, JT65 #, JT9 @, FST4/FST4W `.  JT4 $ is the longstanding
# Band Activity marker used with those modes.

@dataclass(frozen=True)
class ModeSpec:
    name: str
    char: str
    fast_mode: bool
    # None → quint32 0xFFFFFFFF (not applicable), as WSJT-X sends when
    # the T/R or FTol spinner is hidden (FT8/FT4 periods are implied).
    tr_period: int | None
    frequency_tolerance: int | None


MODES: dict[str, ModeSpec] = {
    "FT8":    ModeSpec("FT8",    "~", False, None, None),
    "FT4":    ModeSpec("FT4",    "+", False, None, None),
    "MSK144": ModeSpec("MSK144", "&", True,  15,   100),
    "Q65":    ModeSpec("Q65",    ":", False, None, None),
    "JT65":   ModeSpec("JT65",   "#", False, None, None),
    "JT9":    ModeSpec("JT9",    "@", False, None, None),
    "JT4":    ModeSpec("JT4",    "$", False, None, None),
    "FST4":   ModeSpec("FST4",   "`", False, None, None),
    "FST4W":  ModeSpec("FST4W",  "`", False, None, None),
}

# Reverse map for a Decode mode character (first character only).
_CHAR_TO_NAME = {spec.char: spec.name for spec in MODES.values()}
_CHAR_TO_NAME["^"] = "MSK144"  # WSJT-X also accepts ^ as an MSK144 marker


def mode_spec(name: str | None) -> ModeSpec:
    key = (name or "MSK144").strip().upper()
    if key in MODES:
        return MODES[key]
    # A character may be passed by mistake.
    if key in _CHAR_TO_NAME:
        return MODES[_CHAR_TO_NAME[key]]
    return ModeSpec(name or "MSK144", "?", False, None, None)


def mode_name(value: str | None) -> str:
    return mode_spec(value).name


def mode_char(value: str | None) -> str:
    return mode_spec(value).char


# ── Source / band / instance id ──────────────────────────────────────────

# Keys are engine source_mode values and a few human labels.
SOURCE_TAGS = {
    "radio": "FLEX",
    "flex": "FLEX",
    "usrp": "B210",
    "b210": "B210",
    "airspy": "AIRSPY",
    "rtlsdr": "RTL",
    "rtl": "RTL",
    "sdrangel": "SDRANGEL",
    "wav": "WAV",
}

# Same lower-edge table WS Hub uses (wims.core.bands._BANDS) so the id's
# band token matches the band WS Hub will display from Status dial.
_BANDS = [
    (1_800_000, "160m"), (3_500_000, "80m"), (5_330_000, "60m"),
    (7_000_000, "40m"), (10_100_000, "30m"), (14_000_000, "20m"),
    (18_068_000, "17m"), (21_000_000, "15m"), (24_890_000, "12m"),
    (28_000_000, "10m"), (50_000_000, "6m"), (70_000_000, "4m"),
    (144_000_000, "2m"), (222_000_000, "1.25m"), (420_000_000, "70cm"),
    (902_000_000, "33cm"), (1_240_000_000, "23cm"),
    (2_300_000_000, "13cm"), (3_300_000_000, "9cm"),
    (5_650_000_000, "6cm"), (10_000_000_000, "3cm"),
    (24_000_000_000, "1.2cm"),
]


def band_label(freq_hz: int | float) -> str:
    hz = int(freq_hz or 0)
    label = "?"
    for low, name in _BANDS:
        if hz >= low:
            label = name
        else:
            break
    return label


def source_tag(source: str | None) -> str:
    raw = (source or "").strip()
    if not raw:
        return "RADIO"
    key = raw.lower().replace(" ", "").replace("-", "")
    if key in SOURCE_TAGS:
        return SOURCE_TAGS[key]
    # Human labels from runtime._get_tuned_frequency_mhz, e.g. "USRP B210".
    folded = raw.lower()
    if "flex" in folded:
        return "FLEX"
    if "b210" in folded or "usrp" in folded:
        return "B210"
    if "airspy" in folded:
        return "AIRSPY"
    if "rtl" in folded or "nesdr" in folded:
        return "RTL"
    if "sdrangel" in folded:
        return "SDRANGEL"
    token = re.sub(r"[^A-Za-z0-9]+", "", raw).upper()
    return token[:12] or "RADIO"


_FREQ_CACHE: list[dict] | None = None


def _frequency_entries() -> list[dict]:
    global _FREQ_CACHE
    if _FREQ_CACHE is None:
        path = Path(__file__).resolve().parent / "data" / "wsjtx_frequencies.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _FREQ_CACHE = list(data.get("entries") or [])
        except (OSError, json.JSONDecodeError, AttributeError):
            _FREQ_CACHE = []
    return _FREQ_CACHE


def standard_dials(mode: str | None = None, band: str | None = None) -> list[int]:
    """Standard WSJT-X dial frequencies in Hz, optionally filtered."""
    want_mode = mode_name(mode) if mode else None
    out: list[int] = []
    for ent in _frequency_entries():
        if want_mode and str(ent.get("mode") or "").upper() != want_mode:
            continue
        if band and str(ent.get("band") or "") != band:
            continue
        mhz = ent.get("dial_mhz")
        if mhz is None:
            continue
        hz = int(round(float(mhz) * 1_000_000))
        if hz not in out:
            out.append(hz)
    return out


def primary_dial(mode: str, band: str) -> int | None:
    """The usual calling/dial frequency for this band and mode.

    Prefers the entry whose label is exactly the mode name (``FT8``,
    ``MSK144``), so ``MSK144 low`` or ``FT8 DX`` stay alternates.  Falls
    back to the first listed dial if no exact label matches.
    """
    want = mode_name(mode)
    exact = None
    first = None
    for ent in _frequency_entries():
        if str(ent.get("mode") or "").upper() != want:
            continue
        if str(ent.get("band") or "") != band:
            continue
        mhz = ent.get("dial_mhz")
        if mhz is None:
            continue
        hz = int(round(float(mhz) * 1_000_000))
        if first is None:
            first = hz
        if str(ent.get("label") or "").strip() == want:
            exact = hz
            break
    return exact if exact is not None else first


def instance_id(source: str | None, dial_hz: int, mode: str | None) -> str:
    """Stable WSJT-X UDP id for one emulated instance.

    Format: ``SKIM-{SOURCE}-{BAND}-{MODE}`` when the dial is the primary
    standard frequency for that band and mode; otherwise the dial in kHz
    is appended (``SKIM-FLEX-6m-FT8-50323``) so two dials never share an
    id.  The mapping depends only on (source, band, mode, dial).
    """
    spec = mode_spec(mode)
    band = band_label(dial_hz)
    tag = source_tag(source)
    base = f"SKIM-{tag}-{band}-{spec.name}"
    primary = primary_dial(spec.name, band)
    if primary is not None and abs(int(dial_hz) - primary) <= 1:
        return base
    khz = int(round(int(dial_hz) / 1000.0))
    return f"{base}-{khz}"


def choose_dial(rf_hz: int, mode: str | None, explicit_dial_hz: int | None = None) -> int:
    """Dial frequency for Status, and the origin for Decode delta_frequency.

    Prefer an explicit section/calling dial.  Otherwise pick the standard
    dial for this mode that keeps the audio offset in 0..4000 Hz, nearest
    to 1500 Hz.  Last resort: RF − 1500 Hz (the MSK144 / USB convention).
    """
    if explicit_dial_hz:
        return int(explicit_dial_hz)
    rf = int(rf_hz)
    mode_n = mode_name(mode)
    band = band_label(rf)
    candidates = standard_dials(mode_n, band)
    best = None
    best_score = None
    for dial in candidates:
        delta = rf - dial
        if 0 <= delta <= 4000:
            score = abs(delta - 1500)
            if best_score is None or score < best_score:
                best, best_score = dial, score
    if best is not None:
        return best
    return max(0, rf - 1500)


def in_passband(dial_hz: int, center_hz: int, bandwidth_hz: int) -> bool:
    """True if a ~3 kHz USB slice at ``dial_hz`` overlaps the IQ passband."""
    if bandwidth_hz <= 0 or center_hz <= 0:
        return False
    half = bandwidth_hz / 2.0
    lo, hi = center_hz - half, center_hz + half
    # USB energy lives roughly dial .. dial+3000.
    return (dial_hz + 3000) >= lo and dial_hz <= hi


# ── Packing ──────────────────────────────────────────────────────────────

def pack_utf8(s: str | None) -> bytes:
    """QByteArray utf8: empty is length 0; None is the null (0xFFFFFFFF)."""
    if s is None:
        return struct.pack(">I", QUINT32_MAX)
    b = s.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def pack_bool(v: bool) -> bytes:
    return struct.pack(">B", 1 if v else 0)


def pack_u32(v: int) -> bytes:
    return struct.pack(">I", int(v) & 0xFFFFFFFF)


def pack_u32_opt(v: int | None) -> bytes:
    return pack_u32(QUINT32_MAX if v is None else v)


def pack_i32(v: int) -> bytes:
    return struct.pack(">i", int(v))


def pack_u64(v: int) -> bytes:
    return struct.pack(">Q", int(v))


def pack_f64(v: float) -> bytes:
    return struct.pack(">d", float(v))


def header(msg_type: int, mid: str | None = None, schema: int = SCHEMA) -> bytes:
    return (struct.pack(">I", MAGIC)
            + struct.pack(">I", schema)
            + struct.pack(">I", msg_type)
            + pack_utf8(mid if mid is not None else DEFAULT_ID))


def qtime_ms(utc_time: str | None, extra_s: float = 0.0,
             *, from_period_start: bool = False) -> int:
    """Milliseconds since midnight UTC for a Decode QTime field.

    ``utc_time`` is ``HH:MM:SS`` or ``HH:MM:SS.mmm``.  For MSK144,
    ``utc_time`` is the 15 s period start and ``extra_s`` is the ping
    time inside that period (fractional seconds become QTime milliseconds).
    For FT8/FT4, ``utc_time`` is the period start and ``extra_s`` is DT,
    which belongs in the separate delta_time field — pass
    ``from_period_start=False`` and leave extra_s out of the clock.
    """
    ms = 0
    if utc_time:
        try:
            parts = utc_time.strip().split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            sec = float(parts[2]) if len(parts) > 2 else 0.0
            ms = int(round((h * 3600 + m * 60 + sec) * 1000))
        except (ValueError, IndexError):
            ms = 0
    if from_period_start and extra_s:
        ms += int(round(float(extra_s) * 1000))
    return ms % 86_400_000


def is_low_confidence(decode: dict | None = None, message: str | None = None) -> bool:
    """True when this decode should set the Decode low_confidence flag.

    WSJT-X sets the flag when the 37-character message field ends in
    ``?`` (AP quality < 0.17).  a1..a7 annotations alone are not
    low-confidence.  An explicit ``low_confidence`` key wins.
    """
    if decode:
        if "low_confidence" in decode and decode["low_confidence"] is not None:
            return bool(decode["low_confidence"])
        message = message if message is not None else decode.get("message")
        flags = str(decode.get("ap") or decode.get("jt9_line") or "")
        if "?" in flags.split() or (isinstance(flags, str) and flags.rstrip().endswith("?")):
            # Only treat a trailing ? token as the quality marker, not a
            # '?' buried in some other field.
            if re.search(r"(?:^|\s)\?(?:\s|$)", flags) or flags.rstrip().endswith("?"):
                # jt9_line is the whole stdout line; '?' at the end is the flag.
                if "jt9_line" in decode and decode.get("ap") is None:
                    return flags.rstrip().endswith("?")
                return True
    text = (message or "").rstrip()
    return text.endswith("?")


def is_multicast_ip(host: str) -> bool:
    try:
        parts = [int(p) for p in (host or "").split(".")]
    except ValueError:
        return False
    return len(parts) == 4 and 224 <= parts[0] <= 239


# ── Builders ─────────────────────────────────────────────────────────────

def _version_and_revision(version: str | None, revision: str | None) -> tuple[str, str]:
    if version is None or revision is None:
        try:
            from . import __version__ as pkg_version
            from .buildinfo import git_hash
        except Exception:
            pkg_version, git_hash = "0.1.4", lambda: ""
        if version is None:
            version = pkg_version
        if revision is None:
            try:
                revision = git_hash() or ""
            except Exception:
                revision = ""
    return version, revision


def build_heartbeat(max_schema: int = MAX_SCHEMA, version: str | None = None,
                    revision: str | None = None, id: str | None = None,
                    schema: int = SCHEMA) -> bytes:
    """Heartbeat (type 0).  Default version is this WS Skim release, not a
    fake WSJT-X version."""
    version, revision = _version_and_revision(version, revision)
    return (header(MSG_HEARTBEAT, id, schema)
            + pack_u32(max_schema)
            + pack_utf8(version)
            + pack_utf8(revision))


def build_status(dial_freq_hz: int, my_call: str, my_grid: str,
                 mode: str | None = None, id: str | None = None,
                 *, tx_enabled: bool = False, transmitting: bool = False,
                 decoding: bool = False, rx_df: int = 1500, tx_df: int = 1500,
                 dx_call: str = "", report: str = "", dx_grid: str = "",
                 sub_mode: str = "", config_name: str = "Default",
                 tx_message: str = "", schema: int = SCHEMA) -> bytes:
    """Status (type 1).  Mode/TX Mode are the human names (``FT8``), not
    the Decode characters.  Receive-only: TX Enabled and Transmitting
    default false."""
    spec = mode_spec(mode)
    return (header(MSG_STATUS, id, schema)
            + pack_u64(int(dial_freq_hz))
            + pack_utf8(spec.name)
            + pack_utf8(dx_call)
            + pack_utf8(report)
            + pack_utf8(spec.name)        # TX Mode
            + pack_bool(tx_enabled)
            + pack_bool(transmitting)
            + pack_bool(decoding)
            + pack_u32(rx_df)
            + pack_u32(tx_df)
            + pack_utf8(my_call)
            + pack_utf8(my_grid)
            + pack_utf8(dx_grid)
            + pack_bool(False)            # TX Watchdog
            + pack_utf8(sub_mode)
            + pack_bool(spec.fast_mode)
            + struct.pack(">B", 0)        # Special operation mode: NONE
            + pack_u32_opt(spec.frequency_tolerance)
            + pack_u32_opt(spec.tr_period)
            + pack_utf8(config_name)
            + pack_utf8(tx_message))


def build_decode(decode: dict, my_call: str = "", dial_freq_hz: int = 0,
                 id: str | None = None, schema: int = SCHEMA) -> bytes:
    """Decode (type 2).

    ``my_call`` is accepted for backward compatibility and is not on the
    wire (NetworkMessage.hpp has no DE field in Decode).  ``dial_freq_hz``
    is used only when the decode dict has no audio/dial of its own, to
    compute delta_frequency = RF − dial.

    The Mode field is the WSJT-X character (``~`` FT8, ``+`` FT4,
    ``&`` MSK144, …), not the mode word.
    """
    msg = decode.get("message") or ""
    snr = decode.get("jt9_snr") or 0
    mode = decode.get("mode") or "MSK144"
    spec = mode_spec(mode)
    delta_t, time_ms, delta_freq = decode_offsets(decode, dial_freq_hz)
    low = is_low_confidence(decode, msg)
    off_air = bool(decode.get("off_air", False))
    return (header(MSG_DECODE, id, schema)
            + pack_bool(True)             # New
            + pack_u32(time_ms)
            + pack_i32(int(snr))
            + pack_f64(delta_t)
            + pack_u32(delta_freq)
            + pack_utf8(spec.char)
            + pack_utf8(msg)
            + pack_bool(low)
            + pack_bool(off_air))


def decode_offsets(decode: dict, fallback_dial_hz: int = 0) -> tuple[float, int, int]:
    """Return ``(delta_time, time_ms, delta_frequency)`` for one decode.

    FT8/FT4: ``t_sec`` is DT; QTime is the period start.  MSK144:
    ``t_sec`` is the ping time inside the 15 s window and is folded into
    QTime (milliseconds); delta_time stays 0 unless the dict supplies
    a separate DT.
    """
    mode = mode_name(decode.get("mode"))
    t_sec = float(decode.get("t_sec") or 0.0)
    utc = decode.get("utc_time")
    rf_hz, dial_hz, audio_hz = rf_dial_audio(decode, fallback_dial_hz)
    delta_freq = int(round(audio_hz if audio_hz is not None else (rf_hz - dial_hz)))
    if delta_freq < 0:
        # quint32 on the wire; keep a non-negative offset by adjusting
        # only the reported DF, not the dial (Status carries the dial).
        delta_freq = 0
    if mode in {"FT8", "FT4", "Q65", "FST4", "FST4W", "JT65", "JT9", "JT4"}:
        return t_sec, qtime_ms(utc, from_period_start=False), delta_freq
    # MSK144 and other fast modes: t_sec is time-in-period.
    dt = float(decode.get("dt_s") or 0.0)
    return dt, qtime_ms(utc, t_sec, from_period_start=True), delta_freq


def rf_dial_audio(decode: dict, fallback_dial_hz: int = 0) -> tuple[int, int, float | None]:
    """``(rf_hz, dial_hz, audio_hz_or_None)`` from a decode dict."""
    audio = decode.get("audio_hz")
    dial = decode.get("dial_hz")
    if dial is None and decode.get("dial_khz") is not None:
        dial = float(decode["dial_khz"]) * 1000.0
    radio_khz = decode.get("radio_khz")
    rf = None
    if radio_khz is not None:
        rf = int(round(float(radio_khz) * 1000.0))
    if dial is not None:
        dial_hz = int(round(float(dial)))
    else:
        dial_hz = choose_dial(rf or fallback_dial_hz, decode.get("mode"),
                              fallback_dial_hz or None)
    if rf is None:
        if audio is not None:
            rf = dial_hz + int(round(float(audio)))
        else:
            rf = fallback_dial_hz or dial_hz
    audio_out = float(audio) if audio is not None else float(rf - dial_hz)
    return int(rf), int(dial_hz), audio_out


def build_clear(id: str | None = None, schema: int = SCHEMA) -> bytes:
    """Clear (type 3) — outbound has no Window field (that is inbound-only)."""
    return header(MSG_CLEAR, id, schema)


def build_close(id: str | None = None, schema: int = SCHEMA) -> bytes:
    """Close (type 6) — sent immediately before a graceful shutdown."""
    return header(MSG_CLOSE, id, schema)


# ── Independent parser (NetworkMessage.hpp) ──────────────────────────────

class _Reader:
    def __init__(self, data: bytes):
        self._d = data
        self._i = 0

    def _take(self, n: int) -> bytes:
        if self._i + n > len(self._d):
            raise ValueError(f"truncated: need {n} at {self._i} of {len(self._d)}")
        chunk = self._d[self._i:self._i + n]
        self._i += n
        return chunk

    def u8(self) -> int:
        return self._take(1)[0]

    def boolean(self) -> bool:
        return self._take(1)[0] != 0

    def u32(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self._take(8))[0]

    def f64(self) -> float:
        return struct.unpack(">d", self._take(8))[0]

    def u32_opt(self) -> int | None:
        v = self.u32()
        return None if v == QUINT32_MAX else v

    def utf8(self) -> str | None:
        n = self.u32()
        if n == QUINT32_MAX:
            return None
        return self._take(n).decode("utf-8", "replace")


def parse(data: bytes) -> dict | None:
    """Parse one datagram into a dict, or None if it is not WSJT-X.

    Independent of WS Hub: written from NetworkMessage.hpp so a test can
    assert field values without importing that repo.  ``utf8`` empty
    strings are ``""``; nulls are ``None``.
    """
    if len(data) < 8:
        return None
    r = _Reader(data)
    try:
        if r.u32() != MAGIC:
            return None
        schema = r.u32()
        mtype = r.u32()
        mid = r.utf8()
    except ValueError:
        return None
    msg: dict = {"schema": schema, "type": mtype, "id": mid}
    try:
        if mtype == MSG_HEARTBEAT:
            msg["max_schema"] = r.u32()
            msg["version"] = r.utf8()
            msg["revision"] = r.utf8()
        elif mtype == MSG_STATUS:
            msg["dial_frequency"] = r.u64()
            msg["mode"] = r.utf8()
            msg["dx_call"] = r.utf8()
            msg["report"] = r.utf8()
            msg["tx_mode"] = r.utf8()
            msg["tx_enabled"] = r.boolean()
            msg["transmitting"] = r.boolean()
            msg["decoding"] = r.boolean()
            msg["rx_df"] = r.u32()
            msg["tx_df"] = r.u32()
            msg["de_call"] = r.utf8()
            msg["de_grid"] = r.utf8()
            msg["dx_grid"] = r.utf8()
            msg["tx_watchdog"] = r.boolean()
            msg["sub_mode"] = r.utf8()
            msg["fast_mode"] = r.boolean()
            msg["special_op_mode"] = r.u8()
            msg["frequency_tolerance"] = r.u32_opt()
            msg["tr_period"] = r.u32_opt()
            msg["config_name"] = r.utf8()
            msg["tx_message"] = r.utf8()
        elif mtype == MSG_DECODE:
            msg["new"] = r.boolean()
            msg["time_ms"] = r.u32()
            msg["snr"] = r.i32()
            msg["delta_time"] = r.f64()
            msg["delta_frequency"] = r.u32()
            msg["mode"] = r.utf8()
            msg["message"] = r.utf8()
            msg["low_confidence"] = r.boolean()
            msg["off_air"] = r.boolean() if r._i < len(data) else False
        elif mtype in (MSG_CLEAR, MSG_CLOSE):
            pass
        else:
            msg["payload"] = data[r._i:]
    except ValueError:
        pass
    return msg


def software_id() -> tuple[str, str]:
    """``(version, revision)`` for Heartbeat, from the package."""
    return _version_and_revision(None, None)
