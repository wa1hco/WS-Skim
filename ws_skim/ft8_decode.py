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
"""FT8 / FT4 decode: one jt9 run per Costas-search section.

Signal flow
-----------
Input: one period of complex IQ (the same buffer the Costas search used,
starting ``t0_s`` before the UTC period start) and the search's
:class:`costas_search.Section` list.

For each section, :func:`section_audio` makes the audio an SSB receiver
tuned to dial = ``base_hz`` would give:

1. mix by ``-(base_hz + AUDIO_CENTER_HZ)`` so audio ``AUDIO_CENTER_HZ`` sits
   at 0 Hz (complex, phase-continuous),
2. resample to 12 kHz (``scipy.signal.resample_poly``, complex),
3. complex low-pass ``±AUDIO_HALF_BW_HZ`` (the "SSB filter": keeps audio
   100-4900 Hz, rejects the opposite sideband so nothing folds over),
4. mix back up by ``+AUDIO_CENTER_HZ`` and take the real part (USB audio),
5. keep exactly one period from the UTC period start (zero-padded if the
   buffer is short), as jt9 expects for a WAV file.

Scaling: unit-variance complex noise in → the real audio RMS is set to
``WAV_RMS`` counts before the int16 WAV is written; jt9's SNR is relative,
so the absolute level only has to avoid clipping.  SNR from jt9 is in the
WSJT-X 2.5 kHz reference bandwidth.

jt9 (``-8`` FT8 / ``-5`` FT4) decodes audio ``JT9_LOW_HZ``..``JT9_HIGH_HZ``
(widened to the section's candidates if needed).  Each decode's RF is ``center + base_hz + audio_hz``.
Decodes found by two sections (a signal at an edge) are kept once.

Pure DSP + subprocess; no engine or UI state.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly

from . import costas_search as cs

log = logging.getLogger(__name__)

AUDIO_FS = 12000
#: Centre of the simulated SSB passband (audio Hz).
AUDIO_CENTER_HZ = 2500.0
#: Half width of the passband: audio 100 .. 4900 Hz.
AUDIO_HALF_BW_HZ = 2400.0
#: Real audio RMS in int16 counts (about -30 dBFS; strong signals still fit).
WAV_RMS = 1000.0
JT9_MODE_FLAG = {"FT8": "-8", "FT4": "-5"}
JT9_DEPTH = 3
#: jt9 searches this audio range in every section, not just around the
#: candidates: jt9 fits its SNR baseline over -L..-H, so a narrow range around
#: one signal inflates the baseline and under-reports SNR (seen: -17 for a
#: -12 dB signal).  It also lets jt9 find signals the Costas search missed.
JT9_LOW_HZ = 200
JT9_HIGH_HZ = 3200
JT9_TIMEOUT_S = 12.0
#: Parallel jt9 processes per period.
JT9_WORKERS = 4

_LPF = firwin(255, AUDIO_HALF_BW_HZ / (AUDIO_FS / 2)).astype(np.float64)

# "HHMMSS  SNR   DT  FREQ ~  MESSAGE    [a1 / ?]"
_LINE_RE = re.compile(
    r"^\s*\d{4,6}\s+(?P<snr>-?\d+)\s+(?P<dt>-?\d+(?:\.\d+)?)\s+(?P<af>\d+)\s+\S\s+(?P<msg>.+?)\s*$")


@dataclass(frozen=True)
class Decode:
    mode: str
    period_start_utc: float
    snr_db: int
    dt_s: float
    audio_hz: float          # in the section's audio
    rf_hz: float             # absolute RF of tone 0
    dial_hz: float           # RF of the section's 0 Hz audio (base_hz)
    message: str
    low_confidence: bool = False  # WSJT-X '?': AP quality is poor


def section_audio(iq: np.ndarray, fs: float, base_hz: float, t0_s: float,
                  period_s: float) -> np.ndarray:
    """Real 12 kHz USB audio of one period for dial = ``base_hz`` (IQ frame).

    ``iq`` starts ``t0_s`` seconds relative to the UTC period start (so
    ``t0_s`` is usually negative).  Returns float64, ``period_s * 12000``
    samples, starting at the period start.
    """
    fs_i = int(round(fs))
    if abs(fs - fs_i) > 1e-6:
        raise ValueError(f"sample rate must be a whole number of Hz, got {fs}")
    x = np.asarray(iq, dtype=np.complex128)
    n = np.arange(len(x), dtype=np.float64)
    shift = base_hz + AUDIO_CENTER_HZ
    x = x * np.exp(-2j * np.pi * shift * n / fs)
    g = gcd(AUDIO_FS, fs_i)
    y = resample_poly(x, AUDIO_FS // g, fs_i // g)
    y = lfilter(_LPF, 1.0, y)
    delay = (len(_LPF) - 1) // 2
    y = np.concatenate([y[delay:], np.zeros(delay, y.dtype)])
    m = np.arange(len(y), dtype=np.float64)
    audio = np.real(y * np.exp(2j * np.pi * AUDIO_CENTER_HZ * m / AUDIO_FS)) * np.sqrt(2.0)

    out_n = int(round(period_s * AUDIO_FS))
    start = int(round(-t0_s * AUDIO_FS))
    out = np.zeros(out_n)
    a, b = max(0, start), min(len(audio), start + out_n)
    if b > a:
        out[a - start:b - start] = audio[a:b]
    return out


def write_wav(path: Path, audio: np.ndarray) -> None:
    rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
    scale = WAV_RMS / rms if rms > 0 else 0.0
    pcm = np.clip(np.round(audio * scale), -32767, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_FS)
        w.writeframes(pcm.tobytes())


def parse_jt9(stdout: str) -> list[tuple[int, float, float, str]]:
    """``(snr, dt, audio_hz, message)`` for each decode line of jt9 output."""
    return [(s, d, a, m) for s, d, a, m, _low in parse_jt9_ex(stdout)]


def parse_jt9_ex(stdout: str) -> list[tuple[int, float, float, str, bool]]:
    """Like :func:`parse_jt9`, plus ``low_confidence`` (the WSJT-X ``?`` flag).

    a1..a7 AP annotations are stripped from the message and do not by
    themselves set low_confidence — that matches WSJT-X, which only
    flags a decode when the 37-character message field ends in ``?``.
    """
    out = []
    for line in stdout.splitlines():
        if line.startswith("<") or "EOF" in line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        raw = m["msg"].strip()
        parts = re.split(r"\s{2,}", raw)
        msg = " ".join(parts[0].split())
        flags = " ".join(parts[1:]) if len(parts) > 1 else ""
        # '?' at the end of the 37-char field, or a standalone '?' token.
        low = flags.strip() == "?" or msg.endswith("?") or raw.rstrip().endswith("?")
        out.append((int(m["snr"]), float(m["dt"]), float(m["af"]), msg, low))
    return out


def jt9_command(jt9: str, mode: str, section: cs.Section, wav: Path,
                depth: int = JT9_DEPTH) -> list[str]:
    spec = cs.MODES[mode]
    lo = min(JT9_LOW_HZ, int(np.floor(section.low_audio_hz)))
    hi = max(JT9_HIGH_HZ, int(np.ceil(section.high_audio_hz)))
    return [jt9, JT9_MODE_FLAG[mode], "-p", f"{spec.period_s:g}", "-d", str(depth),
            "-L", str(lo), "-H", str(hi), "-f", str((lo + hi) // 2),
            "-a", str(wav.parent), "-t", str(wav.parent), str(wav)]


def run_jt9(cmd: list[str], cwd: Path, timeout_s: float = JT9_TIMEOUT_S) -> str:
    kwargs = {}
    if os.name == "nt":                                   # no console window per run
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       cwd=str(cwd), **kwargs)
    if r.returncode != 0 and not r.stdout.strip():
        raise RuntimeError(f"jt9 exit {r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout


def decode_period(iq: np.ndarray, fs: float, mode: str, sections, *, t0_s: float,
                  period_start_utc: float, center_freq_mhz: float, jt9: str,
                  depth: int = JT9_DEPTH, workers: int = JT9_WORKERS) -> list[Decode]:
    """Run jt9 on every section of one period; decodes sorted by RF."""
    spec = cs.MODES[mode]
    center_hz = center_freq_mhz * 1e6

    def one(sec) -> list[Decode]:
        with tempfile.TemporaryDirectory(prefix="ws-skim-ft8-") as d:
            wav = Path(d) / "period.wav"
            write_wav(wav, section_audio(iq, fs, sec.base_hz, t0_s, spec.period_s))
            try:
                out = run_jt9(jt9_command(jt9, mode, sec, wav, depth), Path(d))
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as e:
                log.warning("[ft8] jt9 failed on section at %+.0f Hz: %s", sec.base_hz, e)
                return []
        dial = center_hz + sec.base_hz
        return [Decode(mode=mode, period_start_utc=period_start_utc, snr_db=snr, dt_s=dt,
                       audio_hz=af, rf_hz=dial + af, dial_hz=dial, message=msg,
                       low_confidence=low)
                for snr, dt, af, msg, low in parse_jt9_ex(out)]

    sections = list(sections)
    if not sections:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sections)))) as ex:
        found = [d for ds in ex.map(one, sections) for d in ds]
    return dedupe(found, spec.tone_spacing_hz * 2)


def depth_for(n_sections: int, workers: int = JT9_WORKERS) -> int:
    """jt9 depth for a period: 3 (deep, ~2 s per section) while the sections
    fit in about two rounds of ``workers``, else 2 (~0.2 s) so a busy band
    still finishes inside the period.  Depth 2 loses some of the weakest
    decodes (WSJT-X "Normal" vs "Deep")."""
    return JT9_DEPTH if n_sections <= 2 * workers else 2


def dedupe(decodes: list[Decode], freq_tol_hz: float) -> list[Decode]:
    """One decode per (message, RF within ``freq_tol_hz``); keep the best SNR."""
    kept: list[Decode] = []
    for d in sorted(decodes, key=lambda d: -d.snr_db):
        if not any(k.message == d.message and abs(k.rf_hz - d.rf_hz) <= freq_tol_hz for k in kept):
            kept.append(d)
    return sorted(kept, key=lambda d: d.rf_hz)
