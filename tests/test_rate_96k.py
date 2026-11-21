"""96 kHz IQ per receiver with MSK144 off (rates.py, Engine.set_sample_rate)."""
from __future__ import annotations

import math
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ws_skim import costas_search as cs
from ws_skim import ft8_engine, rates, receivers
from ws_skim.engine import Engine
from ws_skim.sdrconnect_source import SDRconnectSource
from ws_skim.wsjtx_udp import MSG_DECODE, instance_id, parse

sys.path.insert(0, str(Path(__file__).parent))
from test_sdrconnect_source import FakeSDRconnect, _wait  # noqa: E402

WIDE = rates.WIDE_RATE


# ── the rule ──────────────────────────────────────────────────────────────

def test_rate_is_96k_for_ft8_ft4_on_sdrconnect_and_48k_otherwise():
    def eng(mode, pm=(), src="sdrconnect"):
        return SimpleNamespace(mode=mode, period_modes=pm, source_mode=src)
    assert rates.wanted_rate(eng("FT8", ("FT8",))) == 96_000
    assert rates.wanted_rate(eng("FT8", ("FT8", "FT4"))) == 96_000
    assert rates.wanted_rate(eng("FT4", ("FT4",))) == 96_000
    assert rates.wanted_rate(eng("MSK144")) == 48_000
    for src in ("radio", "wav", "usrp", "airspy", "rtlsdr", "sdrangel", "idle"):
        assert rates.wanted_rate(eng("FT8", ("FT8",), src)) == 48_000, src
    # Receiver 2 asks for its own source family.
    assert rates.wanted_rate(eng("FT8", ("FT8",), "radio"), "sdrconnect") == 96_000


# ── Engine.set_sample_rate ────────────────────────────────────────────────

def _noise(fs, secs, seed=1):
    return cs.synth_noise(fs, int(secs * fs), rng=np.random.default_rng(seed))


def test_set_sample_rate_rebuilds_rate_sized_state_and_keeps_processing():
    e = Engine(center_freq_mhz=7.074, calling_freq_mhz=7.074, sample_rate=48_000)
    e.set_blanker("Bypass")
    e.set_mode("FT8")
    rows, fft0 = e.max_history, e.fft_size
    x = _noise(48_000, 1.0)
    for s in range(0, len(x), 4096):
        e.process_iq_data(x[s:s + 4096], 0, 0)
    assert e._iq_abs_sample == len(x)

    assert e.set_sample_rate(WIDE) is True
    assert e.set_sample_rate(WIDE) is False               # no-op
    assert e.sample_rate == WIDE and e.fft_size == 2 * fft0
    assert e.max_history == rows                           # same row time
    assert e.spectrogram_data.shape == (rows, 2 * fft0)
    assert e._iq_ring.shape[0] == 30 * WIDE and e._iq_abs_sample == 0
    assert e.timebase.sample_rate_hz == WIDE and e._timebase_anchor_pending
    assert len(e._td_mag_buf) == int(0.2 * WIDE)
    assert e.freq_axis[0] == pytest.approx(7.074 - 0.048)
    x = _noise(WIDE, 1.0, 2)
    for s in range(0, len(x), 8192):
        e.process_iq_data(x[s:s + 8192], 0, 0)
    assert e._iq_abs_sample == len(x)
    assert e._ch_buf_end == 0

    # Back to MSK144 at 48 kHz: the hop loop runs on the rebuilt channelizer.
    e.set_mode("MSK144")
    assert e.set_sample_rate(48_000) is True
    assert e.fft_size == fft0 and e._iq_ring.shape[0] == 30 * 48_000
    x = _noise(48_000, 1.0, 3)
    for s in range(0, len(x), 4096):
        e.process_iq_data(x[s:s + 4096], 0, 0)
    assert e._iq_abs_sample == len(x)
    e.stop_background_workers(timeout_s=5)


def test_channelizer_is_skipped_unless_msk144_runs(monkeypatch):
    from ws_skim import processing
    calls = []
    real = processing.apply_channelizer

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(processing, "apply_channelizer", spy)
    e = Engine(center_freq_mhz=7.074, calling_freq_mhz=7.074, sample_rate=WIDE)
    e.set_blanker("Bypass")
    e.set_mode("FT4")
    x = _noise(WIDE, 0.5)
    for s in range(0, len(x), 8192):
        e.process_iq_data(x[s:s + 8192], 0, 0)
    assert not calls
    e.set_mode("MSK144")
    e.set_sample_rate(48_000)
    x = _noise(48_000, 0.5)
    for s in range(0, len(x), 4096):
        e.process_iq_data(x[s:s + 4096], 0, 0)
    assert calls
    e.stop_background_workers(timeout_s=5)


# ── 40 m: FT8 7.074 band with FT4 7.0475 inside 96 kHz ────────────────────

