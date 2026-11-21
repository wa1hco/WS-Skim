# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""SDRplay (SDRconnect) source against a fake SDRconnect WebSocket server.

The fake speaks the SDRconnect WebSocket API 1.0.3 subset WS Skim uses:
get/set_property, iq_stream_enable, selected_device_name,
device_stream_enable, set_primary/secondary_device_enable, and binary IQ
frames (uint16 type 2 = primary, 5 = secondary, then int16 I,Q) paced in
real time, with meter events interleaved like the real program.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import numpy as np
import pytest

pytest.importorskip("websockets")
pytest.importorskip("scipy")

from websockets.exceptions import ConnectionClosed  # noqa: E402
from websockets.sync.server import serve  # noqa: E402

from ws_skim import sdrconnect_source as sc_mod  # noqa: E402
from ws_skim.sdrconnect_source import (  # noqa: E402
    Decimator, PolyphaseResampler, SDRconnectSource, UNREACHABLE_MSG,
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeSDRconnect:
    """Minimal SDRconnect: one tone per tuner, real-time paced IQ."""

    def __init__(self, port=None, rate=2_000_000, center=(14_000_000, 7_000_000),
                 tones=(14_075_500, 7_077_000), amp=0.5, active_device="RSPduo (X) (Dual)",
                 frame=50_000):
        self.port = port or _free_port()
        self.state = {
            "primary":   {"device_sample_rate": str(rate), "device_center_frequency": str(center[0]),
                          "lna_state": "0", "lna_state_max": "6", "started": "true",
                          "active_antenna": "Tuner 1 50 ohm"},
            "secondary": {"device_sample_rate": str(rate), "device_center_frequency": str(center[1]),
                          "lna_state": "0", "lna_state_max": "6", "started": "true",
                          "active_antenna": "Tuner 2 50 ohm"},
        }
        self.common = {"valid_devices": "RSPduo (X) (Single),RSPduo (X) (Dual),IQ File",
                       "active_device": active_device,
                       "valid_antennas": "Tuner 1 50 ohm,HiZ Port"}
        self.tones = {"primary": tones[0], "secondary": tones[1]}
        self.amp = amp
        self.frame = frame
        self.received = []          # every JSON message from the client
        self.connections = 0
        self._conns = []
        self._server = None
        self._thread = None

    # -- server lifecycle --
    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.listen(8)
        kw = sc_mod._accepted_kwargs(serve, dict(sock=sock, compression=None,
                                                 max_size=None, ping_interval=None))
        self._server = serve(self._handler, **kw)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        # Older websockets releases leave open connections up on shutdown().
        for ws in list(self._conns):
            try:
                ws.close()
            except Exception:
                pass
        if self._server is not None:
            self._server.shutdown()
            self._thread.join(5)
            self._server = None

    def sent(self, event_type, prop=None):
        return [m for m in self.received if m.get("event_type") == event_type
                and (prop is None or m.get("property") == prop)]

    # -- protocol --
    def _iq_frame(self, dev, n0, n):
        st = self.state[dev]
        rate = float(st["device_sample_rate"])
        f = self.tones[dev] - float(st["device_center_frequency"])
        t = (n0 + np.arange(n)) / rate
        iq = self.amp * np.exp(2j * np.pi * f * t)
        out = np.empty(2 * n, dtype="<i2")
        out[0::2] = np.round(iq.real * 32767)
        out[1::2] = np.round(iq.imag * 32767)
        ptype = 2 if dev == "primary" else 5
        return ptype.to_bytes(2, "little") + out.tobytes()

    def _handler(self, ws):
        self.connections += 1
        self._conns.append(ws)
        streaming = False
        want = {"primary": True, "secondary": False}
        sent = 0
        t0 = None
        while True:
            try:
                m = ws.recv(timeout=0.002)
            except TimeoutError:
                m = None
            except ConnectionClosed:
                return
            try:
                if m is not None:
                    j = json.loads(m)
                    self.received.append(j)
                    et, prop, val = j.get("event_type"), j.get("property"), j.get("value")
                    dev = j.get("device") or "primary"
                    if et == "get_property":
                        v = self.state[dev].get(prop, self.common.get(prop, ""))
                        ws.send(json.dumps({"event_type": "get_property_response",
                                            "property": prop, "value": v, "device": dev}))
                    elif et == "set_property":
                        self.state[dev][prop] = val
                        ws.send(json.dumps({"event_type": "property_changed",
                                            "property": prop, "value": val, "device": dev}))
                    elif et == "iq_stream_enable":
                        streaming = val == "true"
                        t0 = time.monotonic()
                        sent = 0
                    elif et == "selected_device_name":
                        self.common["active_device"] = val
                    elif et == "set_secondary_device_enable":
                        want["secondary"] = val == "true"
                    elif et == "set_primary_device_enable":
                        want["primary"] = val == "true"
                if streaming:
                    rate = float(self.state["primary"]["device_sample_rate"])
                    due = (time.monotonic() - t0) * rate
                    while sent + self.frame <= due:
                        for dev in ("primary", "secondary"):
                            if want[dev]:
                                ws.send(self._iq_frame(dev, sent, self.frame))
                        ws.send('{"event_type":"property_changed","property":"signal_power",'
                                '"value":"-87.5","device":"primary"}')
                        sent += self.frame
            except ConnectionClosed:
                return


def _collect(src, seconds):
    out = []
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        try:
            out.append(src.sample_queue.get(timeout=0.2).samples.ravel())
        except Exception:
            pass
    return np.concatenate(out) if out else np.zeros(0, np.complex64)


def _wait(cond, timeout=10.0):
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _tone(y, rate=48000):
    n = min(len(y), rate)
    seg = y[-n:] * np.hanning(n)
    spec = np.abs(np.fft.fft(seg))
    f = np.fft.fftfreq(n, 1 / rate)
    k = int(np.argmax(spec))
    return f[k], np.abs(y[-n // 2:]).mean()


# ── DSP ──────────────────────────────────────────────────────────────────────

def test_resampler_blockwise_matches_one_shot():
    rng = np.random.default_rng(1)
    x = (rng.standard_normal(30_000) + 1j * rng.standard_normal(30_000)).astype(np.complex64)
    taps = sc_mod._kaiser_lowpass(400_000 * 6, 21_000, 27_000)
    ref = PolyphaseResampler(6, 25, taps).process(x)
    r = PolyphaseResampler(6, 25, taps)
    parts, i = [], 0
    for n in (1, 7, 100, 2999, 5000, 10_000, 30_000):
        parts.append(r.process(x[i:i + n]))
        i += n
    got = np.concatenate(parts)
    m = min(len(ref), len(got))
    assert m > 7000
    np.testing.assert_allclose(got[:m], ref[:m], atol=1e-4)
    # Integer path too
    taps1 = sc_mod._kaiser_lowpass(2_000_000, 24_000, 176_000)
    ref1 = PolyphaseResampler(1, 10, taps1).process(x)
    r1 = PolyphaseResampler(1, 10, taps1)
    got1 = np.concatenate([r1.process(x[:1234]), r1.process(x[1234:20000]), r1.process(x[20000:])])
    np.testing.assert_allclose(got1, ref1[:len(got1)], atol=1e-4)


@pytest.mark.parametrize("rate", [2_000_000, 2_048_000, 250_000, 192_000, 96_000])
def test_decimator_rate_tone_and_alias_rejection(rate):
    d = Decimator(rate, 48_000)
    n = int(rate * 1.0)
    t = np.arange(n) / rate
    x = (0.5 * np.exp(2j * np.pi * 1500 * t)).astype(np.complex64)
    y = np.concatenate([d.process(x[i:i + 50_000]) for i in range(0, n, 50_000)])
    assert abs(len(y) - 48_000) <= 2
    f, a = _tone(y[-24_000:])
    assert abs(f - 1500) < 3
    assert abs(a - 0.5) < 0.01
    if rate >= 96_000 * 2:
        # A tone at 30 kHz would alias to -18 kHz: must be >= 60 dB down.
        d2 = Decimator(rate, 48_000)
        x2 = (0.5 * np.exp(2j * np.pi * 30_000 * t)).astype(np.complex64)
        y2 = d2.process(x2)[4000:]
        assert 20 * np.log10(np.abs(y2).mean() / 0.5 + 1e-12) < -60


def test_decimator_keeps_up_with_2msps():
    d = Decimator(2_000_000, 48_000)
    m = sc_mod._Mixer(2_000_000, 74_000)
    x = (np.random.default_rng(0).standard_normal(100_008) * 0.01).astype(np.complex64)
    t0 = time.perf_counter()
    for _ in range(20):          # one second of input
        d.process(m.process(x))
    assert time.perf_counter() - t0 < 0.8   # real time with margin on slow CI


# ── Streaming against the fake server ────────────────────────────────────────

def test_primary_stream_scaling_rate_and_tone():
    srv = FakeSDRconnect().start()
    src = SDRconnectSource(calling_freq_mhz=14.074, port=srv.port, launch="never",
                           control_tuning=False)
    try:
        src.start()
        assert _wait(lambda: src.status == "streaming", 10)
        y = _collect(src, 1.5)
        assert len(y) > 30_000
        f, a = _tone(y)
        assert abs(f - 1500) < 3            # 14.0755 MHz → +1500 Hz from 14.074
        assert abs(a - 0.5) < 0.02          # int16 scaled to ±1
        ratio = src.samples_out / src.samples_in
        assert abs(ratio * 2_000_000 - 48_000) < 300
        assert src.offset_hz == pytest.approx(74_000)
        assert src.device_center_hz == 14_000_000
        assert src.lna_state_max == 6
        assert src.resyncs == 0
        # Nothing was retuned: control_tuning is off.
        assert not srv.sent("set_property", "device_center_frequency")
        assert not srv.sent("selected_device_name")
        # The device was already running; WS Skim did not switch it.
        assert srv.common["active_device"].endswith("(Dual)")
    finally:
        src.stop()
        srv.stop()
    assert srv.sent("iq_stream_enable")[-1]["value"] == "false"


def test_secondary_tuner_ignores_type2_and_reads_type5():
    srv = FakeSDRconnect().start()
    src = SDRconnectSource(calling_freq_mhz=7.074, port=srv.port, tuner="secondary",
                           launch="never", control_tuning=False)
    try:
        src.start()
        assert _wait(lambda: src.status == "streaming", 10)
        y = _collect(src, 1.2)
        f, a = _tone(y)
        assert abs(f - 3000) < 3            # secondary tone 7.077 MHz
        assert abs(a - 0.5) < 0.02
        assert srv.sent("set_secondary_device_enable")[0]["value"] == "true"
        gets = srv.sent("get_property", "device_center_frequency")
        assert gets and all(g.get("device") == "secondary" for g in gets)
    finally:
        src.stop()
        srv.stop()


def test_primary_ignores_secondary_frames():
    srv = FakeSDRconnect().start()
    src = SDRconnectSource(calling_freq_mhz=14.074, port=srv.port, launch="never",
                           control_tuning=False)
    srv.tones["secondary"] = srv.tones["primary"] + 1000    # would show at 2500 Hz
    try:
        src.start()
        assert _wait(lambda: src.status == "streaming", 10)
        # Ask the fake to stream the secondary tuner too, on the same socket.
        src._ws.send(json.dumps({"event_type": "set_secondary_device_enable",
                                 "property": "", "value": "true"}))
        time.sleep(0.5)
        y = _collect(src, 1.0)
        assert src.ignored_frames > 0
        f, _a = _tone(y)
        assert abs(f - 1500) < 3
    finally:
        src.stop()
        srv.stop()


def test_ws_skim_tunes_each_tuner_and_follows_band_change():
    srv = FakeSDRconnect(center=(7_000_000, 7_000_000)).start()
    src = SDRconnectSource(calling_freq_mhz=14.074, port=srv.port, tuner="secondary",
                           launch="never", control_tuning=True)
    try:
        src.start()
        assert _wait(lambda: srv.sent("set_property", "device_center_frequency"), 10)
        s = srv.sent("set_property", "device_center_frequency")[0]
        assert s["device"] == "secondary"
        assert int(s["value"]) == 13_974_000          # 100 kHz below 14.074
        assert _wait(lambda: src.device_center_hz == 13_974_000, 5)
        assert src.offset_hz == pytest.approx(100_000)
        src.retune(50.313)
        assert _wait(lambda: len(srv.sent("set_property", "device_center_frequency")) >= 2, 5)
        s2 = srv.sent("set_property", "device_center_frequency")[-1]
        assert int(s2["value"]) == 50_213_000 and s2["device"] == "secondary"
        # A move inside the span needs no retune: only the mixer moves.
        n = len(srv.sent("set_property", "device_center_frequency"))
        src.retune(50.260)
        assert _wait(lambda: src.offset_hz == pytest.approx(47_000), 5)
        assert len(srv.sent("set_property", "device_center_frequency")) == n
    finally:
        src.stop()
        srv.stop()


def test_out_of_span_is_reported_when_not_tuning():
    srv = FakeSDRconnect(center=(7_000_000, 7_000_000)).start()
    src = SDRconnectSource(calling_freq_mhz=14.074, port=srv.port, launch="never",
                           control_tuning=False)
    try:
        src.start()
        assert _wait(lambda: src.status == "out_of_span", 10)
        assert "outside" in src.status_text
    finally:
        src.stop()
        srv.stop()


def test_fresh_sdrconnect_gets_device_selected_and_started():
    srv = FakeSDRconnect(active_device="").start()
    src = SDRconnectSource(calling_freq_mhz=14.074, port=srv.port, launch="never",
                           control_tuning=False, device_mode="single")
    try:
        src.start()
        assert _wait(lambda: srv.sent("device_stream_enable"), 10)
        assert srv.sent("selected_device_name")[0]["value"] == "RSPduo (X) (Single)"
        assert srv.sent("device_stream_enable")[0]["value"] == "true"
    finally:
        src.stop()
        srv.stop()


def test_reconnects_after_server_restart():
    port = _free_port()
    srv = FakeSDRconnect(port=port).start()
    src = SDRconnectSource(calling_freq_mhz=14.074, port=port, launch="never",
                           control_tuning=False)
    try:
        src.start()
        assert _wait(lambda: src.status == "streaming", 10)
        srv.stop()
        assert _wait(lambda: src.status == "unreachable", 10)
        assert src.status_text == UNREACHABLE_MSG
        srv2 = FakeSDRconnect(port=port).start()
        try:
            n0 = src.samples_out
            assert _wait(lambda: src.status == "streaming" and src.samples_out > n0 + 10_000, 15)
            assert src.reconnects >= 1
        finally:
            src.stop()
            srv2.stop()
    finally:
        src.stop()
        srv.stop()


def test_stop_is_quick_with_no_server():
    port = _free_port()
    src = SDRconnectSource(port=port, launch="never")
    src.start()
    assert _wait(lambda: src.status == "unreachable", 5)
    assert src.status_text == UNREACHABLE_MSG
    t0 = time.monotonic()
    src.stop()
    assert time.monotonic() - t0 < 1.5
    assert src._thread is None


# ── Launching SDRconnect ─────────────────────────────────────────────────────

def test_launch_headless_uses_websocket_port(monkeypatch, tmp_path):
    exe = tmp_path / "SDRconnect_headless"
    exe.write_text("")
    calls = []

    class _P:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kw):
        calls.append((args, kw))
        return _P()

    monkeypatch.setattr(sc_mod, "find_sdrconnect", lambda headless=True: str(exe))
    monkeypatch.setattr(sc_mod.subprocess, "Popen", fake_popen)
    port = _free_port()
    src = SDRconnectSource(port=port, launch="headless", launch_wait_s=0.5)
    src.start()
    assert _wait(lambda: calls, 5)
    args, kw = calls[0]
    assert args == [str(exe), f"--websocket_port={port}"]
    assert kw.get("start_new_session") or kw.get("creationflags")
    assert _wait(lambda: src.status == "unreachable", 5)
    src.stop()
    assert "terminate" in calls          # the headless copy WS Skim started is closed
    assert len([c for c in calls if isinstance(c, tuple)]) == 1   # launched once only


def test_never_mode_and_missing_program_do_not_launch(monkeypatch):
    launched = []
    monkeypatch.setattr(sc_mod, "launch_sdrconnect", lambda *a: launched.append(a))
    src = SDRconnectSource(port=_free_port(), launch="never")
    src.start()
    assert _wait(lambda: src.status == "unreachable", 5)
    src.stop()
    assert not launched
    monkeypatch.setattr(sc_mod, "find_sdrconnect", lambda headless=True: None)
    assert sc_mod.launch_sdrconnect("headless", 5454) is None


def test_running_sdrconnect_is_used_not_relaunched(monkeypatch):
    srv = FakeSDRconnect().start()
    launched = []
    monkeypatch.setattr(sc_mod, "launch_sdrconnect", lambda *a: launched.append(a))
    src = SDRconnectSource(calling_freq_mhz=14.074, port=srv.port, launch="headless",
                           control_tuning=False)
    try:
        src.start()
        assert _wait(lambda: src.status == "streaming", 10)
        assert not launched
    finally:
        src.stop()
        srv.stop()
