# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8 / FT4 jt9 decode: section audio, jt9 plumbing, reporting.

The end-to-end test needs WSJT-X (jt9 and ft8sim) and is skipped without it.
SNR is in the WSJT-X 2.5 kHz reference bandwidth.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from ws_skim import costas_search as cs
from ws_skim import ft8_decode as fd
from ws_skim import ft8_engine

FS = 48000.0


def _tone_power(audio, f, fs=fd.AUDIO_FS):
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / fs)
    return spec[np.abs(freqs - f) < 3].sum()


# ── section audio ──────────────────────────────────────────────────────

def test_section_audio_maps_rf_to_audio_and_rejects_other_sideband():
    t0 = -2.0
    n = int(17 * FS)
    t = np.arange(n) / FS
    base = -10000.0
    usb = np.exp(2j * np.pi * (base + 1000.0) * t)        # audio 1000 Hz
    lsb = np.exp(2j * np.pi * (base - 1700.0) * t)        # would fold to 1700 Hz
    audio = fd.section_audio((usb + lsb).astype(np.complex64), FS, base, t0, 15.0)
    assert len(audio) == 15 * fd.AUDIO_FS
    p_usb = _tone_power(audio, 1000.0)
    p_fold = _tone_power(audio, 1700.0)
    assert 10 * np.log10(p_usb / p_fold) > 50


def test_section_audio_starts_at_period_start():
    t0 = -2.0
    n = int(17 * FS)
    x = np.zeros(n, np.complex64)
    i0 = int(-t0 * FS) + int(1.0 * FS)                     # burst 1.0 s into the period
    x[i0:i0 + int(0.5 * FS)] = np.exp(2j * np.pi * 1500.0 * np.arange(int(0.5 * FS)) / FS)
    audio = fd.section_audio(x, FS, 0.0, t0, 15.0)
    env = np.abs(audio)
    on = np.flatnonzero(env > 0.5 * env.max())
    assert on[0] / fd.AUDIO_FS == pytest.approx(1.0, abs=0.03)


def test_section_audio_short_buffer_is_zero_padded():
    x = cs.synth_noise(FS, int(5 * FS), rng=np.random.default_rng(0))
    audio = fd.section_audio(x, FS, 0.0, -2.0, 15.0)
    assert len(audio) == 15 * fd.AUDIO_FS
    assert np.all(audio[int(4 * fd.AUDIO_FS):] == 0)


def test_section_audio_noise_level_is_flat_in_passband():
    x = cs.synth_noise(FS, int(17 * FS), rng=np.random.default_rng(1))
    audio = fd.section_audio(x, FS, 3000.0, -2.0, 15.0)
    spec = np.abs(np.fft.rfft(audio)) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / fd.AUDIO_FS)
    lo = spec[(freqs > 400) & (freqs < 900)].mean()
    hi = spec[(freqs > 2800) & (freqs < 3300)].mean()
    assert abs(10 * np.log10(lo / hi)) < 0.5
    stop = spec[(freqs > 5300) & (freqs < 5900)].mean()
    assert 10 * np.log10(lo / stop) > 40


def test_write_wav_level_and_clip(tmp_path):
    a = np.random.default_rng(2).standard_normal(12000)
    a[100] = 1e6
    p = tmp_path / "x.wav"
    fd.write_wav(p, a)
    with wave.open(str(p)) as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (12000, 2, 1)
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    assert np.abs(pcm).max() == 32767


# ── jt9 plumbing ───────────────────────────────────────────────────────

JT9_OUT = """\
000000  -5  0.3 1200 ~  CQ K1ABC FN42
000000 -21 -0.4  850 ~  W9XYZ K1ABC RR73                      a2
000000 -12  1.1 2010 ~  CQ DX JA1XYZ PM95                     ?
<DecodeFinished>   0   3        0
"""


