"""A panadapter WS Skim creates, owns and removes on the Flex radio.

With "WS Skim makes its own panadapter" ticked, WS Skim no longer follows
the operator's SmartSDR panadapter.  It creates one of its own, steers its
centre to the band it is decoding, binds a free DAX IQ channel to it and
runs that stream at 96 kHz (48 kHz while MSK144 is on).  The operator's
own panadapters, slices and streams are never touched.

Tested behaviour of SmartSDR v4 (FLEX-8600, v4.3.19) this relies on
-------------------------------------------------------------------
* ``display pan c freq=<MHz> x=<px> y=<px>`` answers ``<pan>,<waterfall>``.
  The pan belongs to the GUI client (it shows on the radio's front panel /
  SmartSDR) and comes with a new slice.  The radio may move the centre to
  where that slice lands, so the centre is set again after creating.
* ``display pan set <pan> center=<MHz>`` answers the centre it applied.  It
  only moves a pan that has a slice, so the slice is kept, muted, and tuned
  to the centre each time (a slice near the edge would drag the pan back).
* The radio does NOT send a status line when our own command moves a pan;
  ``sub pan all`` re-sends the full status, which is how the centre is
  checked.
* ``stream set <stream> daxiq_rate=96000`` changes the rate of a running
  stream (packet class code 0x02E4 = 48 kHz, 0x02E5 = 96 kHz, 0x02E6 =
  192 kHz).  ``radio daxiq_available=`` reports the capacity left (48 kHz
  costs 2 units, 96 kHz 4, 192 kHz 8).
* The pan and its slice survive our TCP connection closing; the stream does
  not.  So the ids are written to a small file and a pan left behind by a
  crash is removed on the next start, but only after checking it is still
  the one we made (same DAX channel, same slice).

Safety rules (an early test removed an operator's slices)
---------------------------------------------------------
* Only ids that came back from OUR create commands, or a recorded id that
  still matches, are ever changed or removed.
* Nothing is created when the radio reported no panadapters at all (the
  status snapshot is then not trustworthy).
* ``tx`` is never set on any slice.  DAX channel 1 is used only when no pan
  or stream has it.
* For WSJT-X for WS Skim our slice may be held at a dial (``hold_slice``)
  and given a DAX audio channel (``set_dax_audio``); both only on our slice.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence

log = logging.getLogger("flexclient.owned_pan")

#: VITA-49 packet class code (low 16 bits of the class id) → DAX IQ rate.
DAXIQ_CLASS_RATES = {0x02E3: 24_000, 0x02E4: 48_000, 0x02E5: 96_000, 0x02E6: 192_000}
#: DAX IQ capacity units each rate costs.
DAXIQ_UNITS = {24_000: 1, 48_000: 2, 96_000: 4, 192_000: 8}
#: DAX channels tried for our stream, in order.
DAX_CHANNEL_ORDER = (2, 3, 4, 1)
#: Width of an FT8/FT4 sub-band above its dial frequency, kHz.
SUBBAND_KHZ = 3.2
#: Pan size requested at create (pixels); only affects the radio's display.
PAN_PIXELS = (1024, 700)


class OwnedPanError(RuntimeError):
    """Setting up the owned panadapter failed; nothing of the operator's was touched."""


def _kv(payload: str) -> Dict[str, str]:
    out = {}
    for tok in payload.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k] = v
    return out


def _hex(v) -> Optional[int]:
    try:
        return int(str(v), 16)
    except (TypeError, ValueError):
        return None


