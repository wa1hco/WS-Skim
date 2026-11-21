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
"""SDRconnectSource: SDRplay radios (RSPduo, RSP1B, ...) through SDRconnect.

WS Skim talks to SDRplay's own SDRconnect program over its WebSocket API
(API 1.0.3, default ``ws://127.0.0.1:5454/``) instead of driving the
hardware.  SDRconnect owns the radio; WS Skim asks it for the raw IQ stream.

Interface
---------
Same ``sample_queue`` / ``start()`` / ``stop()`` / ``retune()`` shape as
``AirspyHFSource``, so ``run_radio_source`` drives it unchanged.  Packets
carry complex64 IQ in ±1.0 scale at ``target_rate`` (48 kHz) **centred on
the calling frequency**, like the SDRangel source: the device is tuned below
the calling frequency (keeps the DC spur away) and the offset is mixed out
here, so ``center_freq_mhz`` == ``calling_freq_mhz`` for the pipeline.

Protocol summary
----------------
Text frames are JSON ``{event_type, property, value, device?}``.  Binary
frames start with a little-endian uint16 payload type: 2 = primary tuner IQ,
5 = secondary tuner IQ, then int16 LE interleaved I,Q at the device rate.
SDRconnect also pushes many ``property_changed`` events (signal_power,
signal_snr, ...) which are skipped without JSON parsing.

Start-up
--------
If nothing listens on a localhost port, SDRconnect is launched (headless by
default, or with its window), the device is selected and streaming is
started through the API.  An SDRconnect WS Skim did not start is never
stopped.  A headless instance WS Skim started is stopped with the source
(it holds the radio, so leaving it would block the SDRconnect window);
an SDRconnect window WS Skim started is left running.

Threading
---------
One daemon worker thread runs the websockets *sync* client with short
``recv`` timeouts, so ``stop()`` returns within about a second and process
shutdown (SIGTERM, window close) is never held up.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Optional

import numpy as np

_TARGET_RATE = 48000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5454

#: Shown in the status bar / source window when the WebSocket can't be reached.
UNREACHABLE_MSG = ("SDRconnect not reachable — start SDRconnect, press Play, "
                   "and turn on WebSocket Server (⋯ menu)")

LAUNCH_MODES = ("headless", "window", "never")

# Binary payload types (SDRconnect WebSocket API 1.0.3)
_PT_IQ = {"primary": 2, "secondary": 5}

# Usable half-span: keep the 48 kHz output (±24 kHz) clear of the band edge.
_EDGE_GUARD_HZ = 30_000.0
# RSPduo dual-tuner mode is Low-IF with a 1.536 MHz IF filter per tuner:
# SDRconnect shows 2 MS/s, but only about ±0.70 MHz of it is flat.
_DUAL_FLAT_HALF_HZ = 700_000.0

#: Shown on tuner 2 while SDRconnect has the RSPduo open in single-tuner mode.
SINGLE_MODE_MSG = ("SDRconnect is in single-tuner mode — choose the "
                   "RSPduo (…) (Dual) entry in SDRconnect for Tuner 2")


def is_dual_device(name: str) -> bool:
    """True for SDRconnect's dual-tuner device entry, ``RSPduo (…) (Dual)``."""
    return str(name or "").strip().lower().endswith("(dual)")
# Minimum distance of the calling frequency from the device LO (DC spur).
_MIN_DC_OFFSET_HZ = 5_000.0


# ---------------------------------------------------------------------------
# Stateful polyphase resampler
# ---------------------------------------------------------------------------

