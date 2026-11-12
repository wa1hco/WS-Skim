# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# The sync metric follows WSJT-X lib/ft8/sync8.f90 (Costas tone power over
# all-tone power, normalised by a per-frequency baseline); the FT8/FT4 tone
# and Costas parameters are from WSJT-X lib/ft8/ft8_params.f90 and
# lib/ft4/ft4_params.f90 / genft4.f90.  Copyright the WSJT Development Team
# and the original authors.  GPL-3.0.  See LICENSE and NOTICE.
"""Wideband FT8 / FT4 Costas sync search (plan step 5.1).

Pipeline position
-----------------
Input: one UTC T/R period (15 s FT8, 7.5 s FT4) of samples, either complex
baseband IQ (e.g. 48 kHz from the radio) or real audio (12 kHz).

1. **Power spectrogram** ``S[t, f]`` (:func:`spectrogram`):
   segment length = one symbol (``Tsym * fs`` samples), rectangular window,
   zero-padded to 2 symbols → bin spacing = tone spacing / 2.  Hop = 1/4
   symbol.  ``|X|**2`` (power, magnitude-squared), unnormalised: the sync
   metric is a ratio, so FFT scale cancels.  Complex input uses the full
   FFT, ``fftshift``-ed so bins run from -fs/2 upward; bin frequency sign is
   the baseband sign (a tone ``exp(+j*2*pi*f*t)`` lands at ``+f``).  Real
   input uses ``rfft`` (0 … fs/2).
2. **Costas sync statistic** (:func:`sync_map`): for each candidate tone-0
   frequency bin ``i`` and start frame ``j``, the power on the Costas tones
   ``t = sum S[j + 4*sym, i + 2*tone]`` over all sync symbols, against the
   mean power of the other tones at the same symbols
   ``t0 = (sum_all_tones - t) / (n_tones - 1)``.  ``sync = t / t0``.
   Noncoherent (power) accumulation; no phase is used.
3. **Baseline**: per frequency, the best ``sync`` over start times, divided
   by its 40th percentile across frequency (WSJT-X's "red" spectrum), so a
   score of ~1 is noise and the threshold is in noise-relative units.
4. **Candidates** (:func:`find_candidates`): local maxima across frequency
   (within ± one signal bandwidth) above ``threshold``, strongest first.

Output: :class:`Candidate` (tone-0 frequency, DT relative to the mode's
nominal start, score).  Candidates are hints for a real decoder (jt9); this
module never decodes.  :func:`group_into_sections` packs candidates into
~3 kHz sections, each to be shifted to a 12 kHz WAV for jt9 (plan step 5.2).

A strong signal can also leave weaker candidates about 6 tones either
side, at other DTs, where its data tones happen to line up with the
Costas pattern (WSJT-X sees the same).  They fall in the same jt9
section as the real signal, so they cost nothing downstream.

Not done here (later slices): GFSK-matched tones, coherent per-array sync
for FT4, and wiring into the live engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

# Spectrogram oversampling, as in WSJT-X sync8: 2 bins per tone, 4 hops per
# symbol.  The Costas indexing below depends on these.
FREQ_OSR = 2
TIME_OSR = 4


@dataclass(frozen=True)
class SyncSpec:
    """Tone and sync layout of one WSJT-X FSK mode."""

    name: str
    period_s: float           # T/R period
    symbol_s: float           # symbol duration; tone spacing = 1 / symbol_s
    n_tones: int
    n_symbols: int            # channel symbols (excluding FT4 ramp symbols)
    costas: tuple[tuple[int, ...], ...]   # one tone sequence per sync array
    costas_pos: tuple[int, ...]           # first symbol index of each array
    nominal_start_s: float = 0.5          # tx start after period start (DT = 0)
    dt_min_s: float = -2.5
    dt_max_s: float = 2.5
    # Default candidate threshold (noise-relative score), from synthetic
    # white-noise runs at 48 kHz IQ (20 periods).  FT8 at 1.7: ~2.6 noise
    # candidates per period across ±24 kHz.  Candidates only choose which
    # sections go to jt9, which runs its own search, so false candidates
    # cost CPU, not false decodes.
    threshold: float = 1.7

    @property
    def tone_spacing_hz(self) -> float:
        return 1.0 / self.symbol_s

    @property
    def bandwidth_hz(self) -> float:
        return self.n_tones * self.tone_spacing_hz

    @property
    def duration_s(self) -> float:
        return self.n_symbols * self.symbol_s

    def sync_symbols(self) -> list[tuple[int, int]]:
        """(symbol index, tone) for every Costas symbol."""
        out = []
        for pos, seq in zip(self.costas_pos, self.costas):
            out.extend((pos + k, tone) for k, tone in enumerate(seq))
        return out


# FT8: 79 symbols of 0.16 s, 8 tones at 6.25 Hz; three 7x7 Costas arrays at
# symbols 0, 36, 72.
FT8 = SyncSpec(
    name="FT8", period_s=15.0, symbol_s=0.16, n_tones=8, n_symbols=79,
    costas=((3, 1, 4, 0, 6, 5, 2),) * 3, costas_pos=(0, 36, 72),
)

# FT4: 103 symbols of 0.048 s, 4 tones at 20.833 Hz; four 4-symbol Costas
# arrays at symbols 0, 33, 66, 99 (genft4.f90 itone(1:4), (34:37), (67:70),
# (100:103)).  The ramp-up/down symbols around them carry no sync.
# nominal_start_s = 0.5 is assumed to match jt9's FT4 DT = 0 (speculation:
# verify against jt9 output on a real FT4 capture).
FT4 = SyncSpec(
    name="FT4", period_s=7.5, symbol_s=0.048, n_tones=4, n_symbols=103,
    costas=((0, 1, 3, 2), (1, 0, 2, 3), (2, 3, 1, 0), (3, 2, 0, 1)),
    costas_pos=(0, 33, 66, 99),
    dt_min_s=-1.0, dt_max_s=1.0,
    # FT4 carries 6.4 dB less sync energy than FT8 (16 x 48 ms vs
    # 21 x 160 ms), so its scores run lower at a given SNR.  At 1.5: ~30
    # noise candidates per period across ±24 kHz, and the search still
    # finds only ~70% of -16 dB signals (jt9 decodes FT4 to ~-17.5 dB).
    # Coherent correlation within each 4-symbol array (WSJT-X sync4d)
    # should close that gap; not done yet.
    threshold=1.5,
)

MODES = {"FT8": FT8, "FT4": FT4}


@dataclass(frozen=True)
class Spectrogram:
    power: np.ndarray        # (n_frames, n_bins) float32, |X|^2
    freqs_hz: np.ndarray     # (n_bins,) bin centre frequency
    hop_s: float
    bin_hz: float
    t0_s: float              # time of frame 0 start relative to period start


@dataclass(frozen=True, order=True)
class Candidate:
    sort_key: float = field(repr=False, compare=True)  # -score: strongest first
    freq_hz: float = field(compare=False)  # tone-0 frequency (IQ offset or audio Hz)
    dt_s: float = field(compare=False)     # start time minus nominal start
    score: float = field(compare=False)    # baseline-normalised sync (noise ~ 1)
    mode: str = field(compare=False, default="FT8")


def spectrogram(x: np.ndarray, fs: float, spec: SyncSpec, t0_s: float = 0.0) -> Spectrogram:
    """Power spectrogram on the mode's sync grid (see module docstring).

    ``t0_s`` is the time of ``x[0]`` relative to the UTC period start
    (negative if the caller includes pre-roll).
    """
    x = np.asarray(x)
    nsym = int(round(spec.symbol_s * fs))
    if abs(nsym - spec.symbol_s * fs) > 1e-6 * fs:
        raise ValueError(f"fs={fs} does not give an integer samples-per-symbol for {spec.name}")
    if nsym % TIME_OSR:
        raise ValueError(f"samples per symbol ({nsym}) not divisible by {TIME_OSR}")
    hop = nsym // TIME_OSR
    nfft = FREQ_OSR * nsym
    n_frames = (len(x) - nsym) // hop + 1
    if n_frames < 1:
        raise ValueError("input shorter than one symbol")

    idx = np.arange(nsym)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx]
    if np.iscomplexobj(x):
        X = np.fft.fftshift(np.fft.fft(frames.astype(np.complex64), n=nfft, axis=1), axes=1)
        freqs = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / fs))
    else:
        X = np.fft.rfft(frames.astype(np.float32), n=nfft, axis=1)
        freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)
    power = (X.real ** 2 + X.imag ** 2).astype(np.float32)
    return Spectrogram(power=power, freqs_hz=freqs, hop_s=hop / fs,
                       bin_hz=fs / nfft, t0_s=t0_s)


def sync_map(sg: Spectrogram, spec: SyncSpec, min_sync_fraction: float = 0.6,
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Costas sync ratio for every (start frame, tone-0 bin).

    Returns ``(sync, start_times_s, freqs_hz)`` where ``sync`` has shape
    ``(n_lags, n_freqs)``; ``start_times_s`` is each lag's signal start
    relative to the period start and ``freqs_hz`` each column's tone-0
    frequency.  Start times cover the mode's DT window, as far as at least
    ``min_sync_fraction`` of the sync symbols fall inside the data (FT8:
    two of three Costas arrays; FT4: three of four).
    """
    S = sg.power
    n_frames, n_bins = S.shape
    span = FREQ_OSR * (spec.n_tones - 1)          # bins from tone 0 to top tone
    n_freqs = n_bins - span
    if n_freqs < 1:
        raise ValueError("spectrogram narrower than one signal")

    # All-tone comb sum: C[t, i] = sum_m S[t, i + FREQ_OSR*m].
    C = np.zeros((n_frames, n_freqs), dtype=np.float32)
    for m in range(spec.n_tones):
        C += S[:, FREQ_OSR * m: FREQ_OSR * m + n_freqs]

    sync_syms = spec.sync_symbols()
    last_sym = max(s for s, _ in sync_syms)

    # Lag window: frame j is the signal start, kept inside the DT range.
    # A signal may run off either end of the data (clock error, or a
    # buffer cut at the period edge); like WSJT-X's sync_abc / sync_bc,
    # count only the sync symbols that are inside, but require at least
    # ``min_sync_fraction`` of them so edge noise does not pass as sync.
    j_lo = int(np.ceil((spec.nominal_start_s + spec.dt_min_s - sg.t0_s) / sg.hop_s))
    j_hi = int(np.floor((spec.nominal_start_s + spec.dt_max_s - sg.t0_s) / sg.hop_s))
    sym_frames = np.array([TIME_OSR * s for s, _ in sync_syms])
    lags = np.arange(j_lo, j_hi + 1)
    inside = ((lags[:, None] + sym_frames[None, :] >= 0)
              & (lags[:, None] + sym_frames[None, :] < n_frames)).sum(axis=1)
    ok = inside >= int(np.ceil(min_sync_fraction * len(sync_syms)))
    if not ok.any():
        raise ValueError("not enough data to hold a signal in the DT window")
    lags = lags[ok.argmax(): len(ok) - ok[::-1].argmax()]
    j_lo, n_lags = int(lags[0]), len(lags)

    # Zero-pad in time so every slice is valid; padded frames add nothing
    # to either the Costas or the all-tone sums.
    pad_lo = max(0, -j_lo)
    pad_hi = max(0, j_lo + n_lags + TIME_OSR * last_sym - n_frames)
    if pad_lo or pad_hi:
        S = np.pad(S, ((pad_lo, pad_hi), (0, 0)))
        C = np.pad(C, ((pad_lo, pad_hi), (0, 0)))

    t = np.zeros((n_lags, n_freqs), dtype=np.float32)
    t_all = np.zeros((n_lags, n_freqs), dtype=np.float32)
    for sym, tone in sync_syms:
        r = pad_lo + j_lo + TIME_OSR * sym
        t += S[r:r + n_lags, FREQ_OSR * tone: FREQ_OSR * tone + n_freqs]
        t_all += C[r:r + n_lags]
    t0 = (t_all - t) / (spec.n_tones - 1)
    sync = t / np.maximum(t0, np.finfo(np.float32).tiny)

    start_times = sg.t0_s + (j_lo + np.arange(n_lags)) * sg.hop_s
    return sync, start_times, sg.freqs_hz[:n_freqs]