def test_40m_ft4_dial_7_0475_is_inside_96k_but_not_48k():
    assert ft8_engine.pick_dial_mhz("FT4", 7.074, WIDE, None) == pytest.approx(7.0475)
    assert ft8_engine.pick_dial_mhz("FT4", 7.074, 48_000, None) != pytest.approx(7.0475)
    off = (7.0475 - 7.074) * 1e6
    assert ft8_engine.dial_section(off, WIDE, cs.FT4) is not None
    assert ft8_engine.dial_section(off, 48_000, cs.FT4) is None
    # FT8's own dial is still the band setting.
    assert ft8_engine.pick_dial_mhz("FT8", 7.074, WIDE, 7.074) == pytest.approx(7.074)
    # The UDP id follows the existing kHz-in-band rule (7047.5 kHz -> 48).
    assert instance_id("sdrconnect", 7_047_500, "FT4") == "SKIM-RSP1-40m-FT4-48"


def test_engine_at_96k_decodes_ft4_7_0475_next_to_ft8_7_074(tmp_path, monkeypatch):
    from ws_skim import ft8_decode
    from ws_skim.reporting import Reporter
    monkeypatch.setenv("WS_SKIM_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(ft8_decode, "run_jt9",
                        lambda cmd, cwd, timeout_s=0: "000000 -10  0.3 1500 ~  CQ K1JT FN20\n")
    monkeypatch.setattr(ft8_engine.DecodeHistory, "is_warm", lambda *a: False)
    fs = WIDE
    e = Engine(center_freq_mhz=7.074, calling_freq_mhz=7.074, sample_rate=fs)
    e.source_mode = "sdrconnect"
    e._ft8_jt9 = "jt9"
    e.set_blanker("Bypass")
    e.set_period_modes(("FT8", "FT4"))
    spec = cs.FT4
    rng = np.random.default_rng(11)
    n = int(20 * fs)
    x = cs.synth_noise(fs, n, rng=rng)
    chunk = 8192
    e.process_iq_data(x[:chunk], 0, 0)
    tb = e.timebase
    tb.should_mark_period = lambda wall: False
    p0 = math.ceil(tb.utc_at(0) / spec.period_s) * spec.period_s
    s0 = tb.sample_at(p0)
    f_if = (7.0475 - 7.074) * 1e6 + 1500.0                # FT4 dial + 1500 Hz audio
    x = x + cs.synth_signal(spec, fs, n, freq_hz=f_if, dt_s=0.3, snr_db=-8,
                            t0_s=-s0 / fs, rng=rng)
    end = s0 + int((spec.period_s + ft8_engine.TAIL_S + 0.5) * fs)
    assert end <= n
    for s in range(chunk, end, chunk):
        e.process_iq_data(x[s:s + chunk], 0, 0)
        for th in list(e._ft8_threads.values()):    # one period at a time
            th.join(timeout=60)
    r = e._ft8_results.get("FT4")
    assert r is not None and r.mode == "FT4"
    assert r.period_start_utc == pytest.approx(p0)
    assert any(abs(s.base_hz - (7.0475 - 7.074) * 1e6) < 1 for s in r.sections), \
        [s.base_hz for s in r.sections]
    assert any(abs(c.freq_hz - f_if) <= 2 * spec.tone_spacing_hz for c in r.candidates), \
        (r.period_start_utc - p0, [(round(c.freq_hz), round(c.dt_s, 2)) for c in r.candidates][:10])
    assert r.decoded
    recs = []
    while not e._decode_queue.empty():
        recs.append(e._decode_queue.get_nowait())
    ft4 = [x for x in recs if x.get("mode") == "FT4" and x.get("decoded", True)]
    assert ft4
    assert any(7047.5 <= x["radio_khz"] <= 7050.5 for x in ft4), [x["radio_khz"] for x in ft4]

    # Announced on WSJT-X UDP as a 40 m FT4 instance at 7047.5 kHz.
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.bind(("127.0.0.1", 0))
    recv.settimeout(2.0)
    rpt = Reporter()
    rpt.apply_settings("WA1HCO", "FN42", True, "127.0.0.1", recv.getsockname()[1], False)
    rpt.set_context(source="sdrconnect", center_hz=7_074_000, bandwidth_hz=fs)
    rpt.start()
    try:
        for rec in ft4:
            rpt.report_decode(rec)
        ids = set()
        deadline = time.time() + 3.0
        while time.time() < deadline and "SKIM-RSP1-40m-FT4-48" not in ids:
            try:
                msg = parse(recv.recvfrom(4096)[0])
            except socket.timeout:
                break
            if msg and msg["type"] == MSG_DECODE:
                ids.add(msg["id"])
        assert "SKIM-RSP1-40m-FT4-48" in ids, ids
    finally:
        rpt.stop()
        recv.close()
        e.stop_background_workers(timeout_s=10)


# ── SDRconnect switches rate on the fly; the engine follows the packets ───