class PolyphaseResampler:
    """Stateful rational resampler (up/down) for complex64 blocks.

    The prototype low-pass ``taps`` runs at ``in_rate * up``.  Output
    sample ``n`` sits at up-sampled position ``n * down``; only the needed
    polyphase branch is evaluated, so the cost is ``len(taps)/up`` MACs per
    output sample.  Block boundaries are seamless (input history is kept).
    """

    def __init__(self, up: int, down: int, taps: np.ndarray):
        self.up = int(up)
        self.down = int(down)
        h = np.asarray(taps, dtype=np.float64)
        h = h * (self.up / h.sum())                 # unity DC gain overall
        T = -(-len(h) // self.up)                   # taps per phase
        h = np.concatenate([h, np.zeros(T * self.up - len(h))])
        # hp[p, m] = h[p + m*up]
        self._hp = h.reshape(T, self.up).T.astype(np.float32).copy()
        self.T = T
        self._m = np.arange(T)
        self._buf = np.zeros(T - 1, dtype=np.complex64)
        self._b0 = -(T - 1)       # global input index of _buf[0]
        self._n = 0               # next global output index
        self.delay_in_samples = (len(taps) - 1) / 2.0 / self.up

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.complex64).ravel()
        buf = np.concatenate([self._buf, x]) if len(self._buf) else x
        last = self._b0 + len(buf) - 1               # last global input index
        n_last = (last * self.up + self.up - 1) // self.down
        if n_last < self._n:
            self._buf = buf
            return np.zeros(0, dtype=np.complex64)
        n = np.arange(self._n, n_last + 1, dtype=np.int64)
        pos = n * self.down
        i0 = pos // self.up
        p = pos % self.up
        if self.up == 1:
            # Integer decimation: strided windows, one dot product each.
            from numpy.lib.stride_tricks import sliding_window_view
            start = int(i0[0] - self._b0) - (self.T - 1)
            W = sliding_window_view(buf, self.T)[start::self.down][:len(n)]
            hr = self._hp[0][::-1].astype(np.complex64)
            y = W @ hr
        else:
            idx = (i0 - self._b0)[:, None] - self._m[None, :]
            X = buf[idx]
            H = self._hp[p]
            y = np.einsum("ij,ij->i", X.real, H) + 1j * np.einsum("ij,ij->i", X.imag, H)
        self._n = int(n_last + 1)
        # Keep only what the next output needs.
        nxt = (self._n * self.down) // self.up - (self.T - 1)
        keep_from = max(0, nxt - self._b0)
        self._buf = buf[keep_from:].copy()
        self._b0 += keep_from
        return y.astype(np.complex64)


def _kaiser_lowpass(fs: float, f_pass: float, f_stop: float, atten_db: float = 75.0):
    from scipy.signal import firwin, kaiserord
    width = max(f_stop - f_pass, 1.0) / (fs / 2.0)
    numtaps, beta = kaiserord(atten_db, width)
    numtaps = max(numtaps | 1, 7)
    return firwin(numtaps, (f_pass + f_stop) / 2.0, fs=fs, window=("kaiser", beta))