def test_parse_jt9_strips_flags():
    got = fd.parse_jt9(JT9_OUT)
    assert got == [(-5, 0.3, 1200.0, "CQ K1ABC FN42"),
                   (-21, -0.4, 850.0, "W9XYZ K1ABC RR73"),
                   (-12, 1.1, 2010.0, "CQ DX JA1XYZ PM95")]


def test_jt9_command_modes(tmp_path):
    sec = cs.Section(base_hz=0.0, low_audio_hz=280.0, high_audio_hz=3300.4, candidates=[])
    c8 = fd.jt9_command("jt9", "FT8", sec, tmp_path / "p.wav")
    assert c8[:4] == ["jt9", "-8", "-p", "15"]
    assert c8[c8.index("-L") + 1] == "200" and c8[c8.index("-H") + 1] == "3301"
    c4 = fd.jt9_command("jt9", "FT4", sec, tmp_path / "p.wav")
    assert c4[1:4] == ["-5", "-p", "7.5"]


def test_depth_for_busy_band():
    assert fd.depth_for(1) == 3
    assert fd.depth_for(2 * fd.JT9_WORKERS) == 3
    assert fd.depth_for(2 * fd.JT9_WORKERS + 1) == 2


def test_decode_period_rf_and_dedupe(monkeypatch):
    calls = []

    def fake(cmd, cwd, timeout_s=0):
        calls.append(cmd)
        assert Path(cmd[-1]).is_file()
        return JT9_OUT

    monkeypatch.setattr(fd, "run_jt9", fake)
    secs = [cs.Section(base_hz=-15000.0, low_audio_hz=300, high_audio_hz=400, candidates=[]),
            cs.Section(base_hz=-15000.0 + 0.5, low_audio_hz=300, high_audio_hz=400, candidates=[])]
    x = cs.synth_noise(FS, int(17 * FS), rng=np.random.default_rng(3))
    ds = fd.decode_period(x, FS, "FT8", secs, t0_s=-2.0, period_start_utc=60.0,
                          center_freq_mhz=7.074, jt9="jt9")
    assert len(calls) == 2
    assert len(ds) == 3                                  # same signals twice → kept once
    d = next(d for d in ds if d.message == "CQ K1ABC FN42")
    assert d.rf_hz == pytest.approx(7.074e6 - 15000 + 1200, abs=1)
    assert d.dial_hz == pytest.approx(7.074e6 - 15000, abs=1)
    assert d.snr_db == -5 and d.period_start_utc == 60.0
    assert [d.rf_hz for d in ds] == sorted(d.rf_hz for d in ds)


def test_decode_period_survives_jt9_failure(monkeypatch):
    def boom(cmd, cwd, timeout_s=0):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(fd, "run_jt9", boom)
    sec = cs.Section(base_hz=0.0, low_audio_hz=300, high_audio_hz=400, candidates=[])
    x = cs.synth_noise(FS, int(17 * FS), rng=np.random.default_rng(4))
    assert fd.decode_period(x, FS, "FT8", [sec], t0_s=-2.0, period_start_utc=0.0,
                            center_freq_mhz=7.074, jt9="jt9") == []


# ── engine / reporting plumbing ────────────────────────────────────────

def _decode(msg="CQ K1ABC FN42", snr=-12):
    return fd.Decode(mode="FT8", period_start_utc=1_700_000_010.0, snr_db=snr, dt_s=0.2,
                     audio_hz=1200.0, rf_hz=7_075_200.0, dial_hz=7_074_000.0, message=msg)