def find_candidates(
    x: np.ndarray,
    fs: float,
    spec: SyncSpec = FT8,
    *,
    t0_s: float = 0.0,
    threshold: float | None = None,
    max_candidates: int = 300,
    freq_range_hz: tuple[float, float] | None = None,
) -> list[Candidate]:
    """Search one period of samples for FT8/FT4 sync; strongest first.

    ``freq_range_hz`` limits the tone-0 frequencies considered (e.g. to keep
    real audio inside 100–3000 Hz, or to skip the IQ DC artifact region).
    ``threshold`` defaults to ``spec.threshold``.
    """
    if threshold is None:
        threshold = spec.threshold
    sg = spectrogram(x, fs, spec, t0_s)
    sync, start_times, freqs = sync_map(sg, spec)

    best_lag = np.argmax(sync, axis=0)
    red = sync[best_lag, np.arange(sync.shape[1])]

    keep = np.ones_like(freqs, dtype=bool)
    if freq_range_hz is not None:
        keep &= (freqs >= freq_range_hz[0]) & (freqs <= freq_range_hz[1])
    if not keep.any():
        return []
    base = np.percentile(red[keep], 40)
    score = red / max(base, np.finfo(np.float32).tiny)
    score[~keep] = 0.0

    # Local maxima across frequency within ± one signal bandwidth: one
    # candidate per signal, but two signals a bandwidth apart both survive.
    half = max(1, int(round(spec.bandwidth_hz / sg.bin_hz / 2)))
    order = np.argsort(score)[::-1]
    taken = np.zeros_like(keep)
    out: list[Candidate] = []
    for i in order:
        s = float(score[i])
        if s < threshold or len(out) >= max_candidates:
            break
        if taken[max(0, i - half): i + half + 1].any():
            continue
        taken[i] = True
        dt = float(start_times[best_lag[i]] - spec.nominal_start_s)
        out.append(Candidate(sort_key=-s, freq_hz=float(freqs[i]), dt_s=dt,
                             score=s, mode=spec.name))
    return out


