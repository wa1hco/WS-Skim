"""Two receivers in one WS Skim: RSPduo tuner 1 + tuner 2 (receivers.py)."""
from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ws_skim import costas_search as cs
from ws_skim import ft8_engine, receivers
from ws_skim.sdrconnect_source import SDRconnectSource, SINGLE_MODE_MSG
from ws_skim.wsjtx_udp import MSG_DECODE, parse, source_tag

sys.path.insert(0, str(Path(__file__).parent))
from test_sdrconnect_source import FakeSDRconnect, _wait  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FS = 48000


# ── ids ────────────────────────────────────────────────────────────────────

def test_rsp_tags_carry_the_receiver_number():
    assert source_tag("sdrconnect") == "RSP1"
    assert source_tag("sdrconnect2") == "RSP2"
    assert source_tag("SDRplay") == "RSP1"


def test_default_tuner2_band_is_next_band_up():
    from ws_skim.ui import FT8_BANDS
    assert receivers.default_band(FT8_BANDS, 7.074) == 10.136
    assert receivers.default_band(FT8_BANDS, 432.174) == 1.840


# ── numba: parallel kernels from two receiver threads ─────────────────────

def test_parallel_channelizer_kernels_from_four_threads_match_serial():
    from ws_skim.channelizer import (N_CHANNELS, apply_channelizer,
                                     design_channelizer_filter, make_channelizer_state)
    taps = design_channelizer_filter(FS)
    rng = np.random.default_rng(3)
    blocks = [(rng.standard_normal(8192) + 1j * rng.standard_normal(8192)).astype(np.complex64)
              for _ in range(12)]

    def run():
        st = make_channelizer_state(N_CHANNELS, taps, FS, channel_offset_hz=1500.0)
        return [apply_channelizer(b, st, lp_taps=taps) for b in blocks]

    ref = run()
    out = [None] * 4
    errs = []

    def worker(i):
        try:
            out[i] = run()
        except Exception as exc:                     # pragma: no cover
            errs.append(exc)

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(120)
    assert not errs
    for res in out:
        for a, b in zip(ref, res):
            np.testing.assert_allclose(a, b, rtol=1e-5, atol=1e-6)


# ── two receivers decode two bands → RSP1 / RSP2 WSJT-X ids ───────────────

def _period_iq(e, f_hz, rng):
    """Noise + one FT8 signal at ``f_hz`` (IF) covering one whole UTC period."""
    spec = cs.FT8
    n = int(40 * FS)
    x = cs.synth_noise(FS, n, rng=rng)
    chunk = 8192
    e.process_iq_data(x[:chunk], 0, 0)               # anchors the timebase
    tb = e.timebase
    tb.should_mark_period = lambda wall: False
    p0 = math.ceil(tb.utc_at(0) / spec.period_s) * spec.period_s
    s0 = tb.sample_at(p0)
    x = x + cs.synth_signal(spec, FS, n, freq_hz=f_hz, dt_s=0.3, snr_db=-10,
                            t0_s=-s0 / FS, rng=rng)
    end = s0 + int((spec.period_s + ft8_engine.TAIL_S + 0.5) * FS)
    return x, chunk, end


def _wait_workers(e):
    for th in list(getattr(e, "_ft8_threads", {}).values()):
        th.join(timeout=60)