def test_publish_decodes_queue_and_jsonl(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("WS_SKIM_OUTPUT_DIR", str(tmp_path))

    class Eng:
        _decode_queue = queue.SimpleQueue()

    ft8_engine.publish_decodes(Eng, [_decode()])
    rec = Eng._decode_queue.get_nowait()
    assert rec["mode"] == "FT8" and rec["outcome"] == "decoded"
    assert rec["radio_khz"] == pytest.approx(7075.2)
    assert rec["utc_time"] == "22:13:30" and rec["jt9_snr"] == -12
    line = json.loads((tmp_path / "decodes.jsonl").read_text().strip())
    assert line["mode"] == "FT8" and line["timestamp"] == "2023-11-14_22:13:30"

    from ws_skim.spot_bus import normalize_map144
    spot = normalize_map144(line, "WA1HCO", "FN42")
    assert spot.mode == "FT8" and spot.tx_call == "K1ABC"
    assert spot.freq_hz == pytest.approx(7_075_200.0)


def test_wsjtx_decode_datagram_carries_mode_and_audio_offset():
    from ws_skim import reporting
    rec = ft8_engine.decode_record(_decode())
    pkt = reporting.build_decode(rec, "WA1HCO", 7_074_000)
    assert b"FT8" in pkt and b"CQ K1ABC FN42" in pkt
    msk = reporting.build_decode({"message": "W1AW WA1HCO FN42", "utc_time": "00:00:15"},
                                 "WA1HCO", 50_260_000)
    assert b"MSK144" in msk
    # delta_frequency sits right before the mode string: u32 then utf8 (u32 len + bytes)
    i = pkt.index(b"FT8") - 8
    assert int.from_bytes(pkt[i:i + 4], "big") == 1200


# ── end to end with real WSJT-X tools ─────────────────────────────────

def _wsjtx_tools():
    from ws_skim.detection import find_jt9
    jt9 = find_jt9()
    if not jt9:
        return None, None
    sim = Path(jt9).with_name("ft8sim" + Path(jt9).suffix)
    return jt9, (str(sim) if sim.is_file() else shutil.which("ft8sim"))


@pytest.mark.skipif(_wsjtx_tools()[1] is None, reason="needs WSJT-X jt9 and ft8sim")
def test_end_to_end_ft8sim_through_iq(tmp_path):
    from scipy.signal import hilbert, resample_poly
    jt9, sim = _wsjtx_tools()
    msgs = [("CQ K1ABC FN42", -15000.0, -12), ("W9XYZ K1ABC -11", 4000.0, -18)]
    t0 = -2.0
    n = int(18 * FS)
    x = cs.synth_noise(FS, n, rng=np.random.default_rng(1)).astype(np.complex128)
    for k, (msg, f_rf, snr) in enumerate(msgs):
        d = tmp_path / f"s{k}"
        d.mkdir()
        subprocess.run([sim, msg, "1200", "0.0", "0", "0", "1", "30"], cwd=d,
                       capture_output=True, check=True)
        with wave.open(str(next(d.glob("*.wav")))) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
        up = resample_poly(hilbert(a), int(FS) // 12000, 1)
        up = up * np.exp(2j * np.pi * (f_rf - 1200.0) * np.arange(len(up)) / FS)
        sig = np.zeros(n, complex)
        i0 = int(-t0 * FS)
        m = min(len(up), n - i0)
        sig[i0:i0 + m] = up[:m]
        rms = np.sqrt(np.mean(np.abs(up[int(0.5 * FS):int(12.5 * FS)]) ** 2))
        x += sig / rms * np.sqrt(2500 / FS * 10 ** (snr / 10))
    x = x.astype(np.complex64)
    r = ft8_engine.search_period(x, FS, "FT8", t0_s=t0, period_start_utc=0.0,
                                 center_freq_mhz=7.074)
    ds = fd.decode_period(x, FS, "FT8", r.sections, t0_s=t0, period_start_utc=0.0,
                          center_freq_mhz=7.074, jt9=jt9)
    got = {d.message: d for d in ds}
    for msg, f_rf, snr in msgs:
        assert msg in got, ds
        assert got[msg].rf_hz == pytest.approx(7.074e6 + f_rf, abs=3)
        assert got[msg].snr_db == pytest.approx(snr, abs=2)