class Decimator:
    """Two-stage decimator from ``in_rate`` to ``out_rate`` (48 kHz).

    Stage 1: integer decimation by D1 to >= 4 × out_rate with a loose filter
    (only the ±out_rate/2 core must stay alias-free).  Stage 2: exact
    rational L/M to out_rate with a sharp filter (pass ±21 kHz, stop 27 kHz).
    D1 is the largest that keeps the stage-2 fraction small (L <= 64).
    """

    def __init__(self, in_rate: float, out_rate: int = _TARGET_RATE):
        in_rate = int(round(in_rate))
        out_rate = int(out_rate)
        if in_rate < out_rate:
            raise ValueError(f"device rate {in_rate} Hz is below {out_rate} Hz")
        self.in_rate = in_rate
        self.out_rate = out_rate
        half = out_rate / 2.0
        d_max = max(1, in_rate // (4 * out_rate))
        best = None
        for d in range(d_max, 0, -1):
            # Largest D1 keeps stage 2 short (its taps per phase scale with
            # the stage-1 output rate); the fraction just has to stay small.
            fr = Fraction(out_rate * d, in_rate)
            if fr.numerator <= 64:
                best = (d, fr)
                break
        if best is None:
            raise ValueError(f"can't resample {in_rate} Hz to {out_rate} Hz exactly")
        d1, fr = best
        self.d1 = d1
        self.up, self.down = fr.numerator, fr.denominator
        self.stages = []
        if d1 > 1:
            fs1 = in_rate / d1
            taps1 = _kaiser_lowpass(in_rate, half, fs1 - half, 70.0)
            self.stages.append(PolyphaseResampler(1, d1, taps1))
        else:
            fs1 = float(in_rate)
        self.mid_rate = fs1
        if (self.up, self.down) != (1, 1):
            taps2 = _kaiser_lowpass(fs1 * self.up, 0.875 * half, 1.125 * half, 75.0)
            self.stages.append(PolyphaseResampler(self.up, self.down, taps2))

    def describe(self) -> str:
        s = []
        if self.d1 > 1:
            s.append(f"÷{self.d1}")
        if (self.up, self.down) != (1, 1):
            s.append(f"×{self.up}/{self.down}")
        return " then ".join(s) or "none"

    def process(self, x: np.ndarray) -> np.ndarray:
        for st in self.stages:
            x = st.process(x)
        return x


class _Mixer:
    """Phase-continuous complex mixer: multiplies by exp(-j·2π·f·t)."""

    def __init__(self, rate: float, freq_hz: float):
        self.rate = float(rate)
        self.phase = 0.0
        self.set_freq(freq_hz)

    def set_freq(self, freq_hz: float):
        self.freq_hz = float(freq_hz)
        self.w = -2.0 * np.pi * self.freq_hz / self.rate

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.freq_hz == 0.0:
            return x
        n = len(x)
        ph = self.phase + self.w * np.arange(n, dtype=np.float64)
        self.phase = float((self.phase + self.w * n) % (2.0 * np.pi))
        return x * np.exp(1j * ph).astype(np.complex64)


# ---------------------------------------------------------------------------
# SDRconnect discovery / launch
# ---------------------------------------------------------------------------

def find_sdrconnect(headless: bool = True) -> Optional[str]:
    """Path to SDRconnect (``headless`` or the windowed program), or None."""
    name = "SDRconnect_headless" if headless else "SDRconnect"
    cands = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands.append(Path(base) / "SDRplay" / "SDRconnect" / f"{name}.exe")
    elif sys.platform == "darwin":
        cands.append(Path("/Applications/SDRconnect.app/Contents/MacOS") / name)
    else:
        cands += [Path("/opt/sdrconnect") / name, Path("/usr/local/bin") / name,
                  Path("/usr/bin") / name]
    for c in cands:
        if c.is_file():
            return str(c)
    w = shutil.which(name)
    return w


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _is_local(host: str) -> bool:
    return host.strip().lower() in ("127.0.0.1", "localhost", "::1", "")


def launch_sdrconnect(mode: str, port: int) -> Optional[subprocess.Popen]:
    """Start SDRconnect detached.  Returns the Popen, or None if not found."""
    exe = find_sdrconnect(headless=(mode == "headless"))
    if exe is None:
        return None
    args = [exe]
    if mode == "headless":
        args.append(f"--websocket_port={int(port)}")
    try:
        from .user_paths import config_dir
        log_path = Path(config_dir()) / "sdrconnect.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab")
    except Exception:
        log = subprocess.DEVNULL
    kw = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
              cwd=str(Path(exe).parent))
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        if mode == "headless":
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kw["creationflags"] = flags
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(args, **kw)
    print(f"[sdrconnect] launched {Path(exe).name} (pid {proc.pid})", flush=True)
    return proc


# ---------------------------------------------------------------------------
# Packet + source
# ---------------------------------------------------------------------------

class _SDRconnectPacket:
    __slots__ = ("samples", "timestamp_int", "timestamp_frac")

    def __init__(self, samples, timestamp_int, timestamp_frac):
        self.samples = samples
        self.timestamp_int = timestamp_int
        self.timestamp_frac = timestamp_frac


_WATCHED_PROPS = ("device_center_frequency", "device_sample_rate", "lna_state",
                  "lna_state_max", "started", "active_device", "valid_devices",
                  "can_control", "valid_antennas", "active_antenna")

#: Arrival may only lag the sample clock; a lag beyond this is a real gap.
_RESYNC_LATE_S = 0.5
#: During warm-up the anchor follows the earliest arrival (start-up backlog).
_WARMUP_S = 3.0


