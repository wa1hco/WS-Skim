"""Receiver 2: the RSPduo's second tuner as a second decoder in one WS Skim.

WS Skim's main window *is* receiver 1 (``WSSkimVisualizer`` is an
:class:`~ws_skim.engine.Engine`).  When the source is SDRplay (SDRconnect)
and **Tuner 2** is ticked in the toolbar, :class:`SecondReceiver` runs a
second, bare ``Engine`` fed by its own ``SDRconnectSource`` (tuner B) on
its own drain thread.  It decodes its own band with the same modes as
receiver 1 (Mode menu), and its decodes join the same Roster, Band
Activity (its own pane), WSJT-X UDP (``SKIM-RSP2-…`` ids), PSKReporter
and DX cluster.

Threads: the GUI only sets ``main._rx2_enabled`` / ``main._rx2_band_mhz``
(toolbar), and the radio thread calls :func:`reconcile` to start, stop,
retune and mode-sync receiver 2.  The GUI drain loop calls
:func:`drain_decodes` for its decode queue.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np

from . import rates

log = logging.getLogger(__name__)

#: Receiver 2's source family (for the 48/96 kHz rate rule in rates.py).
RX2_SOURCE_MODE = "sdrconnect"

#: QSettings keys (receiver 2 on/off and its band per mode).
ENABLED_KEY = "rx2_enabled"


def band_key(mode: str) -> str:
    return f"rx2_dial_freq_mhz_{mode}"


#: ``source`` of receiver 2's decodes → WSJT-X UDP ids ``SKIM-RSP2-…``.
RX2_SOURCE = "sdrconnect2"
#: Source modes that can run a second receiver.
DUAL_SOURCES = ("sdrconnect",)


class _Hidden:
    """Stand-in for GUI windows a bare Engine checks with ``isVisible()``."""

    def isVisible(self) -> bool:            # noqa: N802 (Qt name)
        return False


_HIDDEN = _Hidden()


def default_band(bands, rx1_freq_mhz) -> float:
    """Tuner 2's first band: the next band up from receiver 1's."""
    freqs = [f for _l, f, _s in bands]
    if not freqs:
        return float(rx1_freq_mhz or 0.0)
    try:
        i = min(range(len(freqs)), key=lambda k: abs(freqs[k] - float(rx1_freq_mhz)))
    except (TypeError, ValueError):
        return freqs[0]
    return freqs[(i + 1) % len(freqs)] if len(freqs) > 1 else freqs[0]


def mode_state(eng) -> tuple:
    """``(mode, period_modes)`` of an engine, for mirroring."""
    return str(getattr(eng, "mode", "MSK144")), tuple(getattr(eng, "period_modes", ()) or ())


def wanted(main) -> bool:
    """Receiver 2 should run: SDRplay source live and Tuner 2 ticked."""
    return (getattr(main, "source_mode", "idle") in DUAL_SOURCES
            and bool(getattr(main, "_rx2_enabled", False))
            and bool(getattr(main, "_sdrconnect_started", False))
            and bool(getattr(main, "running", True)))


class SecondReceiver:
    """A bare Engine + SDRconnect tuner-B client + drain thread."""

    rx_id = 2
    source = RX2_SOURCE

    def __init__(self, main, calling_freq_mhz: float, client=None,
                 sample_rate: int | None = None):
        from .engine import Engine
        self.main = main
        f = float(calling_freq_mhz)
        rate = int(sample_rate or getattr(main, "sample_rate", rates.BASE_RATE))
        eng = Engine(center_freq_mhz=f, calling_freq_mhz=f, sample_rate=rate)
        eng.source_mode = getattr(main, "source_mode", "sdrconnect")
        eng.rx_id = self.rx_id
        eng.rx_source = self.source
        # The diagnostic windows stay on receiver 1: skip their work here.
        eng._fast_graph_win = _HIDDEN
        eng._detect_win = _HIDDEN
        eng._sync_detect_win = _HIDDEN
        if hasattr(main, "_ft8_jt9"):            # tests: a fake jt9
            eng._ft8_jt9 = main._ft8_jt9
        eng._band_wf = getattr(main, "_band_wf2", None)
        self.engine = eng
        self.client = client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0
        self.last_error = ""
        self.sync_modes()

    # ── control (radio thread) ─────────────────────────────────────────
    def sync_modes(self) -> None:
        """Follow receiver 1's Mode menu choice and noise blanker."""
        from .ft8_engine import is_period_mode
        eng = self.engine
        mode, pmodes = mode_state(self.main)
        if mode_state(eng) != (mode, pmodes):
            try:
                if pmodes:
                    eng.set_period_modes(pmodes)
                elif not is_period_mode(mode):
                    eng.set_mode(mode)
            except ValueError as exc:
                log.warning("[rx2] mode %s: %s", mode, exc)
        b1 = getattr(getattr(self.main, "blanker", None), "name", None)
        b2 = getattr(getattr(eng, "blanker", None), "name", None)
        if b1 and b1 != b2:
            eng.set_blanker(b1)
        # 96 kHz with MSK144 off; the engine follows the packets' rate.
        c = self.client
        if c is not None and hasattr(c, "set_target_rate"):
            want = rates.wanted_rate(self.main, RX2_SOURCE_MODE)
            if getattr(c, "wanted_rate", want) != want:
                c.set_target_rate(want)

    @property
    def calling_freq_mhz(self) -> float:
        return float(self.engine.calling_freq_mhz)

    def retune(self, freq_mhz: float) -> None:
        eng = self.engine
        f = float(freq_mhz)
        if abs(f - eng.calling_freq_mhz) < 1e-9:
            return
        eng.calling_freq_mhz = f
        eng.center_freq_mhz = f
        eng._rebuild_channelizer_state()
        if self.client is not None:
            self.client.retune(f)
        log.info("[rx2] band → %.4f MHz", f)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self.client is not None:
            self.client.start()
        self._thread = threading.Thread(target=self._drain, name="rx2-drain", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.client is not None:
            try:
                self.client.stop()
            except Exception:
                pass
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None
        try:
            self.engine.stop_background_workers(timeout_s=timeout)
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── IQ (drain thread) ──────────────────────────────────────────────
    def feed(self, samples, timestamp_int=0, timestamp_frac=0, rate=None) -> None:
        if rate and int(rate) != int(self.engine.sample_rate):
            self.engine.set_sample_rate(int(rate))
        self.engine.process_iq_data(np.asarray(samples, dtype=np.complex64),
                                    timestamp_int, timestamp_frac)

    def _drain(self) -> None:
        q = getattr(self.client, "sample_queue", None)
        if q is None:
            return
        while not self._stop.is_set():
            try:
                pkt = q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.feed(pkt.samples, pkt.timestamp_int, pkt.timestamp_frac,
                          getattr(pkt, "rate", None))
            except Exception as exc:              # keep receiving
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                if self.errors <= 3 or self.errors % 100 == 0:
                    log.exception("[rx2] process error #%d", self.errors)
                time.sleep(0.05)


def make_client(main, freq_mhz: float, cfg: dict | None = None):
    """Tuner B's SDRconnect client (the tuner-A client owns any launch)."""
    from .sdrconnect_source import SDRconnectSource
    if cfg is None:
        from .runtime import _sdrconnect_settings
        cfg = _sdrconnect_settings()
    return SDRconnectSource(
        calling_freq_mhz=freq_mhz,
        target_rate=rates.wanted_rate(main, RX2_SOURCE_MODE),
        host=cfg["host"], port=cfg["port"], tuner="secondary",
        control_tuning=cfg["control_tuning"], launch="never",
        device_mode="dual",
    )


def reconcile(main, client_factory=None) -> None:
    """Start, stop, retune and mode-sync receiver 2 (radio thread)."""
    rx = getattr(main, "rx2", None)
    if not wanted(main):
        if rx is not None:
            stop(main)
        return
    band = getattr(main, "_rx2_band_mhz", None)
    if not band:
        return
    if rx is None:
        factory = client_factory or make_client
        try:
            client = factory(main, float(band))
            rx = SecondReceiver(main, float(band), client=client)
            rx.start()
        except Exception as exc:
            log.exception("[rx2] could not start")
            main._rx2_error = f"Tuner 2 could not start: {exc}"
            return
        main._rx2_error = ""
        main.rx2 = rx
        log.info("[rx2] started on %.4f MHz", float(band))
        return
    rx.sync_modes()
    rx.retune(float(band))


def stop(main) -> None:
    rx = getattr(main, "rx2", None)
    main.rx2 = None
    if rx is not None:
        rx.stop()
        log.info("[rx2] stopped")
    rpt = getattr(main, "reporter", None)
    if rpt is not None and hasattr(rpt, "set_receiver_context"):
        rpt.set_receiver_context(RX2_SOURCE, None, None)


def tag_record(rec: dict, rx) -> dict:
    """Mark a receiver-2 decode (``rx``, ``source``, its calling freq)."""
    rec = dict(rec)
    rec["rx"] = rx.rx_id
    rec["source"] = rx.source
    rec.setdefault("calling_mhz", rx.calling_freq_mhz)
    return rec


def drain_decodes(main, handle) -> int:
    """Hand receiver 2's queued decodes to ``handle(rec, calling_mhz)`` (GUI)."""
    rx = getattr(main, "rx2", None)
    if rx is None:
        return 0
    rpt = getattr(main, "reporter", None)
    if rpt is not None and hasattr(rpt, "set_receiver_context"):
        eng = rx.engine
        rpt.set_receiver_context(RX2_SOURCE, int(round(eng.center_freq_mhz * 1e6)),
                                 int(eng.sample_rate))
    q = rx.engine._decode_queue
    n = 0
    while True:
        try:
            rec = q.get_nowait()
        except queue.Empty:
            break
        if not isinstance(rec, dict) or rec.get("clear"):
            continue
        handle(tag_record(rec, rx), rx.calling_freq_mhz)
        n += 1
    return n
