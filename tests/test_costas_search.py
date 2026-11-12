# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""FT8 / FT4 wideband Costas search on synthetic signals.

Categories (CLAUDE.md): noise-only, strong, marginal SNR, timing offset,
frequency offset, edge cases.  SNR is in the WSJT-X 2.5 kHz reference
bandwidth.  Fixed seeds keep every case deterministic.
"""

from __future__ import annotations

import numpy as np
import pytest

from map144_app import costas_search as cs

FS_IQ = 48000.0
FS_AUDIO = 12000.0


def _period(spec, fs, signals=(), *, complex_out=True, seed=0, t0_s=0.0, seconds=None):
    """Noise plus signals [(freq_hz, dt_s, snr_db), ...] for one period."""
    rng = np.random.default_rng(seed)
    n = int(round((seconds or spec.period_s) * fs))
    x = cs.synth_noise(fs, n, complex_out=complex_out, rng=rng)
    for f, dt, snr in signals:
        x = x + cs.synth_signal(spec, fs, n, freq_hz=f, dt_s=dt, snr_db=snr,
                                t0_s=t0_s, complex_out=complex_out, rng=rng)
    return x


def _match(cands, spec, f, dt):
    """The candidate for a signal at (f, dt), within a tone and half a symbol."""
    for c in cands:
        if abs(c.freq_hz - f) <= spec.tone_spacing_hz and abs(c.dt_s - dt) <= spec.symbol_s / 2:
            return c
    return None


# ── mode tables ──────────────────────────────────────────────────────────

def test_mode_tables():
    assert cs.FT8.tone_spacing_hz == pytest.approx(6.25)
    assert cs.FT8.bandwidth_hz == pytest.approx(50.0)
    assert len(cs.FT8.sync_symbols()) == 21
    assert cs.FT8.duration_s == pytest.approx(12.64)
    assert cs.FT4.tone_spacing_hz == pytest.approx(20.8333, rel=1e-4)
    assert len(cs.FT4.sync_symbols()) == 16
    for spec in (cs.FT8, cs.FT4):
        for seq in spec.costas:            # Costas: no tone repeats in an array
            assert len(set(seq)) == len(seq)
            assert all(0 <= t < spec.n_tones for t in seq)
        assert spec.nominal_start_s + spec.duration_s < spec.period_s


def test_spectrogram_grid_and_sign_convention():
    fs, f = FS_IQ, -1234.0
    n = int(1.0 * fs)
    x = np.exp(2j * np.pi * f * np.arange(n) / fs).astype(np.complex64)
    sg = cs.spectrogram(x, fs, cs.FT8)
    assert sg.bin_hz == pytest.approx(cs.FT8.tone_spacing_hz / cs.FREQ_OSR)
    assert sg.hop_s == pytest.approx(cs.FT8.symbol_s / cs.TIME_OSR)
    peak = sg.freqs_hz[np.argmax(sg.power.mean(axis=0))]
    assert peak == pytest.approx(f, abs=sg.bin_hz)   # negative stays negative


def test_synth_snr_is_in_2500_hz():
    """Signal power / (noise PSD * 2500 Hz) equals the requested SNR."""
    for complex_out in (True, False):
        fs = FS_IQ if complex_out else FS_AUDIO
        n = int(15 * fs)
        sig = cs.synth_signal(cs.FT8, fs, n, freq_hz=1000.0, snr_db=-10.0,
                              complex_out=complex_out, rng=np.random.default_rng(1))
        on = sig[sig != 0]
        p_sig = np.mean(np.abs(on) ** 2)
        noise_psd = 1.0 / (fs if complex_out else fs / 2)   # unit-variance noise
        snr = 10 * np.log10(p_sig / (noise_psd * 2500.0))
        assert snr == pytest.approx(-10.0, abs=0.05)


# ── noise only ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec,max_false", [(cs.FT8, 10), (cs.FT4, 60)])
def test_noise_only_few_candidates(spec, max_false):
    x = _period(spec, FS_IQ, seed=11)
    cands = cs.find_candidates(x, FS_IQ, spec)
    assert len(cands) <= max_false
    assert all(c.score < 2.5 for c in cands)


# ── strong and marginal signals ─────────────────────────────────────────

@pytest.mark.parametrize("spec,snr", [(cs.FT8, -10), (cs.FT8, -20), (cs.FT4, -10), (cs.FT4, -14)])
def test_finds_signal(spec, snr):
    f, dt = 7321.0, 0.3
    x = _period(spec, FS_IQ, [(f, dt, snr)], seed=3)
    cands = cs.find_candidates(x, FS_IQ, spec)
    c = _match(cands, spec, f, dt)
    assert c is not None, f"{spec.name} at {snr} dB not found; top: {cands[:3]}"
    assert c.mode == spec.name
    if snr == -10:
        assert cands[0] is c            # strong signal ranks first
        assert c.score > 3.0


def test_score_rises_with_snr():
    scores = []
    for snr in (-22, -18, -14, -10):
        x = _period(cs.FT8, FS_IQ, [(2000.0, 0.0, snr)], seed=5)
        c = _match(cs.find_candidates(x, FS_IQ, cs.FT8, threshold=1.0), cs.FT8, 2000.0, 0.0)
        scores.append(c.score)
    assert scores == sorted(scores)


# ── timing offsets ───────────────────────────────────────────────────────

@pytest.mark.parametrize("dt", [-0.9, -0.4, 0.0, 1.2, 2.2])
def test_ft8_timing_offsets(dt):
    """Including dt < -0.5: the first Costas array starts before the data."""
    f = -3500.0
    x = _period(cs.FT8, FS_IQ, [(f, dt, -16)], seed=7)
    c = _match(cs.find_candidates(x, FS_IQ, cs.FT8), cs.FT8, f, dt)
    assert c is not None
    assert c.dt_s == pytest.approx(dt, abs=cs.FT8.symbol_s / cs.TIME_OSR)


def test_preroll_t0_is_honoured():
    """Buffer starting 1 s before the period: DT is still period-relative."""
    f, dt = 1500.0, 0.25
    x = _period(cs.FT8, FS_IQ, [(f, dt, -14)], seed=8, t0_s=-1.0, seconds=16.0)
    c = _match(cs.find_candidates(x, FS_IQ, cs.FT8, t0_s=-1.0), cs.FT8, f, dt)
    assert c is not None


# ── frequency offsets and input types ───────────────────────────────────

@pytest.mark.parametrize("f", [-23000.0, -12.5, 9876.5, 23000.0])
def test_ft8_frequency_across_iq_band(f):
    x = _period(cs.FT8, FS_IQ, [(f, 0.5, -15)], seed=9)
    c = _match(cs.find_candidates(x, FS_IQ, cs.FT8), cs.FT8, f, 0.5)
    assert c is not None
    assert c.freq_hz == pytest.approx(f, abs=cs.FT8.tone_spacing_hz / 2)


@pytest.mark.parametrize("spec", [cs.FT8, cs.FT4])
def test_real_audio_12k(spec):
    """A 12 kHz real audio WAV, as WSJT-X would see it."""
    f, dt = 1234.0, 0.1
    x = _period(spec, FS_AUDIO, [(f, dt, -12)], complex_out=False, seed=10)
    cands = cs.find_candidates(x, FS_AUDIO, spec, freq_range_hz=(100.0, 3000.0))
    assert _match(cands, spec, f, dt) is not None
    assert all(100.0 <= c.freq_hz <= 3000.0 for c in cands)


# ── edge cases ───────────────────────────────────────────────────────────

def test_two_close_signals_both_found():
    """Two FT8 signals 60 Hz apart (just over one bandwidth)."""
    sigs = [(5000.0, 0.0, -14), (5060.0, 1.0, -14)]
    x = _period(cs.FT8, FS_IQ, sigs, seed=12)
    cands = cs.find_candidates(x, FS_IQ, cs.FT8)
    for f, dt, _ in sigs:
        assert _match(cands, cs.FT8, f, dt) is not None


def test_many_signals():
    rng = np.random.default_rng(13)
    freqs = np.sort(rng.choice(np.arange(-20000, 20000, 200.0), size=25, replace=False))
    sigs = [(float(f), float(rng.uniform(-0.3, 1.0)), float(rng.uniform(-18, -8))) for f in freqs]
    x = _period(cs.FT8, FS_IQ, sigs, seed=13)
    cands = cs.find_candidates(x, FS_IQ, cs.FT8)
    found = sum(_match(cands, cs.FT8, f, dt) is not None for f, dt, _ in sigs)
    assert found == len(sigs)


def test_too_short_input_raises():
    with pytest.raises(ValueError):
        cs.find_candidates(np.zeros(int(3 * FS_IQ), np.complex64), FS_IQ, cs.FT8)


def test_bad_sample_rate_raises():
    with pytest.raises(ValueError):
        # 0.048 s * 44100 Hz = 2116.8 samples: not an integer symbol.
        cs.spectrogram(np.zeros(100000, np.complex64), 44100.0, cs.FT4)


def test_all_zero_input_gives_no_candidates():
    assert cs.find_candidates(np.zeros(int(15 * FS_IQ), np.complex64), FS_IQ, cs.FT8) == []


# ── sections for jt9 ─────────────────────────────────────────────────────

def _cand(f, score=3.0):
    return cs.Candidate(sort_key=-score, freq_hz=f, dt_s=0.0, score=score)


def test_sections_pack_within_width():
    freqs = [100.0, 900.0, 2700.0, 3200.0, 9000.0, 9010.0]
    secs = cs.group_into_sections([_cand(f) for f in freqs], cs.FT8,
                                  width_hz=2800.0, audio_floor_hz=300.0)
    assert [len(s.candidates) for s in secs] == [3, 1, 2]
    for s in secs:
        for c in s.candidates:
            audio = c.freq_hz - s.base_hz
            assert 300.0 <= audio and audio + cs.FT8.bandwidth_hz <= 300.0 + 2800.0
        assert s.low_audio_hz <= min(c.freq_hz for c in s.candidates) - s.base_hz
        assert s.high_audio_hz >= max(c.freq_hz for c in s.candidates) - s.base_hz
    assert secs[0].base_hz == pytest.approx(100.0 - 300.0)


def test_sections_from_search_cover_every_signal():
    sigs = [(-15000.0, 0.0, -12), (-14000.0, 0.5, -12), (8000.0, 0.2, -12)]
    x = _period(cs.FT8, FS_IQ, sigs, seed=14)
    cands = [c for c in cs.find_candidates(x, FS_IQ, cs.FT8) if c.score > 3.0]
    secs = cs.group_into_sections(cands, cs.FT8)
    assert len(secs) == 2
    covered = [c.freq_hz for s in secs for c in s.candidates]
    for f, _, _ in sigs:
        assert any(abs(f - g) <= cs.FT8.tone_spacing_hz for g in covered)


def test_section_narrower_than_signal_raises():
    with pytest.raises(ValueError):
        cs.group_into_sections([_cand(0.0)], cs.FT4, width_hz=50.0)
