#!/usr/bin/env python3
# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the FT8/FT4 Costas search on a WAV file and print what it finds.

Developer tool for checking ws_skim.costas_search on real captures.

    python tools/dev/costas_scan.py capture.wav --mode FT8 [--t0 0.0]
    python tools/dev/costas_scan.py --synthetic --mode FT4

Input: a stereo IQ WAV (left = I, right = Q, the app's IQ WAV format) or
a mono audio WAV (e.g. WSJT-X's 12 kHz saves).  The file should start at
a UTC period boundary; otherwise pass ``--t0`` (seconds of the first
sample relative to the period start, negative for pre-roll).  Only the
first period is searched.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ws_skim import costas_search as cs  # noqa: E402


def load_wav(path: Path) -> tuple[np.ndarray, float]:
    from scipy.io import wavfile

    fs, data = wavfile.read(path)
    data = np.asarray(data)
    if data.dtype.kind in "iu":
        data = data.astype(np.float32) / np.iinfo(data.dtype).max
    if data.ndim == 2 and data.shape[1] >= 2:
        return (data[:, 0] + 1j * data[:, 1]).astype(np.complex64), float(fs)
    return data.reshape(-1).astype(np.float32), float(fs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("wav", nargs="?", type=Path)
    ap.add_argument("--mode", choices=sorted(cs.MODES), default="FT8")
    ap.add_argument("--t0", type=float, default=0.0, help="time of first sample vs period start (s)")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--fmin", type=float, default=None, help="lowest tone-0 frequency (Hz)")
    ap.add_argument("--fmax", type=float, default=None, help="highest tone-0 frequency (Hz)")
    ap.add_argument("--synthetic", action="store_true",
                    help="make a 48 kHz IQ test period with a few signals instead of reading a file")
    ap.add_argument("--max", type=int, default=40, help="candidates to print")
    args = ap.parse_args(argv)
    spec = cs.MODES[args.mode]

    if args.synthetic:
        fs = 48000.0
        rng = np.random.default_rng(1)
        n = int(spec.period_s * fs)
        x = cs.synth_noise(fs, n, rng=rng)
        late = min(1.1, spec.dt_max_s - 0.2)
        truth = [(-12000.0, 0.2, -12.0), (-11940.0, 0.9 if spec is cs.FT8 else 0.5, -18.0),
                 (3000.0, -0.3, -20.0), (15000.0, late, -8.0)]
        for f, dt, snr in truth:
            x = x + cs.synth_signal(spec, fs, n, freq_hz=f, dt_s=dt, snr_db=snr, rng=rng)
        print("truth (freq Hz, dt s, SNR dB):", truth)
    elif args.wav:
        x, fs = load_wav(args.wav)
    else:
        ap.error("give a WAV file or --synthetic")

    n_needed = int(round((spec.period_s - args.t0) * fs))
    x = x[:max(n_needed, 0)] if n_needed > 0 else x
    fr = None
    if args.fmin is not None or args.fmax is not None:
        fr = (args.fmin if args.fmin is not None else -np.inf,
              args.fmax if args.fmax is not None else np.inf)

    t = time.perf_counter()
    cands = cs.find_candidates(x, fs, spec, t0_s=args.t0, threshold=args.threshold,
                               freq_range_hz=fr)
    el = time.perf_counter() - t
    kind = "IQ" if np.iscomplexobj(x) else "audio"
    print(f"{spec.name}: {len(x) / fs:.2f} s of {fs:.0f} Hz {kind}, "
          f"{len(cands)} candidates in {el * 1000:.0f} ms")
    print(f"{'freq Hz':>10} {'dt s':>6} {'score':>6}")
    for c in cands[:args.max]:
        print(f"{c.freq_hz:10.1f} {c.dt_s:6.2f} {c.score:6.2f}")
    secs = cs.group_into_sections(cands, spec)
    print(f"\n{len(secs)} jt9 sections:")
    for s in secs:
        print(f"  base {s.base_hz:9.1f} Hz  -L {s.low_audio_hz:6.0f} -H {s.high_audio_hz:6.0f}"
              f"  ({len(s.candidates)} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