def test_sdrconnect_switches_to_96k_and_packets_carry_the_rate():
    srv = FakeSDRconnect(center=(7_000_000, 7_000_000), tones=(7_075_000, 7_076_000)).start()
    src = SDRconnectSource(calling_freq_mhz=7.074, port=srv.port, launch="never",
                           control_tuning=False)
    main = SimpleNamespace(sdrconnect_client=src, mode="FT8", period_modes=("FT8",),
                           source_mode="sdrconnect")
    try:
        src.start()
        assert _wait(lambda: src.samples_out > 48_000, 15), src.status_text
        from ws_skim.runtime import _reconcile_rate
        _reconcile_rate(main)
        assert src.wanted_rate == WIDE
        got = {}

        def collect():
            while True:
                try:
                    p = src.sample_queue.get_nowait()
                except Exception:
                    return
                got[p.rate] = got.get(p.rate, 0) + len(p.samples)
        assert _wait(lambda: (collect(), got.get(WIDE, 0) > WIDE)[1], 15), got
        assert src.target_rate == WIDE
        # The tone at +1 kHz is in the 96 kHz output (centred on 7.074).
        e = Engine(center_freq_mhz=7.074, calling_freq_mhz=7.074, sample_rate=48_000)
        e.set_mode("FT8")
        p = src.sample_queue.get(timeout=5)
        assert p.rate == WIDE
        e.set_sample_rate(p.rate)
        e.process_iq_data(np.asarray(p.samples, dtype=np.complex64), 0, 0)
        assert e.sample_rate == WIDE
        # MSK144 back on → 48 kHz again.
        main.mode, main.period_modes = "MSK144", ()
        _reconcile_rate(main)
        got.clear()
        assert _wait(lambda: (collect(), got.get(48_000, 0) > 48_000)[1], 15), got
        e.stop_background_workers(timeout_s=5)
    finally:
        src.stop()
        srv.stop()


def test_tuner2_follows_the_rate_rule_and_its_packets():
    main = SimpleNamespace(source_mode="sdrconnect", mode="FT8", period_modes=("FT8",),
                           blanker=None, sample_rate=48_000)

    class Client:
        target_rate = 48_000
        wanted_rate = 48_000

        def set_target_rate(self, r):
            self.wanted_rate = r
    c = Client()
    rx = receivers.SecondReceiver(main, 10.136, client=c)
    assert c.wanted_rate == WIDE
    rx.feed(np.zeros((4096, 1), np.complex64), 0, 0, rate=WIDE)
    assert rx.engine.sample_rate == WIDE and rx.engine._iq_abs_sample == 4096
    main.mode, main.period_modes = "MSK144", ()
    rx.sync_modes()
    assert c.wanted_rate == 48_000
    rx.engine.stop_background_workers(timeout_s=5)


def test_dc_spur_kept_out_of_the_96k_band_when_tuning_is_allowed():
    src = SDRconnectSource(calling_freq_mhz=7.074, target_rate=WIDE)
    src.device_rate = 2_000_000.0
    src.device_center_hz = 7_046_700.0                     # 27.3 kHz below: inside ±48 kHz
    src.active_device = "RSPduo (X) (Dual)"
    src._update_offset()
    assert "inside the decoded band" in src.edge_note
    sent = []
    src._set = lambda ws, prop, val: sent.append((prop, val))
    src._get = lambda ws, prop: None
    src.control_tuning = True
    src._apply_tuning(None)
    assert sent and sent[0][0] == "device_center_frequency"
    assert abs(7_074_000 - float(sent[0][1])) > WIDE / 2
    # At 48 kHz the same LO was already outside the band: no retune.
    src2 = SDRconnectSource(calling_freq_mhz=7.074, target_rate=48_000)
    src2.device_rate, src2.device_center_hz = 2_000_000.0, 7_046_700.0
    src2.active_device = "RSPduo (X) (Dual)"
    sent.clear()
    src2._set = lambda ws, prop, val: sent.append((prop, val))
    src2._get = lambda ws, prop: None
    src2.control_tuning = True
    src2._apply_tuning(None)
    src2._update_offset()
    assert not sent and "inside the decoded band" not in src2.edge_note


def test_band_activity_waterfall_span_follows_the_rate():
    from ws_skim.band_activity_window import BandWaterfall
    wf = BandWaterfall(span_s=60.0)
    for fs, n in ((48_000, 2048), (WIDE, 4096)):
        for i in range(40):
            wf.push(np.full(n, 1e-3, np.float32), 1000.0 + i * 0.5, 7.074, float(fs))
        snap = wf.snapshot()
        assert snap is not None
        _rows, _t0, _t1, f0, f1 = snap
        assert (f1 - f0) * 1e6 == pytest.approx(fs, rel=0.01)
        assert f0 < 7.074 - fs / 2.2e6 and f1 > 7.074 + fs / 2.2e6
    assert f0 < 7.0475                      # FT4 40 m is on the waterfall at 96 kHz
