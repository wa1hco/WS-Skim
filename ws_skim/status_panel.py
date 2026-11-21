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
"""Main window: program status only, in the style of WS Hub's main page.

One row per part of the program, each with a coloured dot (green working,
amber needs a look, red not working, grey off), a title and a one-line
value; hover any row for details.  Decodes are in View → Roster and the
band picture in View → Band Activity.

:func:`status_rows` is pure (no Qt) so it can be tested; the receiver,
centre-frequency, channel and UTC values are written by ``displays.py``
into labels this panel lays out.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt5 import QtCore, QtGui, QtWidgets

OK, WARN, ERR, OFF = "ok", "warn", "err", "off"
LEVEL_COLORS = {OK: "#1e9e3a", WARN: "#d08a00", ERR: "#c62828", OFF: "#a0a0a0"}
LEVEL_WORDS = {OK: "working", WARN: "check", ERR: "not working", OFF: "off"}

#: Order and titles of the rows.  (key, title, tooltip)
ROWS = (
    ("receiver", "Receiver", "The radio WS Skim is listening to (File menu), with "
                             "its data rate, loss and noise-blanker activity."),
    ("band", "Band / modes", "The band picked in the toolbar and the modes being "
                             "decoded (Mode menu)."),
    ("centre", "Centre frequency", "Where the receiver is tuned (the middle of its span)."),
    ("iq", "IQ data", "Radio samples arriving per second, against the rate expected. "
                      "Green when at least 95% arrive."),
    ("audio", "Audio to WSJT-X", "12 kHz audio streams WS Skim makes for WSJT-X "
                                 "(USRP B210 only)."),
    ("decodes", "Decodes", "Decodes per minute for each mode over the last minute "
                           "(MSK144: decodes in the last 10 minutes)."),
    ("filtered", "Filtered", "FT8/FT4 decodes set aside as false since start "
                             "(kept in rejected.jsonl in the output folder)."),
    ("udp", "WSJT-X UDP", "Decodes sent as WSJT-X UDP messages to WS Hub, GridTracker, "
                          "JTAlert and others (View → Reporting)."),
    ("psk", "PSKReporter", "Spots uploaded to pskreporter.info (View → Reporting)."),
    ("dx", "DX cluster", "Spots sent to a DX cluster node (View → Reporting)."),
    ("channels", "MSK144 channels", "IF channels the MSK144 detector watches, kHz from "
                                    "the centre."),
    ("error", "Last error", "The most recent reporting error, if any."),
    ("utc", "UTC", "The computer's clock, UTC.  FT8 and FT4 need it within about a second."),
)

_SOURCE_NAMES = {
    "radio": "Flex Radio", "usrp": "USRP B210", "airspy": "Airspy HF+",
    "rtlsdr": "RTL-SDR", "sdrangel": "SDRangel",
    "sdrconnect": "SDRplay (SDRconnect)", "wav": "WAV file", "idle": "none",
}

#: Average the IQ rate over at least this long.
IQ_WINDOW_S = 3.0


@dataclass
class Row:
    key: str
    value: str | None          # None: the label is written elsewhere (displays.py)
    level: str
    tip: str = ""


def _modes(eng) -> tuple[str, ...]:
    from . import ft8_engine
    modes = ft8_engine.enabled_period_modes(eng)
    return modes or (str(getattr(eng, "mode", "MSK144")),)


def band_text(eng) -> str:
    modes = " + ".join(_modes(eng))
    label = ""
    combo = getattr(eng, "_band_combo", None)
    if combo is not None:
        label = " ".join(combo.currentText().split())
    return f"{label} · {modes}" if label else modes


def iq_health(eng, now: float | None = None) -> tuple[str, str]:
    """``(text, level)`` from how fast ``eng._iq_abs_sample`` grows."""
    src = getattr(eng, "source_mode", "idle") or "idle"
    if src == "idle":
        return "no source", OFF
    now = time.monotonic() if now is None else float(now)
    n = int(getattr(eng, "_iq_abs_sample", 0) or 0)
    hist = getattr(eng, "_status_iq_hist", None)
    if hist is None:
        hist = eng._status_iq_hist = []
    hist.append((now, n))
    while len(hist) > 2 and now - hist[1][0] >= IQ_WINDOW_S:
        hist.pop(0)
    fs = float(getattr(eng, "sample_rate", 0.0) or 0.0)
    (t0, n0), (t1, n1) = hist[0], hist[-1]
    if t1 - t0 < 1.0:
        return f"starting… ({fs / 1e3:g} kS/s expected)", WARN
    rate = (n1 - n0) / (t1 - t0)
    if fs <= 0:
        return f"{rate / 1e3:.1f} kS/s", OK
    pct = 100.0 * rate / fs
    text = f"{rate / 1e3:.1f} kS/s · {pct:.0f}% of {fs / 1e3:g} kS/s"
    if rate <= 0:
        return f"no data for {t1 - t0:.0f} s ({fs / 1e3:g} kS/s expected)", ERR
    if src == "wav":
        return text + " (replay)", OK
    return text, OK if pct >= 95.0 else WARN if pct >= 50.0 else ERR


def decodes_text(eng) -> tuple[str, str]:
    from . import ft8_engine
    rates = ft8_engine.decode_rates(eng)
    if rates:
        text = " · ".join(f"{m} {rates[m]:.0f}/min" for m in rates)
        return text, OK if any(rates.values()) else WARN
    table = getattr(eng, "decode_panel", None)
    n = table.recent_count(600.0, "MSK144") if hasattr(table, "recent_count") else 0
    return f"MSK144 {n} in 10 min", OK if n else WARN


def filtered_text(eng) -> tuple[str, str, str]:
    from . import ft8_engine
    n = sum((getattr(eng, "_ft8_filtered", None) or {}).values())
    if not ft8_engine.enabled_period_modes(eng):
        return "FT8/FT4 only", OFF, ""
    return (f"{n:,} since start" if n else "none yet"), OK, (
        "By reason: " + ft8_engine.filter_detail(eng))


def reporting_rows(rpt) -> dict[str, Row]:
    """UDP / PSKReporter / DX cluster / last-error rows from the Reporter."""
    if rpt is None:
        return {k: Row(k, "not started", OFF) for k in ("udp", "psk", "dx")} | {
            "error": Row("error", "none", OK)}
    err = str(getattr(rpt, "stat_last_error", "") or "")
    out = {}
    if getattr(rpt, "wsjtx_enabled", False):
        dest = f"{rpt.wsjtx_host}:{rpt.wsjtx_port}"
        ids = []
        getter = getattr(rpt, "instance_ids", None)
        if callable(getter):
            try:
                ids = list(getter())
            except Exception:
                ids = []
        level = ERR if err.startswith("UDP") else OK
        out["udp"] = Row("udp", f"on → {dest} · {rpt.stat_udp_sent:,} sent", level,
                         "Instances: " + (", ".join(ids) if ids else "none yet"))
    else:
        out["udp"] = Row("udp", "off", OFF)
    if getattr(rpt, "pskreporter_enabled", False):
        last = rpt.stat_last_psk_time or "none yet"
        level = ERR if err.startswith("PSKReporter") else OK
        out["psk"] = Row("psk", f"on · {rpt.stat_psk_uploaded:,} uploaded · last {last}"
                         f"{' UTC' if rpt.stat_last_psk_time else ''}", level,
                         f"{rpt.stat_psk_queued} waiting for the next upload")
    else:
        out["psk"] = Row("psk", "off", OFF)
    if getattr(rpt, "dx_enabled", False):
        st = str(rpt.stat_dx_status or "")
        level = OK if st.startswith("connected") else ERR if st.startswith("error") else WARN
        out["dx"] = Row("dx", f"{st} · {rpt.stat_dx_sent:,} spots", level)
    else:
        out["dx"] = Row("dx", "off", OFF)
    out["error"] = Row("error", err or "none", ERR if err else OK)
    return out


def audio_text(eng) -> tuple[str, str, str]:
    exports = getattr(eng, "_wsjtx_exports", None) or {}
    if not exports:
        return "off", OFF, ""
    names = ", ".join(str(getattr(e, "label", k)) for k, e in sorted(exports.items()))
    dropped = sum(int(getattr(e, "_dropped", 0) or 0) for e in exports.values())
    return (f"{len(exports)} stream{'s' if len(exports) > 1 else ''} ({names})"
            + (f" · {dropped:,} dropped" if dropped else ""),
            WARN if dropped else OK, "")


def status_rows(eng, now: float | None = None) -> dict[str, Row]:
    """Every row's value and level (pure: reads ``eng`` only)."""
    rows: dict[str, Row] = {}
    src = getattr(eng, "source_mode", "idle") or "idle"
    iq_txt, iq_level = iq_health(eng, now)
    rows["receiver"] = Row("receiver", "none · pick one in the File menu" if src == "idle"
                           else None, OFF if src == "idle" else iq_level,
                           f"Source: {_SOURCE_NAMES.get(src, src)}")
    rows["band"] = Row("band", band_text(eng), OK)
    rows["centre"] = Row("centre", None, OFF if src == "idle" else OK)
    rows["iq"] = Row("iq", iq_txt, iq_level)
    a_txt, a_level, a_tip = audio_text(eng)
    rows["audio"] = Row("audio", a_txt, a_level, a_tip)
    d_txt, d_level = decodes_text(eng)
    rows["decodes"] = Row("decodes", d_txt, OFF if src == "idle" else d_level)
    f_txt, f_level, f_tip = filtered_text(eng)
    rows["filtered"] = Row("filtered", f_txt, f_level, f_tip)
    rows.update(reporting_rows(getattr(eng, "reporter", None)))
    msk = "MSK144" in _modes(eng)
    rows["channels"] = Row("channels", None, OK if msk else OFF)
    rows["utc"] = Row("utc", None, OK)
    return rows