class SDRconnectSource:
    """IQ from an SDRplay radio via SDRconnect's WebSocket API.

    ``pkt.samples`` is complex64, ±1.0 scale, shape (n, 1), at
    ``target_rate`` and centred on ``calling_freq_mhz``.  Timestamps use the
    picosecond encoding ``t = timestamp_int + timestamp_frac * 1e-12``.
    """

    def __init__(self, calling_freq_mhz: float = 14.074,
                 target_rate: int = _TARGET_RATE,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 tuner: str = "primary", lna_state: Optional[int] = None,
                 control_tuning: bool = True, sample_rate: Optional[float] = None,
                 launch: str = "headless", device_mode: str = "single",
                 lo_offset_hz: float = 100_000.0, antenna: str = "",
                 open_timeout: float = 2.0, launch_wait_s: float = 20.0):
        self.calling_freq_mhz = float(calling_freq_mhz)
        self.center_freq_mhz = self.calling_freq_mhz   # output centre (pipeline view)
        self.center_freq_mhz_actual = self.calling_freq_mhz
        self.target_rate = int(target_rate)
        self.host = host or DEFAULT_HOST
        self.port = int(port)
        self.tuner = "secondary" if str(tuner).lower().startswith(("s", "b", "2")) else "primary"
        self.lna_state = None if lna_state is None or int(lna_state) < 0 else int(lna_state)
        self.control_tuning = bool(control_tuning)
        self.requested_rate = float(sample_rate) if sample_rate else None
        self.launch = launch if launch in LAUNCH_MODES else "headless"
        self.device_mode = "dual" if str(device_mode).lower().startswith("d") else "single"
        self.lo_offset_hz = float(lo_offset_hz)
        self.antenna = (antenna or "").strip()
        self.open_timeout = float(open_timeout)
        self.launch_wait_s = float(launch_wait_s)

        self.sample_queue: "queue.Queue[_SDRconnectPacket]" = queue.Queue(maxsize=4000)

        # Live state (read by the GUI)
        self.status = "idle"          # idle|launching|connecting|connected|streaming|unreachable|out_of_span|single_mode|error
        self.status_text = "Not started"
        self.device_center_hz: Optional[float] = None
        self.device_rate: Optional[float] = None
        self.active_device = ""
        self.valid_devices: list = []
        self.lna_state_actual: Optional[int] = None
        self.lna_state_max: Optional[int] = None
        self.device_started: Optional[bool] = None
        self.valid_antennas: list = []
        self.active_antenna = ""
        self.offset_hz = 0.0
        self.decimator_desc = "—"
        self.launched_pid: Optional[int] = None

        # Counters
        self.messages = 0
        self.bytes_in = 0
        self.samples_in = 0
        self.samples_out = 0
        self.queue_drops = 0
        self.resyncs = 0
        self.gap_samples = 0
        self.reconnects = 0
        self.ignored_frames = 0
        self._sessions = 0

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ws = None
        self._lock = threading.Lock()
        self._retune_pending = False
        self._mode_switch_pending = False
        self._decim: Optional[Decimator] = None
        self._mixer: Optional[_Mixer] = None
        self._t_anchor: Optional[float] = None
        self._in_since_anchor = 0
        self._out_since_anchor = 0
        self._anchor_wall0 = 0.0
        self._proc: Optional[subprocess.Popen] = None
        self._launch_t = 0.0

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}/"

    def start(self):
        """Start the worker thread.  Never blocks; failures show in status."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._set_status("connecting", f"Connecting to SDRconnect at {self.host}:{self.port}…")
        self._thread = threading.Thread(target=self._run, name="sdrconnect-ws", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        """Stop streaming and the worker.  Returns within ~``timeout`` s."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None
        self._stop_launched()
        self._set_status("idle", "Stopped")

    def set_device_mode(self, mode: str) -> None:
        """Ask for single/dual tuner mode.  Applied at once only to an
        SDRconnect that WS Skim launched itself (never to the user's own
        SDRconnect window, which it only reports on); otherwise when a
        device is next selected."""
        want = "dual" if str(mode).lower().startswith("d") else "single"
        with self._lock:
            if want != self.device_mode:
                self.device_mode = want
                self._mode_switch_pending = True

    def retune(self, calling_freq_mhz: float):
        """Follow a WS Skim band change (applied by the worker thread)."""
        with self._lock:
            self.calling_freq_mhz = float(calling_freq_mhz)
            self.center_freq_mhz = self.calling_freq_mhz
            self.center_freq_mhz_actual = self.calling_freq_mhz
            self._retune_pending = True

    # ── Status helpers ────────────────────────────────────────────────────

    def _set_status(self, status: str, text: str):
        if status != self.status or text != self.status_text:
            self.status = status
            self.status_text = text

    # ── Launch handling ───────────────────────────────────────────────────

    def _maybe_launch(self) -> bool:
        """Launch SDRconnect once if allowed.  True if a process is (now) ours."""
        if self._proc is not None and self._proc.poll() is None:
            return True
        if self.launch == "never" or not _is_local(self.host):
            return False
        if self._proc is not None:      # ours exited — don't loop relaunching
            return False
        try:
            self._proc = launch_sdrconnect(self.launch, self.port)
        except Exception as exc:
            print(f"[sdrconnect] launch failed: {exc}", flush=True)
            self._proc = None
        if self._proc is None:
            return False
        self.launched_pid = self._proc.pid
        self._launch_t = time.monotonic()
        return True

    def _stop_launched(self):
        p = self._proc
        if p is None or self.launch != "headless":
            return
        self._proc = None
        if p.poll() is None:
            try:
                p.terminate()
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    p.kill()
            except Exception:
                pass

    # ── Worker ────────────────────────────────────────────────────────────

    def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            if _is_local(self.host) and not port_open(self.host, self.port):
                ours = self._maybe_launch()
                if ours and time.monotonic() - self._launch_t < self.launch_wait_s:
                    self._set_status("launching", "Starting SDRconnect…")
                    self._stop.wait(0.5)
                    continue
                self._set_status("unreachable", UNREACHABLE_MSG)
                self._stop.wait(backoff)
                backoff = min(backoff * 2.0, 5.0)
                continue
            try:
                self._session()
                backoff = 0.5
            except Exception as exc:  # connection refused, closed, protocol...
                if self._stop.is_set():
                    break
                self._set_status("unreachable", UNREACHABLE_MSG)
                print(f"[sdrconnect] connection: {type(exc).__name__}: {exc}", flush=True)
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2.0, 5.0)

    def _send(self, ws, event_type, prop="", value="", device=None):
        msg = {"event_type": event_type, "property": prop, "value": str(value)}
        if device:
            msg["device"] = device
        ws.send(json.dumps(msg))

    def _get(self, ws, prop):
        self._send(ws, "get_property", prop, "", self.tuner)

    def _set(self, ws, prop, value):
        self._send(ws, "set_property", prop, value, self.tuner)

    def _session(self):
        from websockets.sync.client import connect
        kw = dict(open_timeout=self.open_timeout, close_timeout=1.0, max_size=None,
                  compression=None,
                  ping_interval=None,   # websockets >= 15 only
                  proxy=None)           # >= 15: never route through a system proxy
        with connect(self.uri, **_accepted_kwargs(connect, kw)) as ws:
            self._ws = ws
            if self._sessions:
                self.reconnects += 1
            self._sessions += 1
            try:
                self._set_status("connected", "Connected to SDRconnect")
                self._handshake(ws)
                self._stream(ws)
            finally:
                try:
                    self._send(ws, "iq_stream_enable", "", "false")
                except Exception:
                    pass
                self._ws = None

    def _handshake(self, ws):
        self._decim = None
        self.device_rate = None
        self.device_center_hz = None
        if self.tuner == "secondary":
            # SDRconnect filters the secondary tuner's events and IQ until asked.
            self._send(ws, "set_secondary_device_enable", "", "true")
            # ... and the primary's IQ is not needed on this connection.
            self._send(ws, "set_primary_device_enable", "", "false")
        for p in ("valid_devices", "active_device", "started", "device_sample_rate",
                  "device_center_frequency", "lna_state", "lna_state_max",
                  "valid_antennas", "active_antenna"):
            self._get(ws, p)
        self._pump_until(ws, lambda: self.device_rate is not None
                         and self.device_center_hz is not None, 2.0)

        # Pick a device if SDRconnect has none (fresh headless instance).
        if not self.active_device or self.device_started is False:
            name = "" if self.active_device else self._choose_device()
            if name:
                print(f"[sdrconnect] selecting device {name!r}", flush=True)
                self._send(ws, "selected_device_name", "", name)
                self._pump_until(ws, lambda: False, 1.0)
            self._send(ws, "device_stream_enable", "", "true")
            self._get(ws, "started")
            self._get(ws, "active_device")
            self._pump_until(ws, lambda: False, 0.5)

        if self.lna_state is not None and self.lna_state != self.lna_state_actual:
            self._set(ws, "lna_state", self.lna_state)
        if self.antenna and self.antenna != self.active_antenna:
            self._set(ws, "active_antenna", self.antenna)
            self._get(ws, "active_antenna")
        # Dual-tuner mode has a fixed 2 MS/s per tuner: never set the rate.
        if (self.control_tuning and self.requested_rate and not self.dual_mode
                and self.device_rate and abs(self.device_rate - self.requested_rate) > 1):
            self._set(ws, "device_sample_rate", int(self.requested_rate))
            self._get(ws, "device_sample_rate")
        self._apply_tuning(ws, force=False)
        self._pump_until(ws, lambda: False, 0.3)
        self._ensure_decimator()      # filter design + scipy import before IQ flows
        self._send(ws, "iq_stream_enable", "", "true")

    def _ensure_decimator(self) -> bool:
        if self._decim is not None:
            return True
        if self.device_rate is None or self.device_center_hz is None:
            return False
        try:
            self._decim = Decimator(self.device_rate, self.target_rate)
        except ValueError as exc:
            self._set_status("error", f"SDRconnect sample rate not usable: {exc}")
            return False
        self._mixer = _Mixer(self.device_rate, self.offset_hz)
        self.decimator_desc = self._decim.describe()
        self._update_offset()
        self._t_anchor = None
        return True

    def _switch_device_mode(self, ws) -> None:
        ours = self._proc is not None and self._proc.poll() is None
        if not ours or self.dual_mode == (self.device_mode == "dual"):
            return
        name = self._choose_device()
        if not name or name == self.active_device:
            return
        print(f"[sdrconnect] switching our SDRconnect to {name!r}", flush=True)
        self._send(ws, "selected_device_name", "", name)
        self._pump_until(ws, lambda: False, 1.0)
        self._send(ws, "device_stream_enable", "", "true")
        self._get(ws, "active_device")
        self._get(ws, "device_sample_rate")
        self._get(ws, "device_center_frequency")
        self._decim = None
        self._t_anchor = None
        self._pump_until(ws, lambda: False, 0.5)
        self._retune_pending = True           # re-place the LO for the new mode

    def _choose_device(self) -> str:
        devs = [d for d in self.valid_devices if d and "iq file" not in d.lower()]
        want = "(dual)" if self.device_mode == "dual" else "(single)"
        for d in devs:
            if d.lower().endswith(want):
                return d
        return devs[0] if devs else ""

    @property
    def dual_mode(self) -> bool:
        """SDRconnect has the RSPduo open in dual-tuner mode."""
        return is_dual_device(self.active_device)

    def usable_half_span_hz(self) -> float:
        """How far the calling frequency may sit from the LO (Hz)."""
        if self.device_rate is None:
            return 0.0
        guard = max(_EDGE_GUARD_HZ, self.target_rate / 2.0 + 6_000.0)
        half = self.device_rate / 2.0
        if self.dual_mode:
            half = min(half, _DUAL_FLAT_HALF_HZ)
        return half - guard

    def _apply_tuning(self, ws, force: bool = False):
        """Tune the device LO near the calling frequency when needed."""
        if self.device_rate is None or self.device_center_hz is None:
            return
        call_hz = self.calling_freq_mhz * 1e6
        off = call_hz - self.device_center_hz
        usable = self.usable_half_span_hz()
        need = abs(off) > usable or abs(off) < _MIN_DC_OFFSET_HZ
        if need and self.control_tuning:
            lo_off = min(self.lo_offset_hz, max(usable - 1.0, _MIN_DC_OFFSET_HZ))
            new_c = int(round(call_hz - lo_off))
            print(f"[sdrconnect] tuning {self.tuner} LO to {new_c/1e6:.6f} MHz "
                  f"(calling {self.calling_freq_mhz:.6f})", flush=True)
            self._set(ws, "device_center_frequency", new_c)
            self._get(ws, "device_center_frequency")

    def _update_offset(self):
        if self._single_mode_secondary():
            self._set_status("single_mode", SINGLE_MODE_MSG)
        elif self.status == "single_mode":
            self._set_status("connected", "Connected to SDRconnect")
        if self.device_rate is None or self.device_center_hz is None:
            return
        call_hz = self.calling_freq_mhz * 1e6
        self.offset_hz = call_hz - self.device_center_hz
        usable = self.usable_half_span_hz()
        # Hard limit: the stream itself.  In dual-tuner mode the part past
        # ±0.70 MHz still streams but is in the IF filter's roll-off.
        hard = self.device_rate / 2.0 - max(_EDGE_GUARD_HZ, self.target_rate / 2.0 + 6_000.0)
        self.edge_note = ""
        if self._single_mode_secondary():
            self._set_status("single_mode", SINGLE_MODE_MSG)
        elif abs(self.offset_hz) > hard:
            self._set_status(
                "out_of_span",
                f"{self.calling_freq_mhz:.3f} MHz is outside SDRconnect's span "
                f"({self.device_center_hz/1e6:.3f} MHz ± {self.device_rate/2e6:.3f}) — "
                "tune SDRconnect near it or let WS Skim tune")
        else:
            if abs(self.offset_hz) > usable:
                self.edge_note = (f" — {abs(self.offset_hz)/1e3:.0f} kHz from the tuner centre, "
                                  "past the flat ±700 kHz of dual-tuner mode (weaker signals)")
            if self.status in ("out_of_span", "single_mode"):
                self._set_status("streaming", "Streaming" + self.edge_note)
        if self._mixer is not None:
            self._mixer.set_freq(self.offset_hz)

    def _single_mode_secondary(self) -> bool:
        """Tuner 2 asked for while SDRconnect runs the RSPduo single-tuner."""
        return (self.tuner == "secondary" and bool(self.active_device)
                and "rspduo" in self.active_device.lower() and not self.dual_mode)

    def _on_text(self, m: str):
        # Cheap skip of the high-rate meter events.
        if '"signal_' in m or "spectrum" in m:
            return
        try:
            j = json.loads(m)
        except ValueError:
            return
        prop = j.get("property", "")
        if prop not in _WATCHED_PROPS:
            return
        dev = j.get("device")
        if dev and dev != self.tuner and prop not in ("valid_devices", "active_device"):
            return
        val = str(j.get("value", ""))
        try:
            if prop == "device_center_frequency":
                self.device_center_hz = float(val)
                self._update_offset()
            elif prop == "device_sample_rate":
                r = float(val)
                if r != self.device_rate:
                    self.device_rate = r
                    self._decim = None        # rebuilt on next IQ
                self._update_offset()
            elif prop == "lna_state":
                self.lna_state_actual = int(float(val))
            elif prop == "lna_state_max":
                self.lna_state_max = int(float(val))
            elif prop == "started":
                self.device_started = val.lower() == "true"
            elif prop == "active_device":
                self.active_device = val
                self._update_offset()
            elif prop == "valid_antennas":
                self.valid_antennas = [v.strip() for v in val.split(",") if v.strip()]
            elif prop == "active_antenna":
                self.active_antenna = val
            elif prop == "valid_devices":
                self.valid_devices = [v.strip() for v in val.split(",") if v.strip()]
        except ValueError:
            pass

    def _pump_until(self, ws, cond, timeout):
        t_end = time.monotonic() + timeout
        while not self._stop.is_set() and not cond():
            left = t_end - time.monotonic()
            if left <= 0:
                return
            try:
                m = ws.recv(timeout=min(left, 0.25))
            except TimeoutError:
                continue
            if isinstance(m, str):
                self._on_text(m)

    def _stream(self, ws):
        self._t_anchor = None
        last_iq = time.monotonic()
        while not self._stop.is_set():
            if getattr(self, "_mode_switch_pending", False):
                with self._lock:
                    self._mode_switch_pending = False
                self._switch_device_mode(ws)
            if self._retune_pending:
                with self._lock:
                    self._retune_pending = False
                self._apply_tuning(ws, force=True)
                self._update_offset()
                self._t_anchor = None
            try:
                m = ws.recv(timeout=0.25)
            except TimeoutError:
                if (time.monotonic() - last_iq > 3.0 and self.status in ("connected", "streaming")
                    and not self._single_mode_secondary()):
                    self._set_status("connected",
                                     "Connected, no IQ — press Play (▶) in SDRconnect")
                continue
            if isinstance(m, str):
                self._on_text(m)
                continue
            if self._on_binary(m):
                last_iq = time.monotonic()

    def _on_binary(self, m) -> bool:
        if len(m) < 6:
            return False
        ptype = m[0] | (m[1] << 8)
        if ptype != _PT_IQ[self.tuner]:
            self.ignored_frames += 1
            return False
        now = time.time()
        self.messages += 1
        self.bytes_in += len(m)
        nbytes = (len(m) - 2) & ~3
        raw = np.frombuffer(m, dtype="<i2", count=nbytes // 2, offset=2)
        x = raw.astype(np.float32).view(np.complex64) * np.float32(1.0 / 32768.0)
        n_in = len(x)
        if not self._ensure_decimator():
            return True
        rate = self.device_rate
        # Timestamp: the device sample count is the clock, anchored to the
        # arrival time.  Arrival can only lag the true sample time, so while
        # warming up the anchor follows the earliest arrival seen (soaks up a
        # start-up backlog); after that only a real gap (> 0.5 s late, e.g.
        # samples lost or SDRconnect paused) re-anchors.
        if self._t_anchor is None:
            self._t_anchor = now - n_in / rate
            self._anchor_wall0 = now
            self._in_since_anchor = 0
            self._out_since_anchor = 0
        else:
            expect_end = self._t_anchor + (self._in_since_anchor + n_in) / rate
            drift = now - expect_end
            if drift < 0 and now - self._anchor_wall0 < _WARMUP_S:
                self._t_anchor += drift
            elif drift > _RESYNC_LATE_S or drift < -_RESYNC_LATE_S:
                self.resyncs += 1
                if drift > 0:
                    self.gap_samples += int(drift * rate)
                self._t_anchor = now - n_in / rate
                self._anchor_wall0 = now
                self._in_since_anchor = 0
                self._out_since_anchor = 0
        y = self._decim.process(self._mixer.process(x))
        self.samples_in += n_in
        self._in_since_anchor += n_in
        if len(y) == 0:
            return True
        t_chunk = self._t_anchor + self._out_since_anchor / self.target_rate
        self._out_since_anchor += len(y)
        self.samples_out += len(y)
        ts_int = int(t_chunk)
        ts_frac = int((t_chunk - ts_int) * 1e12)
        try:
            self.sample_queue.put_nowait(_SDRconnectPacket(y.reshape(-1, 1), ts_int, ts_frac))
        except queue.Full:
            self.queue_drops += 1
        if self.status != "out_of_span":
            self._set_status("streaming",
                             f"Streaming {self.active_device or 'SDRplay'}"
                             f" ({self.tuner} tuner){getattr(self, 'edge_note', '')}")
        return True


def _accepted_kwargs(func, kw: dict) -> dict:
    """Drop keyword arguments an older websockets release doesn't know."""
    import inspect
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return kw
    # websockets 13/14 forward unknown **kwargs to socket.create_connection,
    # so filter against the named parameters only.
    return {k: v for k, v in kw.items() if k in params}
