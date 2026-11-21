"""IQ sample rate per receiver.

MSK144 needs 48 kHz (its channelizer, matched filters and display constants
are built for it).  FT8 and FT4 only search Costas sync across the IQ band,
so with MSK144 off a receiver runs at 96 kHz where the source can: a
40 m window centred on FT8 7.074 then also holds FT4 7.0475.

The source sets the rate and stamps it on every packet; the engine follows
the packets (``Engine.set_sample_rate``), so the rate never changes under
samples already queued at the old one.
"""
from __future__ import annotations

#: The rate every source starts at, and the only one MSK144 runs at.
BASE_RATE = 48_000
#: The rate used for FT8/FT4 only, on sources that can deliver it.
WIDE_RATE = 96_000
#: Sources whose decimator can switch between the two at run time.
#: (Flex DAX IQ is fixed at 48 kHz; the others restart to change rate and
#: stay at 48 kHz for now.)
WIDE_SOURCES = ("sdrconnect",)


def needs_base_rate(eng) -> bool:
    """True when ``eng`` decodes MSK144 (alone or alongside anything)."""
    mode = getattr(eng, "mode", "MSK144")
    if mode == "MSK144":
        return True
    return "MSK144" in tuple(getattr(eng, "period_modes", ()) or ())


def wanted_rate(eng, source: str | None = None) -> int:
    """The IQ rate ``eng`` should run at on ``source`` (default its own)."""
    src = source if source is not None else getattr(eng, "source_mode", "")
    if src in WIDE_SOURCES and not needs_base_rate(eng):
        return WIDE_RATE
    return BASE_RATE
