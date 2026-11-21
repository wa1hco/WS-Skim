"""Toolbar controls for receiver 2 (RSPduo tuner 2), WSJT-X style.

Next to the main Band pulldown: ``[x] Tuner 2  [30m 10.136 MHz ▾]``.
Only shown while the source is SDRplay (SDRconnect).  Tuner 2 decodes
the same modes as Tuner 1 (Mode menu); the pulldown offers that mode's
band list, and each mode remembers its own Tuner 2 band.  The radio
thread does the work (:func:`ws_skim.receivers.reconcile`); this module
only records what is wanted in ``self._rx2_enabled`` / ``_rx2_band_mhz``.
"""
from __future__ import annotations

from PyQt5 import QtWidgets

from . import receivers


def _settings():
    from .visualizer import _SETTINGS
    return _SETTINGS


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def setup(self, toolbar) -> None:
    """Add the Tuner 2 checkbox and band pulldown to ``toolbar``."""
    s = _settings()
    self._rx2_enabled = _bool(s.value(receivers.ENABLED_KEY, False))
    self._rx2_band_mhz = None
    self._rx2_error = ""
    spacer = QtWidgets.QLabel("    ")
    check = QtWidgets.QCheckBox("Tuner 2:")
    check.setToolTip("Decode a second band on the RSPduo's second tuner, with the "
                     "same modes as Tuner 1.\nSDRconnect runs the RSPduo in "
                     "dual-tuner mode for this.")
    check.setChecked(self._rx2_enabled)
    combo = QtWidgets.QComboBox()
    combo.setToolTip("Band for Tuner 2")
    combo.setEnabled(self._rx2_enabled)
    self._rx2_check = check
    self._rx2_combo = combo
    self._rx2_bands = []
    self._rx2_actions = [toolbar.addWidget(spacer), toolbar.addWidget(check),
                         toolbar.addWidget(combo)]
    check.toggled.connect(lambda on: on_toggled(self, on))
    combo.currentIndexChanged.connect(lambda i: on_band_changed(self, i))
    refresh_bands(self)
    update_visibility(self)


def _mode(self) -> str:
    return str(getattr(self, "mode", "MSK144") or "MSK144")


def refresh_bands(self) -> None:
    """Fill the Tuner 2 pulldown with the current mode's bands."""
    combo = getattr(self, "_rx2_combo", None)
    if combo is None:
        return
    from .ui import BANDS_BY_MODE, MSK144_BANDS, band_setting_key
    mode = _mode(self)
    bands = list(getattr(self, "_bands", None) or BANDS_BY_MODE.get(mode, MSK144_BANDS))
    s = _settings()
    try:
        rx1 = float(getattr(self, "calling_freq_mhz", 0.0)
                    or s.value(band_setting_key(mode), bands[0][1]))
    except (TypeError, ValueError):
        rx1 = bands[0][1] if bands else 0.0
    try:
        saved = float(s.value(receivers.band_key(mode), receivers.default_band(bands, rx1)))
    except (TypeError, ValueError):
        saved = receivers.default_band(bands, rx1)
    idx = next((i for i, (_l, f, _s) in enumerate(bands) if abs(f - saved) < 1e-4), 0)
    self._rx2_bands = bands
    combo.blockSignals(True)
    combo.clear()
    for label, _f, _s in bands:
        combo.addItem(label)
    combo.setCurrentIndex(idx)
    combo.blockSignals(False)
    if bands:
        self._rx2_band_mhz = float(bands[idx][1])


def on_band_changed(self, idx: int) -> None:
    bands = getattr(self, "_rx2_bands", [])
    if not 0 <= idx < len(bands):
        return
    f = float(bands[idx][1])
    self._rx2_band_mhz = f
    _settings().setValue(receivers.band_key(_mode(self)), f)


def on_toggled(self, on: bool) -> None:
    on = bool(on)
    self._rx2_enabled = on
    _settings().setValue(receivers.ENABLED_KEY, on)
    self._rx2_combo.setEnabled(on)
    self._rx2_error = ""
    sc = getattr(self, "sdrconnect_client", None)
    if on and sc is not None and hasattr(sc, "set_device_mode"):
        sc.set_device_mode("dual")
    update_visibility(self)


def update_visibility(self) -> None:
    """Show the Tuner 2 controls only for the SDRplay source."""
    show = getattr(self, "source_mode", "idle") in receivers.DUAL_SOURCES
    for act in getattr(self, "_rx2_actions", []):
        if act.isVisible() != show:
            act.setVisible(show)
    label = getattr(self, "_band_label", None)
    if label is not None:
        text = "  Band (Tuner 1): " if show else "  Band: "
        if label.text() != text:
            label.setText(text)