# ── Sections for jt9 (plan step 5.2) ────────────────────────────────────────

@dataclass
class Section:
    """A slice of spectrum to shift into one 12 kHz WAV for jt9.

    ``base_hz`` is the frequency (in the search input's frame) that lands at
    0 Hz audio; jt9 is told to look between ``low_audio_hz`` and
    ``high_audio_hz``.  For the WSJT-X UDP Decode, dial = RF of ``base_hz``.
    """

    base_hz: float
    low_audio_hz: float
    high_audio_hz: float
    candidates: list[Candidate]


def group_into_sections(
    candidates: Sequence[Candidate],
    spec: SyncSpec = FT8,
    *,
    width_hz: float = 2800.0,
    audio_floor_hz: float = 300.0,
    margin_hz: float = 20.0,
) -> list[Section]:
    """Greedily pack candidates (by frequency) into sections ≤ ``width_hz``.

    Each section places its lowest candidate at ``audio_floor_hz`` audio and
    holds every candidate whose whole signal fits below
    ``audio_floor_hz + width_hz``.  ``low/high_audio_hz`` bracket the
    candidates' tone-0 frequencies by ``margin_hz`` (jt9 ``-L``/``-H``).
    """
    if width_hz <= spec.bandwidth_hz:
        raise ValueError("section narrower than one signal")
    sections: list[Section] = []
    cur: list[Candidate] = []
    base = 0.0
    for c in sorted(candidates, key=lambda c: c.freq_hz):
        if cur and c.freq_hz + spec.bandwidth_hz - base > audio_floor_hz + width_hz:
            sections.append(_close(base, cur, margin_hz))
            cur = []
        if not cur:
            base = c.freq_hz - audio_floor_hz
        cur.append(c)
    if cur:
        sections.append(_close(base, cur, margin_hz))
    return sections