def test_two_receivers_decode_two_bands_with_rsp1_rsp2_ids(tmp_path, monkeypatch):
    from ws_skim import ft8_decode
    from ws_skim.engine import Engine
    from ws_skim.reporting import Reporter
    monkeypatch.setenv("WS_SKIM_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(ft8_decode, "run_jt9",
                        lambda cmd, cwd, timeout_s=0: "000000 -10  0.3 1500 ~  CQ K1JT FN20\n")
    monkeypatch.setattr(ft8_engine.DecodeHistory, "is_warm", lambda *a: False)

    e1 = Engine(center_freq_mhz=7.074, calling_freq_mhz=7.074, sample_rate=FS)
    e1.source_mode = "sdrconnect"
    e1._ft8_jt9 = "jt9"
    e1.set_blanker("Bypass")
    e1.set_mode("FT8")
    rx = receivers.SecondReceiver(e1, 10.136)
    e2 = rx.engine
    assert (e2.mode, e2.period_modes, e2.blanker.name) == ("FT8", ("FT8",), "Bypass")
    assert e2.calling_freq_mhz == 10.136 and e2.rx_id == 2

    feeds = [(e1, *_period_iq(e1, 3000.0, np.random.default_rng(5))),
             (e2, *_period_iq(e2, -4000.0, np.random.default_rng(6)))]
    errs = []

    def feed(e, x, chunk, end):
        try:
            for s in range(chunk, end, chunk):
                e.process_iq_data(x[s:s + chunk], 0, 0)
            _wait_workers(e)
        except Exception as exc:                     # pragma: no cover
            errs.append(exc)

    ths = [threading.Thread(target=feed, args=f) for f in feeds]
    for t in ths:
        t.start()
    for t in ths:
        t.join(300)
    assert not errs
    assert e1._ft8_result is not None and e1._ft8_result.decoded
    assert e2._ft8_result is not None and e2._ft8_result.decoded
    assert e2._ft8_result.center_freq_mhz == pytest.approx(10.136)

    # decodes.jsonl: both receivers, tagged.
    lines = [json.loads(x) for x in (tmp_path / "decodes.jsonl").read_text().splitlines()]
    assert {x["rx"] for x in lines} == {1, 2}

    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.bind(("127.0.0.1", 0))
    recv.settimeout(2.0)
    rpt = Reporter()
    rpt.apply_settings("WA1HCO", "FN42", True, "127.0.0.1", recv.getsockname()[1], False)
    rpt.set_context(source="sdrconnect", center_hz=7_074_000, bandwidth_hz=FS)
    rpt.start()
    e1.reporter = rpt
    e1.rx2 = rx
    try:
        while not e1._decode_queue.empty():
            rec = e1._decode_queue.get_nowait()
            if rec.get("decoded"):
                rpt.report_decode(rec)
        got = []
        n2 = receivers.drain_decodes(
            e1, lambda rec, cal: (got.append((rec, cal)), rpt.report_decode(rec)))
        assert n2 >= 1
        rec2, cal2 = got[0]
        assert rec2["rx"] == 2 and rec2["source"] == "sdrconnect2" and cal2 == 10.136
        assert 10_100 < rec2["radio_khz"] < 10_160
        ids = set()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                msg = parse(recv.recvfrom(4096)[0])
            except socket.timeout:
                break
            if msg and msg["type"] == MSG_DECODE:
                ids.add(msg["id"])
            if any(i.startswith("SKIM-RSP1-40m-FT8-") for i in ids) and \
                    any(i.startswith("SKIM-RSP2-30m-FT8-") for i in ids):
                break
        assert any(i.startswith("SKIM-RSP1-40m-FT8-") for i in ids), ids
        assert any(i.startswith("SKIM-RSP2-30m-FT8-") for i in ids), ids
        # Receiver 1's context refresh must not close receiver 2's instance.
        rpt.set_context(source="sdrconnect", center_hz=7_074_100, bandwidth_hz=FS)
        assert any(i.startswith("SKIM-RSP2-") for i in rpt.instance_ids())
        receivers.stop(e1)
        assert not any(i.startswith("SKIM-RSP2-") for i in rpt.instance_ids())
        assert any(i.startswith("SKIM-RSP1-") for i in rpt.instance_ids())
    finally:
        rpt.stop()
        recv.close()
        e1.stop_background_workers(timeout_s=10)
        e2.stop_background_workers(timeout_s=10)


# ── SDRconnect dual-tuner setup (fake server) ─────────────────────────────

def _main(**kw):
    d = dict(source_mode="sdrconnect", _rx2_enabled=True, _sdrconnect_started=True,
             running=True, _rx2_band_mhz=10.136, mode="FT8", period_modes=("FT8",),
             blanker=None, sample_rate=FS, reporter=None, rx2=None)
    d.update(kw)
    return SimpleNamespace(**d)