# ── Qt panel ───────────────────────────────────────────────────────────────

def _dot(level: str) -> str:
    return (f'<span style="color:{LEVEL_COLORS[level]}; font-size:15pt;">&#9679;</span>')


def version_text() -> tuple[str, str]:
    """``("v0.1.4-test.16", "0.1.4 (46ba0c9)")``: the release tag this copy
    was installed from when known, else the version; and the build."""
    from . import __version__
    try:
        from .buildinfo import version_string
        build = version_string()
    except Exception:
        build = __version__
    try:
        from .update_check import installed_tag
        tag = installed_tag()
    except Exception:
        tag = ""
    return (tag if tag else f"v{__version__}"), build


def build_status_panel(self) -> QtWidgets.QWidget:
    """The main window's central widget.  Expects ``self._receiver_label``,
    ``tuned_freq_label``, ``_msk_monitor_label`` and ``utc_clock_label``."""
    w = QtWidgets.QWidget()
    w.setObjectName("statusPanel")
    w.setStyleSheet(
        "QWidget#statusPanel { background: #ffffff; }"
        "QLabel { color: #1a1a1a; background: transparent; }"
        "QLabel[role='title'] { color: #505860; font-weight: bold; }"
        "QFrame[role='rule'] { color: #e2e5e9; }")
    v = QtWidgets.QVBoxLayout(w)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(6)

    ver, build = version_text()
    head = QtWidgets.QLabel(f"<span style='font-size:17pt; font-weight:bold;'>WS Skim</span>"
                            f"&nbsp;&nbsp;<span style='color:#606870;'>{ver}</span>")
    head.setToolTip("Wideband FT8 / FT4 / MSK144 skimmer for one radio.\n"
                    f"Build: {build}\n"
                    "Decodes: View → Roster.  Band picture: View → Band Activity.")
    v.addWidget(head)
    sub = QtWidgets.QLabel("Program status")
    sub.setStyleSheet("QLabel { color: #606870; }")
    v.addWidget(sub)
    rule = QtWidgets.QFrame()
    rule.setFrameShape(QtWidgets.QFrame.HLine)
    rule.setProperty("role", "rule")
    v.addWidget(rule)

    grid = QtWidgets.QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(7)
    grid.setColumnStretch(2, 1)
    external = {"receiver": self._receiver_label, "centre": self.tuned_freq_label,
                "channels": self._msk_monitor_label, "utc": self.utc_clock_label}
    font = QtGui.QFont()
    font.setPointSize(11)
    self._status_widgets = {}
    for r, (key, title, tip) in enumerate(ROWS):
        dot = QtWidgets.QLabel(_dot(OFF))
        dot.setTextFormat(QtCore.Qt.RichText)
        name = QtWidgets.QLabel(title)
        name.setProperty("role", "title")
        name.setFont(font)
        value = external.get(key) or QtWidgets.QLabel("—")
        value.setStyleSheet("")
        value.setFont(font)
        value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        for x in (dot, name, value):
            x.setToolTip(tip)
        grid.addWidget(dot, r, 0, QtCore.Qt.AlignVCenter)
        grid.addWidget(name, r, 1, QtCore.Qt.AlignVCenter)
        grid.addWidget(value, r, 2, QtCore.Qt.AlignVCenter)
        self._status_widgets[key] = (dot, name, value, tip)
    v.addLayout(grid)
    v.addStretch(1)
    hint = QtWidgets.QLabel("Decodes: View → Roster  ·  Band picture: View → Band Activity")
    hint.setStyleSheet("QLabel { color: #707880; }")
    v.addWidget(hint)
    self._status_panel = w
    return w


def update_status_panel(self, now: float | None = None) -> None:
    """Refresh the dots and values (about once a second)."""
    widgets = getattr(self, "_status_widgets", None)
    if not widgets:
        return
    try:
        rows = status_rows(self, now)
    except Exception as e:                          # never kill the display loop
        dot, _n, value, _t = widgets["error"]
        value.setText(f"status error: {e}")
        return
    for key, row in rows.items():
        if key not in widgets:
            continue
        dot, name, value, tip = widgets[key]
        dot.setText(_dot(row.level))
        if row.value is not None:
            value.setText(row.value)
        full = tip + (f"\n\n{row.tip}" if row.tip else "") + \
            f"\n\nStatus: {LEVEL_WORDS[row.level]}"
        for x in (dot, name, value):
            x.setToolTip(full)
        visible = not (key == "channels" and row.level == OFF)
        for x in (dot, name, value):
            x.setVisible(visible)