def _close(base: float, cands: list[Candidate], margin: float) -> Section:
    lo = min(c.freq_hz for c in cands) - base
    hi = max(c.freq_hz for c in cands) - base
    return Section(base_hz=base, low_audio_hz=max(0.0, lo - margin),
                   high_audio_hz=hi + margin, candidates=list(cands))


# ── Synthetic signals for tests and tuning ──────────────────────────────────

def synth_tones(spec: SyncSpec, rng: np.random.Generator) -> np.ndarray:
    """Channel tone sequence: Costas arrays in place, random data elsewhere."""
    tones = rng.integers(0, spec.n_tones, size=spec.n_symbols)
    for sym, tone in spec.sync_symbols():
        tones[sym] = tone
    return tones


def synth_signal(
    spec: SyncSpec,
    fs: float,
    n_samples: int,
    *,
    freq_hz: float,
    dt_s: float = 0.0,
    snr_db: float = 0.0,
    t0_s: float = 0.0,
    complex_out: bool = True,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Continuous-phase FSK test signal (no GFSK smoothing), no noise.

    Amplitude is set for ``snr_db`` in the WSJT-X 2.5 kHz reference
    bandwidth against unit-variance noise from :func:`synth_noise` at the
    same ``fs``.  Signal power P: noise power in 2.5 kHz is
    ``2500 / fs`` for complex unit-variance noise (PSD = 1/fs over fs Hz)
    and ``2500 / (fs/2)`` for real (PSD = 1/(fs/2) over fs/2 Hz).
    """
    rng = rng or np.random.default_rng()
    tones = synth_tones(spec, rng)
    nsym = int(round(spec.symbol_s * fs))
    f_inst = np.repeat(freq_hz + tones * spec.tone_spacing_hz, nsym)
    phase = 2 * np.pi * np.cumsum(f_inst) / fs
    noise_bw_frac = 2500.0 / (fs if complex_out else fs / 2)
    p_sig = noise_bw_frac * 10 ** (snr_db / 10)
    if complex_out:
        burst = np.sqrt(p_sig) * np.exp(1j * phase)
    else:
        burst = np.sqrt(2 * p_sig) * np.cos(phase)
    out = np.zeros(n_samples, dtype=np.complex64 if complex_out else np.float32)
    start = int(round((spec.nominal_start_s + dt_s - t0_s) * fs))
    a, b = max(start, 0), min(start + len(burst), n_samples)
    if b > a:
        out[a:b] = burst[a - start: b - start]
    return out


def synth_noise(fs: float, n_samples: int, *, complex_out: bool = True,
                rng: np.random.Generator | None = None) -> np.ndarray:
    """Unit-variance white Gaussian noise (complex: E|n|^2 = 1)."""
    rng = rng or np.random.default_rng()
    if complex_out:
        n = (rng.standard_normal(n_samples) + 1j * rng.standard_normal(n_samples)) / np.sqrt(2)
        return n.astype(np.complex64)
    return rng.standard_normal(n_samples).astype(np.float32)