def test_reconcile_starts_retunes_and_stops_tuner2_without_touching_tuner1():
    srv = FakeSDRconnect(center=(7_000_000, 7_000_000), tones=(7_075_000, 10_037_000)).start()
    rx1 = SDRconnectSource(calling_freq_mhz=7.074, port=srv.port, launch="never",
                           control_tuning=False)
    main = _main()

    def factory(m, f):
        return SDRconnectSource(calling_freq_mhz=f, port=srv.port, tuner="secondary",
                                launch="never")
    try:
        rx1.start()
        assert _wait(lambda: rx1.samples_out > FS, 15)
        receivers.reconcile(main, client_factory=factory)
        rx = main.rx2
        assert rx is not None and rx.running
        assert _wait(lambda: rx.engine._iq_abs_sample > FS, 20), rx.client.status_text
        # Tuner 2 was tuned (device field "secondary"); tuner 1 was not.
        tunes = srv.sent("set_property", "device_center_frequency")
        assert tunes and all(m["device"] == "secondary" for m in tunes)
        assert srv.state["primary"]["device_center_frequency"] == "7000000"
        assert abs(float(srv.state["secondary"]["device_center_frequency"]) - 10.036e6) < 1
        # Band change on Tuner 2 → retune tuner B only.
        main._rx2_band_mhz = 14.074
        receivers.reconcile(main, client_factory=factory)
        assert rx.engine.calling_freq_mhz == 14.074
        assert _wait(lambda: abs(float(srv.state["secondary"]["device_center_frequency"])
                                 - 13.974e6) < 1, 10)
        assert srv.state["primary"]["device_center_frequency"] == "7000000"
        # Mode follows Tuner 1's Mode menu.
        main.mode, main.period_modes = "FT4", ("FT4",)
        receivers.reconcile(main, client_factory=factory)
        assert rx.engine.period_modes == ("FT4",)
        # Untick Tuner 2: receiver 2 stops, tuner 1 keeps streaming.
        main._rx2_enabled = False
        receivers.reconcile(main, client_factory=factory)
        assert main.rx2 is None and not rx.running
        n = rx1.samples_out
        assert _wait(lambda: rx1.samples_out > n + FS // 2, 10)
        assert rx1.status == "streaming"
    finally:
        if main.rx2 is not None:
            receivers.stop(main)
        rx1.stop()
        srv.stop()


def test_dual_mode_uses_the_flat_part_of_the_low_if_span():
    src = SDRconnectSource(calling_freq_mhz=7.074)
    src.device_rate = 2_000_000.0
    src.active_device = "RSPduo (X) (Dual)"
    assert src.dual_mode and src.usable_half_span_hz() == pytest.approx(670_000.0)
    src.active_device = "RSPduo (X) (Single)"
    assert not src.dual_mode and src.usable_half_span_hz() == pytest.approx(970_000.0)


def test_tuner2_reports_single_tuner_mode():
    srv = FakeSDRconnect(active_device="RSPduo (X) (Single)").start()
    src = SDRconnectSource(calling_freq_mhz=7.074, port=srv.port, tuner="secondary",
                           launch="never")
    try:
        src.start()
        assert _wait(lambda: src.status == "single_mode", 10), src.status_text
        assert src.status_text == SINGLE_MODE_MSG
    finally:
        src.stop()
        srv.stop()


# ── GUI: the real main window (separate process, own settings) ────────────

_GUI = textwrap.dedent('''
    import os, sys, time, json
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, sys.argv[1])
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication(sys.argv)
    from ws_skim.visualizer import WSSkimVisualizer, _SETTINGS
    from ws_skim import ft8_window, band_activity_window as ba
    # Saved FT8 on 30 m: the startup band must be 30 m, not the FT8 list's
    # first entry (160 m) picked by the MSK144 list's saved index.
    for k, v in {"mode": "FT8", "period_modes": "FT8", "dial_freq_mhz_FT8": 10.136}.items():
        _SETTINGS.setValue(k, v)
    w = WSSkimVisualizer()
    w.show()
    def spin(n=10):
        for _ in range(n):
            app.processEvents(); time.sleep(0.02)
    spin()
    out = {"startup": [w.calling_freq_mhz, w._band_combo.currentText()],
           "hidden_before": not w._rx2_actions[1].isVisible(),
           "label_before": w._band_label.text()}
    ft8_window.apply_modes(w, ("FT8",))
    i = next(i for i in range(w._band_combo.count()) if "7.074" in w._band_combo.itemText(i))
    w._band_combo.setCurrentIndex(i)
    w.source_mode = "sdrconnect"
    w._status_panel_t = 0.0
    w.update_displays(); spin()
    out["shown"] = w._rx2_actions[1].isVisible()
    out["label"] = w._band_label.text()
    out["rx2_band"] = w._rx2_combo.currentText()
    w._rx2_check.setChecked(True)
    j = next(i for i in range(w._rx2_combo.count()) if "14.074" in w._rx2_combo.itemText(i))
    w._rx2_combo.setCurrentIndex(j)
    out["enabled"] = w._rx2_enabled
    out["band_mhz"] = w._rx2_band_mhz
    out["saved"] = [str(_SETTINGS.value("rx2_enabled")), float(_SETTINGS.value("rx2_dial_freq_mhz_FT8"))]
    win = w._band_activity_win
    win.show(); spin()
    ba.tick(w, win); spin()
    out["pane2"] = win._ba_pane2._ba_box.isVisible()
    w._status_panel_t = 0.0
    w.update_displays(); spin()
    out["tuner2_row"] = w._status_widgets["tuner2"][2].text()
    out["tuner2_visible"] = w._status_widgets["tuner2"][2].isVisible()
    w._rx2_check.setChecked(False)
    ba.tick(w, win); spin()
    out["pane2_after"] = win._ba_pane2._ba_box.isVisible()
    w.source_mode = "idle"
    print("RESULT " + json.dumps(out)); sys.stdout.flush()
    os._exit(0)
''')


def test_main_window_tuner2_controls_and_band_activity_pane(tmp_path):
    script = tmp_path / "gui.py"
    script.write_text(_GUI)
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / ".config"),
               QT_QPA_PLATFORM="offscreen", APPDATA=str(tmp_path / "AppData"))
    p = subprocess.run([sys.executable, str(script), str(REPO)], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=180)
    line = next((x for x in p.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line is not None, p.stdout[-2000:] + p.stderr[-2000:]
    r = json.loads(line[7:])
    assert r["startup"][0] == 10.136 and "10.136" in r["startup"][1]
    assert r["hidden_before"] and r["label_before"].strip() == "Band:"
    assert r["shown"] and r["label"].strip() == "Band (Tuner 1):"
    assert "10.136" in r["rx2_band"]                  # next band up from 7.074
    assert r["enabled"] and r["band_mhz"] == 14.074
    assert r["saved"][0].lower() == "true" and r["saved"][1] == 14.074
    assert r["pane2"] and not r["pane2_after"]
    assert r["tuner2_visible"] and "14.074" in r["tuner2_row"]


def test_dual_mode_edge_streams_with_a_note_and_retunes_when_allowed():
    src = SDRconnectSource(calling_freq_mhz=10.136, control_tuning=False)
    src.device_rate = 2_000_000.0
    src.device_center_hz = 11_000_000.0
    src.active_device = "RSPduo (X) (Dual)"
    src._update_offset()
    assert src.status != "out_of_span" and "flat" in src.edge_note
    sent = []
    src._set = lambda ws, prop, value: sent.append((prop, value))
    src._get = lambda ws, prop: None
    src._apply_tuning(None)
    assert sent == []                                 # WS Skim does not tune: leave it
    src.control_tuning = True
    src._apply_tuning(None)
    assert sent == [("device_center_frequency", 10_036_000)]
    src.calling_freq_mhz = 14.074
    src._update_offset()
    assert src.status == "out_of_span"
