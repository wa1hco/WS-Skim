"""Where WS Skim's own Flex panadapter goes, and what rate it runs at.

Used when "WS Skim makes its own panadapter" is ticked in the Flex Radio
window (setting ``flex_own_pan``).  The pan centre holds the dial of every
FT8/FT4 mode being decoded on the selected band (40 m: FT8 7.074 and FT4
7.0475) with the centre itself (DC) off every sub-band.  MSK144 runs at
48 kHz centred on its calling frequency.
"""
from __future__ import annotations

from . import rates

SETTING_KEY = "flex_own_pan"


def own_pan_enabled(eng) -> bool:
    return bool(getattr(eng, "_flex_own_pan", False))


def band_dials_mhz(eng) -> tuple[float, list[float]]:
    """(primary dial, every dial to keep inside the pan) for ``eng``'s band."""
    from .ui import BANDS_BY_MODE
    from .wsjtx_udp import band_label
    primary = float(getattr(eng, "calling_freq_mhz", 0.0) or 0.0)
    band = band_label(primary * 1e6)
    dials = [primary]
    modes = list(getattr(eng, "period_modes", ()) or ())
    mode = getattr(eng, "mode", "")
    if mode in ("FT8", "FT4") and mode not in modes:
        modes.append(mode)
    for m in modes:
        if m == "MSK144":
            continue
        for _label, f, _src in BANDS_BY_MODE.get(m, ()):
            if band_label(f * 1e6) == band:
                dials.append(float(f))
    return primary, dials


def wanted_center_mhz(eng, rate: int | None = None) -> float:
    """Pan centre for ``eng``'s band and modes at ``rate``."""
    from flexclient.owned_pan import choose_center_mhz
    primary, dials = band_dials_mhz(eng)
    if rates.needs_base_rate(eng) and getattr(eng, "mode", "") == "MSK144":
        return round(primary, 6)
    r = int(rate or rates.wanted_rate(eng, "radio"))
    return choose_center_mhz(dials, r, primary_mhz=primary)


def reconcile(eng) -> None:
    """Push the wanted centre and rate to the owned pan (cheap; call often)."""
    rc = getattr(eng, "radio_client", None)
    if rc is None or not getattr(rc, "own_pan", False) or not hasattr(rc, "retune"):
        return
    want_rate = rates.wanted_rate(eng, "radio")
    if getattr(rc, "wanted_rate", None) != want_rate:
        rc.set_target_rate(want_rate)
    try:
        c = wanted_center_mhz(eng, want_rate)
    except Exception:
        return
    cur = getattr(rc, "wanted_center_mhz", None)
    if cur is None or abs(cur - c) > 1e-6:
        rc.retune(c)