def _mhz(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f / 1e6 if f >= 1e6 else f


class RadioState:
    """Pans, slices and DAX IQ streams as the radio's status lines report them."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pans: Dict[int, dict] = {}
        self.slices: Dict[int, dict] = {}
        self.streams: Dict[int, dict] = {}
        self.daxiq_available: Optional[int] = None
        self.daxiq_capacity: Optional[int] = None
        self.lines_seen = 0

    def feed(self, line: str) -> None:
        """Absorb one ``S<handle>|...`` status line (others are ignored)."""
        try:
            self._feed(line)
        except Exception as exc:          # never let a bad line break the reader
            log.debug("status parse error %r: %s", line[:120], exc)

    def _feed(self, line: str) -> None:
        if not line.startswith("S") or "|" not in line:
            return
        payload = line.split("|", 1)[1].strip()
        toks = payload.split()
        if not toks:
            return
        with self.lock:
            self.lines_seen += 1
            if toks[0] == "display" and len(toks) >= 3 and toks[1] == "pan":
                pid = _hex(toks[2])
                if not pid:
                    return
                kv = _kv(payload)
                if kv.get("removed") == "1" or (len(toks) == 3) or "removed" in toks[3:]:
                    self.pans.pop(pid, None)
                    return
                entry = self.pans.setdefault(pid, {})
                if "center" in kv:
                    entry["center"] = _mhz(kv["center"])
                if "bandwidth" in kv:
                    entry["bandwidth"] = kv["bandwidth"]
                for k in ("rxant", "client_handle", "daxiq_channel"):
                    if k in kv:
                        entry[k] = kv[k]
            elif toks[0] == "slice" and len(toks) >= 2:
                try:
                    n = int(toks[1])
                except ValueError:
                    return
                kv = _kv(payload)
                if kv.get("in_use") == "0" or "removed" in toks[2:]:
                    self.slices.pop(n, None)
                    return
                entry = self.slices.setdefault(n, {})
                if "pan" in kv:
                    entry["pan"] = _hex(kv["pan"])
                if "RF_frequency" in kv:
                    entry["freq"] = _mhz(kv["RF_frequency"])
                for k in ("in_use", "tx", "audio_mute", "rxant", "client_handle", "mode",
                          "index_letter", "dax"):
                    if k in kv:
                        entry[k] = kv[k]
            elif toks[0] == "stream" and len(toks) >= 2:
                sid = _hex(toks[1])
                if sid is None:
                    return
                kv = _kv(payload)
                if "removed" in toks[2:] or kv.get("removed") == "1":
                    self.streams.pop(sid, None)
                    return
                if kv.get("type", self.streams.get(sid, {}).get("type")) != "dax_iq":
                    return
                entry = self.streams.setdefault(sid, {})
                entry.update({k: v for k, v in kv.items()
                              if k in ("type", "daxiq_channel", "pan", "daxiq_rate",
                                       "client_handle", "active")})
            elif toks[0] == "radio":
                kv = _kv(payload)
                if "daxiq_available" in kv:
                    self.daxiq_available = int(kv["daxiq_available"])
                if "daxiq_capacity" in kv:
                    self.daxiq_capacity = int(kv["daxiq_capacity"])

    # ── queries ──────────────────────────────────────────────────────────
    def pan_ids(self) -> set:
        with self.lock:
            return set(self.pans)

    def slice_ids(self) -> set:
        with self.lock:
            return set(self.slices)

    def center_of(self, pan_id: int) -> Optional[float]:
        with self.lock:
            return self.pans.get(pan_id, {}).get("center")

    def used_dax_channels(self) -> set:
        used = set()
        with self.lock:
            for p in self.pans.values():
                ch = p.get("daxiq_channel")
                if ch not in (None, "", "0"):
                    used.add(int(ch))
            for s in self.streams.values():
                ch = s.get("daxiq_channel")
                if ch not in (None, "", "0"):
                    used.add(int(ch))
        return used


# ── pure helpers (unit-tested) ───────────────────────────────────────────────

def usable_half_span_khz(rate: int) -> float:
    """Half of the IQ band the decoders search (the FT8 sections use 0.45·fs)."""
    return 0.45 * rate / 1000.0


def choose_center_mhz(dials_mhz: Sequence[float], rate: int,
                      primary_mhz: Optional[float] = None,
                      subband_khz: float = SUBBAND_KHZ) -> float:
    """Pan centre that holds every sub-band in ``dials_mhz`` with DC off them.

    Each dial covers ``[dial, dial + subband_khz]``.  Candidates are the
    middle of every gap between sub-bands and just outside either end; the
    one keeping DC farthest from any sub-band wins, provided everything fits
    inside the usable band.  If not all fit (48 kHz with FT8 + FT4 on 40 m),
    the dials farthest from ``primary_mhz`` are dropped until they do.
    """
    dials = sorted({round(float(d), 6) for d in dials_mhz if d})
    if not dials:
        raise ValueError("no dial frequencies")
    primary = float(primary_mhz) if primary_mhz else dials[0]
    half = usable_half_span_khz(rate) - 0.5
    sb = subband_khz / 1000.0
    while True:
        ivs = [(d, d + sb) for d in dials]
        lo, hi = ivs[0][0], max(b for _, b in ivs)
        if (hi - lo) * 1000.0 <= 2 * half or len(dials) == 1:
            break
        far = max(dials, key=lambda d: (abs(d - primary), d))
        dials.remove(far)
    ivs = [(d, d + sb) for d in dials]
    lo, hi = ivs[0][0], max(b for _, b in ivs)
    cands = []
    merged: List[List[float]] = []
    for a, b in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for (a1, b1), (a2, b2) in zip(merged, merged[1:]):
        cands.append((b1 + a2) / 2.0)
    for margin_khz in (3.0, 2.0, 1.0, 0.5):
        cands.append(lo - margin_khz / 1000.0)
        cands.append(hi + margin_khz / 1000.0)
    mid = (lo + hi) / 2.0

    def fits(c):
        return (c - lo) * 1000.0 <= half and (hi - c) * 1000.0 <= half

    def dc_clear(c):
        return min(0.0 if a <= c <= b else min(abs(c - a), abs(c - b)) for a, b in merged)

    ok = [c for c in cands if fits(c)]
    if not ok:
        return round(mid, 6)
    best = max(ok, key=lambda c: (round(dc_clear(c) * 1e6), -abs(c - mid)))
    return round(best, 6)


def pick_dax_channel(used: Iterable[int], order: Sequence[int] = DAX_CHANNEL_ORDER) -> Optional[int]:
    used = set(used)
    for ch in order:
        if ch not in used:
            return ch
    return None


def rate_from_class_id(class_id: Optional[int]) -> Optional[int]:
    if class_id is None:
        return None
    return DAXIQ_CLASS_RATES.get(int(class_id) & 0xFFFF)


# ── the owned pan ────────────────────────────────────────────────────────────

class OwnedPan:
    """Creates, steers and removes WS Skim's own pan + slice + DAX IQ stream.

    ``send(cmd, timeout)`` sends one command and returns the reply payload or
    raises ``RuntimeError`` (rejected / timeout).  ``state`` must be fed every
    status line the radio sends.  ``store_path`` (optional) is where the ids
    are kept so a pan left behind by a crash can be removed next time.
    """

    #: Centre error that counts as "someone moved it", kHz.
    STEER_TOLERANCE_KHZ = 0.5
    #: Seconds between checks of the centre (each check re-sends ``sub pan all``).
    CHECK_INTERVAL_S = 10.0
    #: Fewest seconds between two re-steers.
    RESTEER_MIN_S = 5.0

    def __init__(self, send: Callable[..., str], state: RadioState, *,
                 client_ip: str, udp_port: int, store_path: Optional[str] = None,
                 radio_key: str = "", sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 settle_s: float = 0.4):
        self._send_raw = send
        self.state = state
        self.client_ip = client_ip
        self.udp_port = int(udp_port)
        self.store_path = store_path
        self.radio_key = radio_key
        self._sleep = sleep
        self._clock = clock
        self._settle = settle_s
        self.pan_id: Optional[int] = None
        self.waterfall_id: Optional[int] = None
        self.slice_id: Optional[int] = None
        self.stream_id: Optional[int] = None
        self.dax_channel: Optional[int] = None
        self.rate: Optional[int] = None
        self.target_center_mhz: Optional[float] = None
        self.reported_center_mhz: Optional[float] = None
        self.resteer_count = 0
        #: Where our slice is held for WSJT-X for WS Skim (MHz), or None to
        #: park it at the pan centre.
        self.slice_hold_mhz: Optional[float] = None
        #: DAX audio channel put on our slice for WSJT-X for WS Skim (None: leave).
        self.dax_audio_channel: Optional[int] = None
        self._last_check = 0.0
        self._last_resteer = -1e9
        self._lock = threading.RLock()

    # ── plumbing ─────────────────────────────────────────────────────────
    def _send(self, cmd: str, timeout: float = 5.0, retry: bool = False) -> str:
        """Send; on a timeout optionally try once more (idempotent commands only)."""
        try:
            return self._call(cmd, timeout)
        except RuntimeError as exc:
            if retry and "timeout" in str(exc).lower():
                log.warning("No reply to %r; trying once more", cmd)
                return self._call(cmd, timeout)
            raise

    def _call(self, cmd: str, timeout: float) -> str:
        try:
            return self._send_raw(cmd, timeout)
        except OSError as exc:          # connection gone
            raise RuntimeError(f"send failed: {cmd}: {exc}") from exc

    def refresh(self) -> None:
        """Ask the radio to re-send pan and slice status (it won't by itself
        after our own commands)."""
        for cmd in ("sub pan all", "sub slice all"):
            try:
                self._send(cmd, timeout=3.0)
            except RuntimeError as exc:
                log.debug("%s: %s", cmd, exc)
        self._sleep(self._settle)

    def _save(self) -> None:
        if not self.store_path:
            return
        rec = {
            "radio": self.radio_key,
            "pan": f"0x{self.pan_id:08X}" if self.pan_id else None,
            "slice": self.slice_id,
            "stream": f"0x{self.stream_id:08X}" if self.stream_id else None,
            "dax_channel": self.dax_channel,
            "saved": time.time(),
        }
        try:
            os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
            tmp = self.store_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, self.store_path)
        except OSError as exc:
            log.warning("Could not record owned pan ids: %s", exc)

    def _forget(self) -> None:
        if self.store_path:
            try:
                os.remove(self.store_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("Could not clear owned pan record: %s", exc)

    def _load(self) -> Optional[dict]:
        if not self.store_path or not os.path.exists(self.store_path):
            return None
        try:
            with open(self.store_path, encoding="utf-8") as f:
                rec = json.load(f)
            return rec if isinstance(rec, dict) else None
        except (OSError, ValueError):
            return None

    # ── start-up ─────────────────────────────────────────────────────────
    def require_snapshot(self) -> None:
        if not self.state.pan_ids():
            raise OwnedPanError(
                "The radio reported no panadapters; not creating one "
                "(start SmartSDR or the radio's front panel first)")

    def remove_leftover(self) -> bool:
        """Remove a pan a previous run left behind, if it is verifiably ours.

        Returns True when one was removed.  A record that no longer matches
        the radio is forgotten without touching anything.
        """
        rec = self._load()
        if not rec:
            return False
        if self.radio_key and rec.get("radio") and rec.get("radio") != self.radio_key:
            log.info("Owned-pan record is for another radio; leaving it")
            return False
        pan = _hex(rec.get("pan"))
        ch = rec.get("dax_channel")
        sl = rec.get("slice")
        with self.state.lock:
            p = dict(self.state.pans.get(pan, {})) if pan else {}
            s = dict(self.state.slices.get(sl, {})) if sl is not None else {}
            stream = _hex(rec.get("stream"))
            st = dict(self.state.streams.get(stream, {})) if stream else {}
        matched = bool(pan and p
                       and ch is not None and str(p.get("daxiq_channel")) == str(ch)
                       and (sl is None or s.get("pan") == pan))
        if not matched:
            log.info("Owned-pan record does not match the radio any more; forgetting it")
            self._forget()
            return False
        if stream and st and str(st.get("daxiq_channel")) == str(ch) and _hex(st.get("pan")) == pan:
            try:
                self._send(f"stream remove 0x{stream:08X}", timeout=3.0)
            except RuntimeError as exc:
                log.debug("leftover stream remove: %s", exc)
        try:
            self._send(f"display pan r 0x{pan:08X}", timeout=3.0)
        except RuntimeError as exc:
            log.warning("Could not remove leftover pan 0x%08X: %s", pan, exc)
            return False
        log.info("Removed panadapter 0x%08X left over from the last run", pan)
        self._forget()
        return True

    def create(self, center_mhz: float, rxant: Optional[str] = None) -> int:
        """Create our pan (and its slice), mute the slice, steer the centre."""
        with self._lock:
            self.require_snapshot()
            slices_before = self.state.slice_ids()
            x, y = PAN_PIXELS
            resp = self._send(f"display pan c freq={center_mhz:.6f} x={x} y={y}", timeout=6.0)
            ids = [t.strip() for t in resp.strip().split(",") if t.strip()]
            pan = _hex(ids[0]) if ids else None
            if not pan:
                raise OwnedPanError(f"Radio did not return a panadapter id ({resp!r})")
            self.pan_id = pan
            self.waterfall_id = _hex(ids[1]) if len(ids) > 1 else None
            self._save()
            log.info("Created panadapter 0x%08X", pan)

            # The new slice: in use now, not before, and on our pan.
            slice_id = None
            for _ in range(10):
                self._sleep(0.2)
                with self.state.lock:
                    new = [n for n, s in self.state.slices.items()
                           if n not in slices_before and s.get("pan") == pan]
                if new:
                    slice_id = min(new)
                    break
            if slice_id is None:
                self.refresh()
                with self.state.lock:
                    new = [n for n, s in self.state.slices.items()
                           if n not in slices_before and s.get("pan") == pan]
                slice_id = min(new) if new else None
            self.slice_id = slice_id
            self._save()
            if slice_id is not None:
                try:
                    self._send(f"slice set {slice_id} audio_mute=1", retry=True)
                except RuntimeError as exc:
                    log.warning("Could not mute slice %s: %s", slice_id, exc)
                if self.dax_audio_channel:
                    self.set_dax_audio(self.dax_audio_channel)
                with self.state.lock:
                    if self.state.slices.get(slice_id, {}).get("tx") == "1":
                        log.warning("The radio made WS Skim's slice %s the transmit slice "
                                    "(it was the only slice); WS Skim never changes that",
                                    slice_id)
            else:
                log.warning("No slice came with panadapter 0x%08X; its centre may not steer", pan)
            if rxant:
                try:
                    self._send(f"display pan set 0x{pan:08X} rxant={rxant}", retry=True)
                except RuntimeError as exc:
                    log.warning("Could not set rxant=%s: %s", rxant, exc)
            self.steer(center_mhz, force=True)
            return pan

    def steer(self, center_mhz: float, force: bool = False) -> Optional[float]:
        """Move our pan's centre (and park our slice there).  Returns the centre
        the radio says it applied, or None when that is unknown."""
        with self._lock:
            if not self.pan_id:
                return None
            c = round(float(center_mhz), 6)
            if (not force and self.target_center_mhz is not None
                    and abs(c - self.target_center_mhz) < 1e-6
                    and self.reported_center_mhz is not None
                    and abs(self.reported_center_mhz - c) * 1000 <= self.STEER_TOLERANCE_KHZ):
                return self.reported_center_mhz
            self.target_center_mhz = c
            applied = None
            try:
                resp = self._send(f"display pan set 0x{self.pan_id:08X} center={c:.6f}", retry=True)
                applied = _mhz(resp.strip()) if resp.strip() else None
            except RuntimeError as exc:
                log.warning("Could not set pan centre %.6f: %s", c, exc)
            if self.slice_id is not None:
                park = self.park_mhz(c)
                try:
                    self._send(f"slice tune {self.slice_id} {park:.6f}", retry=True)
                except RuntimeError as exc:
                    log.warning("Could not park slice %s: %s", self.slice_id, exc)
            if applied is not None:
                self.reported_center_mhz = applied
                with self.state.lock:
                    self.state.pans.setdefault(self.pan_id, {})["center"] = applied
            self._last_check = self._clock()
            return applied

    #: A held slice further than this from the pan centre is let go (kHz);
    #: it would leave the received band (and drag the pan along with it).
    HOLD_MAX_OFFSET_KHZ = 40.0

    def park_mhz(self, center_mhz: float) -> float:
        """Where our slice goes when the pan centre is ``center_mhz``: the
        WSJT-X hold frequency when it is inside the received band, else the
        centre (a band change lets go of the hold)."""
        hold = self.slice_hold_mhz
        if hold is not None and abs(hold - center_mhz) * 1000.0 <= self.HOLD_MAX_OFFSET_KHZ:
            return round(hold, 6)
        if hold is not None:
            log.info("Letting go of slice hold %.6f (pan centre now %.6f)", hold, center_mhz)
            self.slice_hold_mhz = None
        return round(float(center_mhz), 6)

    def hold_slice(self, freq_mhz: Optional[float]) -> bool:
        """Tune OUR slice to ``freq_mhz`` and keep it there (None: back to the
        centre).  Returns False when there is no slice of ours or the
        frequency is outside the received band."""
        with self._lock:
            if self.slice_id is None or not self.pan_id:
                return False
            centre = self.target_center_mhz
            if freq_mhz is not None and centre is not None and \
                    abs(float(freq_mhz) - centre) * 1000.0 > self.HOLD_MAX_OFFSET_KHZ:
                return False
            self.slice_hold_mhz = None if freq_mhz is None else round(float(freq_mhz), 6)
            target = self.slice_hold_mhz if self.slice_hold_mhz is not None else centre
            if target is None:
                return True
            try:
                self._send(f"slice tune {self.slice_id} {target:.6f}", retry=True)
            except RuntimeError as exc:
                log.warning("Could not tune slice %s: %s", self.slice_id, exc)
                return False
            return True

    def set_dax_audio(self, channel: Optional[int]) -> bool:
        """Put DAX audio ``channel`` on OUR slice (for WSJT-X for WS Skim)."""
        with self._lock:
            self.dax_audio_channel = int(channel) if channel else None
            if self.slice_id is None or not self.dax_audio_channel:
                return False
            try:
                self._send(f"slice set {self.slice_id} dax={self.dax_audio_channel}", retry=True)
            except RuntimeError as exc:
                log.warning("Could not set DAX audio channel %s on slice %s: %s",
                            self.dax_audio_channel, self.slice_id, exc)
                return False
            return True

    @property
    def slice_letter(self) -> Optional[str]:
        if self.slice_id is None:
            return None
        with self.state.lock:
            letter = self.state.slices.get(self.slice_id, {}).get("index_letter")
        if letter:
            return str(letter)
        return chr(ord("A") + int(self.slice_id)) if 0 <= int(self.slice_id) < 8 else None

    @property
    def tx_on_our_slice(self) -> bool:
        """True when the radio's transmit slice is ours (WS Skim never sets it)."""
        if self.slice_id is None:
            return False
        with self.state.lock:
            return self.state.slices.get(self.slice_id, {}).get("tx") == "1"

    def verify_center(self) -> Optional[float]:
        """Re-read our pan's centre from the radio."""
        if not self.pan_id:
            return None
        self.refresh()
        got = self.state.center_of(self.pan_id)
        if got is not None:
            self.reported_center_mhz = got
        return got

    def check(self, force: bool = False) -> None:
        """Periodic: re-steer if someone else moved our pan's centre."""
        with self._lock:
            if not self.pan_id or self.target_center_mhz is None:
                return
            now = self._clock()
            if not force and now - self._last_check < self.CHECK_INTERVAL_S:
                return
            self._last_check = now
            if self.pan_id not in self.state.pan_ids():
                # Gone from the radio (removed in SmartSDR?).  Never recreate
                # silently; the source restart path handles it.
                log.warning("WS Skim's panadapter 0x%08X is gone from the radio", self.pan_id)
                return
            got = self.verify_center()
            if got is None:
                return
            if abs(got - self.target_center_mhz) * 1000 > self.STEER_TOLERANCE_KHZ:
                if now - self._last_resteer < self.RESTEER_MIN_S:
                    return
                self._last_resteer = now
                self.resteer_count += 1
                log.info("Panadapter 0x%08X centre moved to %.6f; steering back to %.6f",
                         self.pan_id, got, self.target_center_mhz)
                self.steer(self.target_center_mhz, force=True)

    @property
    def gone(self) -> bool:
        return bool(self.pan_id) and self.state.lines_seen > 0 and self.pan_id not in self.state.pan_ids()

    def start_stream(self, rate: int) -> int:
        """Bind a free DAX IQ channel to our pan and create the stream."""
        with self._lock:
            if not self.pan_id:
                raise OwnedPanError("No owned panadapter")
            ch = pick_dax_channel(self.state.used_dax_channels())
            if ch is None:
                raise OwnedPanError("All DAX IQ channels are in use")
            self._send(f"dax iq set {ch} pan=0x{self.pan_id:08X}", retry=True)
            self.dax_channel = ch
            self._save()
            resp = self._send(f"stream create daxiq={ch} ip={self.client_ip} port={self.udp_port}")
            sid = _hex(resp.strip())
            if not sid:
                raise OwnedPanError(f"Radio did not return a stream id ({resp!r})")
            self.stream_id = sid
            self.rate = 48_000
            self._save()
            log.info("DAX IQ channel %d → stream 0x%08X on pan 0x%08X", ch, sid, self.pan_id)
            self.set_rate(rate)
            return sid

    def affordable_rate(self, rate: int) -> int:
        """``rate`` if the radio has DAX IQ capacity for it, else lower."""
        avail = self.state.daxiq_available
        if avail is None:
            return rate
        have = avail + DAXIQ_UNITS.get(self.rate or 0, 0)
        for r in sorted(DAXIQ_UNITS, reverse=True):
            if r <= rate and DAXIQ_UNITS[r] <= have:
                return r
        return 48_000

    def set_rate(self, rate: int) -> int:
        """Set our stream's rate; returns the rate now in effect."""
        with self._lock:
            if not self.stream_id:
                return self.rate or 48_000
            want = self.affordable_rate(int(rate))
            if want != int(rate):
                log.warning("DAX IQ capacity left is too small for %d Hz; using %d Hz", rate, want)
            if want == self.rate:
                return want
            try:
                self._send(f"stream set 0x{self.stream_id:08X} daxiq_rate={want}", retry=True)
                self.rate = want
                log.info("DAX IQ stream 0x%08X rate %d Hz", self.stream_id, want)
            except RuntimeError as exc:
                log.warning("Could not set DAX IQ rate %d: %s", want, exc)
            return self.rate or 48_000

    def teardown(self) -> None:
        """Remove our stream and pan (its slice goes with it).  Never blocks long."""
        with self._lock:
            ok = True
            if self.stream_id:
                try:
                    self._send(f"stream remove 0x{self.stream_id:08X}", timeout=2.0)
                except RuntimeError as exc:
                    log.debug("stream remove: %s", exc)
                self.stream_id = None
            if self.pan_id:
                try:
                    self._send(f"display pan r 0x{self.pan_id:08X}", timeout=2.0)
                    log.info("Removed panadapter 0x%08X", self.pan_id)
                except RuntimeError as exc:
                    ok = False
                    log.warning("Could not remove panadapter 0x%08X: %s "
                                "(it will be removed next start)", self.pan_id, exc)
            if ok:
                self.pan_id = None
                self.slice_id = None
                self._forget()
