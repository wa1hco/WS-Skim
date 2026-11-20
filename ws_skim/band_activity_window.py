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
"""Band Activity window (View → Band Activity): the whole band, every mode.

A vertical waterfall of the receiver's full span.  Frequency runs across
the top; UTC time runs down the side, newest at the top, scrolling down.
Two layers, each with its own check box (remembered):

* **Waterfall** - the real signals, from the radio's IQ.  Shows activity
  the decoders miss (weak, partial or other-mode signals).
* **Detections** - one marker per decode, at its frequency and period
  (an MSK144 ping at its burst time), coloured by mode: FT8, FT4, MSK144.

The FT8, FT4 and MSK144 sub-bands (WSJT-X dial + 200…3000 Hz) are lightly
shaded in the mode colour.  No callsigns are drawn on the plot: hover a
marker for call, grid, SNR, mode, UTC time and frequency.

Decodes the false-decode filter set aside (``PeriodResult.rejected``) are
never shown: FT8/FT4 come from ``PeriodResult.decodes`` only, MSK144 from
the decoded records of the main decode queue.

CPU: the waterfall costs nothing while the window is hidden or *Waterfall*
is off (the engine skips the FFT).  When on, the radio thread averages the
overlap-FFT power over :data:`MIN_ROW_S`-ish rows and keeps at most
:data:`MAX_COLUMNS` columns (adjacent bins averaged), in a ring buffer the
GUI repaints once a second.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from .plot_style import style_plot

# ── Settings ───────────────────────────────────────────────────────────────

GEOMETRY_KEY = "band_activity_geometry"
MINUTES_KEY = "band_activity_minutes"
WATERFALL_KEY = "band_activity_waterfall"
DETECTIONS_KEY = "band_activity_detections"
#: Keys of the FT8 Activity window this one replaces (read once to migrate).
OLD_GEOMETRY_KEY = "activity_geometry"
OLD_PERIODS_KEY = "activity_periods"

DEFAULT_MINUTES = 2
MIN_MINUTES = 1
MAX_MINUTES = 30

# ── Look ───────────────────────────────────────────────────────────────────

MODE_ORDER = ("FT8", "FT4", "MSK144")
#: Marker / shading colour per mode; all readable on white and on the
#: blue-grey waterfall.
MODE_COLORS = {
    "FT8": (230, 85, 13),       # orange
    "FT4": (0, 150, 60),        # green
    "MSK144": (175, 0, 175),    # magenta
}
#: Occupied bandwidth drawn for a decode, Hz (FT8/FT4 from tone 0 up;
#: MSK144 centred on its frequency).
MODE_WIDTH_HZ = {"FT8": 50.0, "FT4": 83.3, "MSK144": 2000.0}
#: Marker height: the T/R period (MSK144: about one ping).
MODE_SPAN_S = {"FT8": 15.0, "FT4": 7.5, "MSK144": 1.0}
#: Share of the period a marker fills (a small gap between periods).
MARK_FILL = 0.86
#: Markers are at least this big on screen when zoomed out.
MIN_MARK_W_PX = 5.0
MIN_MARK_H_PX = 4.0
#: Sub-band shading: the WSJT-X audio passband above each dial.
SUBBAND_LOW_HZ = 200.0
SUBBAND_HIGH_HZ = 3000.0
SUBBAND_ALPHA = 34

# White (noise) → pale blue-grey → steel blue → navy (strongest).
WATERFALL_CMAP = pg.ColorMap(
    np.array([0.0, 0.3, 0.65, 1.0]),
    np.array([[255, 255, 255, 255],
              [205, 215, 230, 255],
              [80, 110, 165, 255],
              [15, 25, 70, 255]], dtype=np.ubyte),
)
#: Colour scale: noise floor (a low percentile) minus this … plus RANGE_DB.
LEVEL_BELOW_FLOOR_DB = 2.0
LEVEL_RANGE_DB = 30.0

# ── Waterfall buffer ───────────────────────────────────────────────────────

#: At most this many frequency columns (adjacent FFT bins averaged).
MAX_COLUMNS = 1024
#: At most this many rows; rows are at least MIN_ROW_S apart.
MAX_ROWS = 1200
MIN_ROW_S = 0.5
FILL_DB = -200.0
#: Left (UTC) axis width, px: an 11 pt HH:MM:SS tick plus the axis title.
TIME_AXIS_WIDTH = 110
#: GUI repaint period.
TIMER_MS = 1000


def clamp_minutes(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_MINUTES
    return max(MIN_MINUTES, min(MAX_MINUTES, n))


def minutes_from_periods(periods) -> int:
    """The old window's History (FT8 periods) in whole minutes."""
    try:
        return clamp_minutes(round(int(periods) * 15 / 60))
    except (TypeError, ValueError):
        return DEFAULT_MINUTES


class BandWaterfall:
    """Scrolling, column-reduced spectrogram shared by the radio thread
    (:meth:`push`) and the GUI (:meth:`snapshot`)."""

    def __init__(self, span_s: float = DEFAULT_MINUTES * 60.0,
                 max_columns: int = MAX_COLUMNS, max_rows: int = MAX_ROWS):
        self._lock = threading.Lock()
        self.max_columns = int(max_columns)
        self.max_rows = int(max_rows)
        #: Set by the window: True only while it is shown with Waterfall on.
        self.wanted = False
        self.version = 0
        self._geom = None
        self.set_span(span_s)

    # Geometry ---------------------------------------------------------------
    def set_span(self, span_s: float) -> None:
        """Change the history length, keeping the rows already recorded."""
        with self._lock:
            old = self._rows_locked()
            self.span_s = float(span_s)
            self.row_s = max(MIN_ROW_S, self.span_s / self.max_rows)
            self.n_rows = max(2, int(math.ceil(self.span_s / self.row_s)))
            self._reset_locked(self._geom)
            if old is not None and self.buf is not None:
                self._regrid_locked(*old)

    def _rows_locked(self):
        """``(rows oldest first, row_s, t_last)`` or None."""
        if getattr(self, "buf", None) is None or not getattr(self, "n", 0):
            return None
        idx = (self.head - self.n + np.arange(self.n)) % self.n_rows
        return self.buf[idx].copy(), self.row_s, self.t_last

    def _regrid_locked(self, rows, old_row_s: float, t_last: float) -> None:
        """Put old rows onto the new row spacing (newest row stays newest)."""
        if rows.shape[1] != self.n_cols:
            return
        n_old = rows.shape[0]
        old_start = t_last - (n_old - 1) * old_row_s       # start time of rows[0]
        n_new = min(self.n_rows,
                    int((t_last - old_start) / self.row_s) + 1)
        for j in range(n_new):
            t = t_last - (n_new - 1 - j) * self.row_s
            lo = int(math.floor((t - old_start) / old_row_s + 1e-9))
            hi = int(math.floor((t + self.row_s - old_start) / old_row_s - 1e-9))
            lo, hi = max(0, lo), min(n_old - 1, max(lo, hi))
            seg = rows[lo:hi + 1]
            real = seg[(seg > FILL_DB + 1.0).any(axis=1)] if seg.size else seg
            self.buf[j] = real.max(axis=0) if real.shape[0] else FILL_DB
        self.head = n_new % self.n_rows
        self.n = n_new
        self.t_last = float(t_last)
        self.version += 1

    def _reset_locked(self, geom) -> None:
        self._geom = geom                     # (n_bins, center_mhz, fs)
        self.buf = None
        self.head = 0                         # next row to write
        self.n = 0
        self.t_last = None                    # start time of the newest row
        self._acc = None
        self._acc_n = 0
        self._acc_t0 = None
        self.version += 1
        if geom is not None:
            n_bins = geom[0]
            self.factor = max(1, int(math.ceil(n_bins / self.max_columns)))
            self.n_cols = n_bins // self.factor
            self.buf = np.full((self.n_rows, self.n_cols), FILL_DB, dtype=np.float32)

    def clear(self) -> None:
        with self._lock:
            self._reset_locked(self._geom)

    # Radio thread -----------------------------------------------------------
    def push(self, magnitude, t: float, center_mhz: float, fs: float) -> None:
        """Add one fftshifted FFT magnitude spectrum taken at UTC ``t``."""
        mag = np.asarray(magnitude, dtype=np.float32).reshape(-1)
        geom = (int(mag.shape[0]), round(float(center_mhz), 9), float(fs))
        with self._lock:
            if geom != self._geom:
                self._reset_locked(geom)
            if self._acc_t0 is None or t < self._acc_t0:
                self._acc = np.zeros(mag.shape[0], dtype=np.float64)
                self._acc_n = 0
                self._acc_t0 = float(t)
            self._acc += mag.astype(np.float64) ** 2
            self._acc_n += 1
            if t - self._acc_t0 >= self.row_s:
                self._finish_row_locked()

    def _finish_row_locked(self) -> None:
        p = self._acc / max(1, self._acc_n)
        f = self.factor
        p = p[: self.n_cols * f].reshape(self.n_cols, f).mean(axis=1)
        row = (10.0 * np.log10(p + 1e-20)).astype(np.float32)
        t_row = self._acc_t0
        self._acc = np.zeros_like(self._acc)
        self._acc_n = 0
        self._acc_t0 = None
        if self.t_last is not None:
            k = int(round((t_row - self.t_last) / self.row_s))
            if k <= 0 or k > 4 * self.n_rows:      # time went back or far ahead
                self._reset_locked(self._geom)
            else:
                for _ in range(min(k - 1, self.n_rows)):   # a gap: blank rows
                    self.buf[self.head] = FILL_DB
                    self.head = (self.head + 1) % self.n_rows
                    self.n = min(self.n + 1, self.n_rows)
                t_row = self.t_last + k * self.row_s       # keep rows evenly spaced
        self.buf[self.head] = row
        self.head = (self.head + 1) % self.n_rows
        self.n = min(self.n + 1, self.n_rows)
        self.t_last = float(t_row)
        self.version += 1

    # GUI --------------------------------------------------------------------
    def snapshot(self):
        """``(rows, t0, t1, f0_mhz, f1_mhz)`` with rows oldest first, or None.

        ``rows`` is ``(n, n_cols)`` dB; row ``i`` covers
        ``t0 + i*row_s … t0 + (i+1)*row_s``; columns span ``f0…f1`` MHz.
        """
        with self._lock:
            if self.buf is None or self.n == 0 or self._geom is None:
                return None
            n = self.n
            idx = (self.head - n + np.arange(n)) % self.n_rows
            rows = self.buf[idx].copy()
            t1 = self.t_last + self.row_s
            t0 = t1 - n * self.row_s
            n_bins, center, fs = self._geom
            df = fs / n_bins
            f0 = center * 1e6 - fs / 2.0 - df / 2.0
            f1 = f0 + self.n_cols * self.factor * df
        return rows, t0, t1, f0 / 1e6, f1 / 1e6

    def span_mhz(self):
        with self._lock:
            if self._geom is None:
                return None
            _n, center, fs = self._geom
            return center - fs / 2e6, center + fs / 2e6


def color_levels(rows: np.ndarray) -> tuple[float, float]:
    """Colour-scale ends: just under the noise floor … +LEVEL_RANGE_DB."""
    sample = rows[:: max(1, rows.shape[0] // 64)]
    sample = sample[sample > FILL_DB + 1.0]
    if sample.size == 0:
        return (-130.0, -100.0)
    lo = float(np.percentile(sample, 30)) - LEVEL_BELOW_FLOOR_DB
    return lo, lo + LEVEL_RANGE_DB


def push_spectrum(eng, magnitude, t: float) -> None:
    """Radio-thread entry point (processing.py), only called while wanted."""
    wf = getattr(eng, "_band_wf", None)
    if wf is None:
        return
    wf.push(magnitude, t, float(getattr(eng, "center_freq_mhz", 0.0)),
            float(getattr(eng, "sample_rate", 48000.0)))


# ── Detections ─────────────────────────────────────────────────────────────

@dataclass
class Detection:
    mode: str
    t0: float              # UTC seconds, top/bottom of the marker
    t1: float
    f_lo_mhz: float
    f_hi_mhz: float
    freq_mhz: float        # reported frequency (FT8/FT4 tone 0, MSK144 centre)
    when: float            # UTC of the period start (MSK144: the ping)
    call: str = ""
    grid: str = ""
    snr_db: int | None = None
    message: str = ""

    @property
    def f_mid_mhz(self) -> float:
        return 0.5 * (self.f_lo_mhz + self.f_hi_mhz)

    def tooltip(self) -> str:
        hms = time.strftime("%H:%M:%S", time.gmtime(self.when))
        snr = f"{self.snr_db:+d} dB" if self.snr_db is not None else "SNR ?"
        lines = [f"{self.call or '?'}   {self.grid or 'no grid'}",
                 f"{self.mode}   {snr}",
                 f"{hms} UTC   {self.freq_mhz:.6f} MHz"]
        if self.message:
            lines.append(self.message)
        return "\n".join(lines)


def _call_and_grid(message: str) -> tuple[str, str]:
    from .ft8_window import call_from_message
    call = call_from_message(message)
    grid = ""
    try:
        from .decode_filter import parse_message
        p = parse_message(message or "")
        if p is not None and p.grid:
            grid = p.grid
    except Exception:
        pass
    return call, grid


def _period_marker(start: float, period_s: float) -> tuple[float, float]:
    pad = period_s * (1.0 - MARK_FILL) / 2.0
    return start + pad, start + period_s - pad


def detection_from_decode(d) -> Detection:
    """One FT8/FT4 ``ft8_decode.Decode`` (already through the filter)."""
    mode = str(d.mode)
    period = MODE_SPAN_S.get(mode, 15.0)
    start = math.floor(float(d.period_start_utc) / period + 1e-9) * period
    t0, t1 = _period_marker(start, period)
    rf = float(d.rf_hz)
    call, grid = _call_and_grid(d.message)
    return Detection(mode, t0, t1, rf / 1e6, (rf + MODE_WIDTH_HZ.get(mode, 50.0)) / 1e6,
                     rf / 1e6, start, call, grid, int(d.snr_db), str(d.message))


def record_time(rec: dict, now: float | None = None) -> float:
    """UTC seconds of a decode-queue record (period time + ``t_sec``)."""
    now = time.time() if now is None else float(now)
    ts = rec.get("timestamp")
    base = None
    if ts:
        try:
            base = datetime.strptime(str(ts), "%Y-%m-%d_%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            base = None
    if base is None and rec.get("utc_time"):
        try:
            h, m, s = (int(x) for x in str(rec["utc_time"]).split(":"))
            day = datetime.fromtimestamp(now, tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp()
            base = day + h * 3600 + m * 60 + s
            if base > now + 600:                  # just before midnight UTC
                base -= 86400
        except (TypeError, ValueError):
            base = None
    if base is None:
        return now
    if rec.get("mode") == "MSK144":
        try:
            base += float(rec.get("t_sec") or 0.0)
        except (TypeError, ValueError):
            pass
    return base


def detection_from_record(rec: dict, now: float | None = None) -> Detection | None:
    """An MSK144 decode-queue record; None for anything that is not one.

    FT8/FT4 records are skipped here: they arrive from ``PeriodResult``.
    """
    if not isinstance(rec, dict) or rec.get("mode", "MSK144") != "MSK144":
        return None
    if rec.get("rejected") or not (rec.get("decoded") or rec.get("outcome") == "decoded"):
        return None
    try:
        f = float(rec["radio_khz"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None
    t = record_time(rec, now)
    half = MODE_SPAN_S["MSK144"] / 2.0
    hw = MODE_WIDTH_HZ["MSK144"] / 2e6
    msg = str(rec.get("message") or "")
    call, grid = _call_and_grid(msg)
    snr = rec.get("jt9_snr")
    try:
        snr = int(snr) if snr is not None else None
    except (TypeError, ValueError):
        snr = None
    return Detection("MSK144", t - half, t + half, f - hw, f + hw, f, t,
                     call, grid, snr, msg)


class DetectionStore:
    """Decodes of every mode for the history span."""

    def __init__(self, span_s: float = DEFAULT_MINUTES * 60.0):
        self.span_s = float(span_s)
        self.items: list[Detection] = []
        self._periods: dict[tuple, list[Detection]] = {}
        self.version = 0

    def set_span(self, span_s: float) -> None:
        self.span_s = float(span_s)
        self.trim()

    def add_period_result(self, res) -> bool:
        """Record an FT8/FT4 period's kept decodes (``res.rejected`` never)."""
        if res is None or not getattr(res, "decoded", False):
            return False
        decodes = list(getattr(res, "decodes", []) or [])
        mode = str(res.mode)
        key = (mode, round(float(res.period_start_utc), 3))
        dets = [detection_from_decode(d) for d in decodes]
        old = self._periods.get(key)
        if old is not None and len(old) == len(dets):
            return False
        if old:
            ids = {id(x) for x in old}
            self.items = [x for x in self.items if id(x) not in ids]
        self._periods[key] = dets
        self.items.extend(dets)
        self.version += 1
        return True

    def add(self, det: Detection | None) -> bool:
        if det is None:
            return False
        self.items.append(det)
        self.version += 1
        return True

    def newest(self) -> float | None:
        return max((d.t1 for d in self.items), default=None)

    def trim(self, now: float | None = None) -> None:
        ref = now if now is not None else self.newest()
        if ref is None:
            return
        keep_from = ref - self.span_s
        n = len(self.items)
        self.items = [d for d in self.items if d.t1 >= keep_from]
        self._periods = {k: v for k, v in self._periods.items()
                         if k[1] >= keep_from - 60.0}
        if len(self.items) != n:
            self.version += 1

    def nearest(self, f_mhz: float, t: float, f_tol_mhz: float, t_tol_s: float):
        best, best_d = None, None
        for d in self.items:
            if not (d.t0 - t_tol_s <= t <= d.t1 + t_tol_s):
                continue
            if not (d.f_lo_mhz - f_tol_mhz <= f_mhz <= d.f_hi_mhz + f_tol_mhz):
                continue
            dist = abs(f_mhz - d.f_mid_mhz) / max(f_tol_mhz, 1e-9) \
                + abs(t - 0.5 * (d.t0 + d.t1)) / max(t_tol_s, 1e-9) * 1e-3
            if best_d is None or dist < best_d:
                best, best_d = d, dist
        return best

    def counts(self) -> dict[str, int]:
        out = {m: 0 for m in MODE_ORDER}
        for d in self.items:
            out[d.mode] = out.get(d.mode, 0) + 1
        return out


def mode_color(mode: str) -> tuple[int, int, int]:
    return MODE_COLORS.get(mode, (90, 90, 90))


def subbands(f0_mhz: float, f1_mhz: float) -> list[tuple[str, float, float, float]]:
    """``(mode, lo, hi, dial)`` of the WSJT-X sub-bands inside ``f0…f1`` MHz."""
    from .ui import BANDS_BY_MODE
    out = []
    for mode in MODE_ORDER:
        for _label, dial, _src in BANDS_BY_MODE.get(mode, []):
            lo = dial + SUBBAND_LOW_HZ / 1e6
            hi = dial + SUBBAND_HIGH_HZ / 1e6
            if hi > f0_mhz and lo < f1_mhz:
                out.append((mode, lo, hi, dial))
    return out


class MarkersItem(pg.GraphicsObject):
    """Paints detections as rectangles (frequency across, time up), with a
    minimum on-screen size.  Translucent with an outline over the waterfall,
    solid without it."""

    def __init__(self):
        super().__init__()
        self._dets: list[Detection] = []
        self._hl: set[str] = set()
        self.translucent = True
        self._bounds = QtCore.QRectF()

    def set_detections(self, dets, highlighted=(), translucent=True) -> None:
        self.prepareGeometryChange()
        self._dets = list(dets)
        self._hl = {c.upper() for c in highlighted}
        self.translucent = bool(translucent)
        if self._dets:
            x0 = min(d.f_lo_mhz for d in self._dets)
            x1 = max(d.f_hi_mhz for d in self._dets)
            y0 = min(d.t0 for d in self._dets)
            y1 = max(d.t1 for d in self._dets)
            px, py = 0.002, 2.0
            self._bounds = QtCore.QRectF(x0 - px, y0 - py, (x1 - x0) + 2 * px,
                                         (y1 - y0) + 2 * py)
        else:
            self._bounds = QtCore.QRectF()
        self.update()

    def detections(self) -> list[Detection]:
        return list(self._dets)

    def brush_color(self, det: Detection) -> QtGui.QColor:
        r, g, b = mode_color(det.mode)
        # Over the waterfall: a light tint and an outline, so the signal shows through.
        return QtGui.QColor(r, g, b, 45 if self.translucent else 235)

    def boundingRect(self):
        return self._bounds

    def paint(self, painter, *_args):
        if not self._dets:
            return
        min_w = min_h = 0.0
        view = self.getViewBox()
        if view is not None:
            px, py = view.viewPixelSize()
            min_w, min_h = MIN_MARK_W_PX * float(px), MIN_MARK_H_PX * float(py)
        for d in self._dets:
            x0, x1, y0, y1 = d.f_lo_mhz, d.f_hi_mhz, d.t0, d.t1
            if x1 - x0 < min_w:
                mid = 0.5 * (x0 + x1)
                x0, x1 = mid - min_w / 2, mid + min_w / 2
            if y1 - y0 < min_h:
                mid = 0.5 * (y0 + y1)
                y0, y1 = mid - min_h / 2, mid + min_h / 2
            r, g, b = mode_color(d.mode)
            hl = bool(d.call) and d.call.upper() in self._hl
            pen = pg.mkPen((0, 0, 0) if hl else (r, g, b), width=2.0 if hl else 1.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(self.brush_color(d)))
            painter.drawRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))


# ── Window ─────────────────────────────────────────────────────────────────

def _settings():
    from .visualizer import _SETTINGS
    return _SETTINGS


def _bool_setting(key: str, default: bool) -> bool:
    v = _settings().value(key, default)
    if v is None:
        return default
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return bool(v)


def saved_minutes() -> int:
    s = _settings()
    v = s.value(MINUTES_KEY, None)
    if v is not None:
        return clamp_minutes(v)
    old = s.value(OLD_PERIODS_KEY, None)
    return minutes_from_periods(old) if old is not None else DEFAULT_MINUTES


def legend_html() -> str:
    parts = []
    for m in MODE_ORDER:
        r, g, b = MODE_COLORS[m]
        parts.append(f'<span style="color:rgb({r},{g},{b}); font-size:14pt;">&#9632;</span>'
                     f'&nbsp;{m}')
    return "&nbsp;&nbsp;&nbsp;".join(parts)


def build_window(self, view_action):
    """Create the Band Activity panel (hidden); stored as
    ``self._band_activity_win``; the engine's waterfall is ``self._band_wf``."""
    from .ui import _PanelWindow

    class Panel(_PanelWindow):
        def showEvent(self_, event):
            super().showEvent(event)
            _update_wanted(self_)

        def hideEvent(self_, event):
            super().hideEvent(event)
            _update_wanted(self_)

    win = Panel("WS Skim — Band Activity", view_action, self, GEOMETRY_KEY)
    win.setMinimumSize(560, 420)
    s = _settings()
    geo = s.value(GEOMETRY_KEY) or s.value(OLD_GEOMETRY_KEY)
    if geo:
        win.restoreGeometry(geo)
    else:
        win.setGeometry(QtCore.QRect(480, 50, 1000, 700))
    layout = QtWidgets.QVBoxLayout(win)
    layout.setContentsMargins(4, 4, 4, 4)

    status = QtWidgets.QLabel("Waiting for the radio…")
    layout.addWidget(status)
    top = QtWidgets.QHBoxLayout()
    wf_cb = QtWidgets.QCheckBox("Waterfall")
    wf_cb.setToolTip("Show the real signals across the receiver's whole span, "
                     "including ones nothing decoded")
    wf_cb.setChecked(_bool_setting(WATERFALL_KEY, True))
    top.addWidget(wf_cb)
    det_cb = QtWidgets.QCheckBox("Detections")
    det_cb.setToolTip("Mark each decode at its frequency and time, coloured by mode. "
                      "Hover over a mark for call, grid, SNR, mode, time and frequency.")
    det_cb.setChecked(_bool_setting(DETECTIONS_KEY, True))
    top.addWidget(det_cb)
    top.addSpacing(16)
    top.addWidget(QtWidgets.QLabel("History:"))
    spin = QtWidgets.QSpinBox()
    spin.setRange(MIN_MINUTES, MAX_MINUTES)
    spin.setSuffix(" min")
    spin.setToolTip("How much time the window shows, newest at the top")
    spin.setValue(saved_minutes())
    top.addWidget(spin)
    top.addSpacing(16)
    legend = QtWidgets.QLabel(legend_html())
    legend.setTextFormat(QtCore.Qt.RichText)
    legend.setToolTip("Marker colours.  The same colours lightly shade each "
                      "mode's WSJT-X sub-band (dial + 200 to 3000 Hz).")
    top.addWidget(legend)
    top.addStretch(1)
    layout.addLayout(top)

    axis = pg.DateAxisItem(orientation="left", utcOffset=0)
    plot = pg.PlotWidget(axisItems={"left": axis})
    plot.setBackground("w")
    plot.showAxis("top")
    plot.hideAxis("bottom")
    plot.setLabel("top", "Frequency (MHz)")
    plot.setLabel("left", "Time (UTC)")
    style_plot(plot)
    for name in ("top", "left"):
        plot.getAxis(name).setPen("k")
        plot.getAxis(name).setTextPen("k")
    plot.getAxis("left").setWidth(TIME_AXIS_WIDTH)      # room for "22:13:30"
    plot.showGrid(x=True, y=True, alpha=0.18)
    vb = plot.getViewBox()
    vb.disableAutoRange()
    # Wheel and drag zoom frequency only; time follows the History setting.
    vb.setMouseEnabled(x=True, y=False)

    img = pg.ImageItem()
    img.setColorMap(WATERFALL_CMAP)
    img.setZValue(0)
    plot.addItem(img)
    markers = MarkersItem()
    markers.setZValue(10)
    plot.addItem(markers)
    layout.addWidget(plot, stretch=1)

    span_s = spin.value() * 60.0
    wf = getattr(self, "_band_wf", None)
    if wf is None:
        wf = BandWaterfall(span_s)
        self._band_wf = wf
    else:
        wf.set_span(span_s)

    win._ba_eng = self
    win._ba_status = status
    win._ba_wf_cb = wf_cb
    win._ba_det_cb = det_cb
    win._ba_spin = spin
    win._ba_legend = legend
    win._ba_plot = plot
    win._ba_img = img
    win._ba_markers = markers
    win._ba_wf = wf
    win._ba_store = DetectionStore(span_s)
    win._ba_regions = []
    win._ba_subbands = []
    win._ba_subband_key = None
    win._ba_x_key = None
    win._ba_last_res = {}
    win._ba_highlight = set()
    win._ba_wf_version = None

    def on_wf(on):
        _settings().setValue(WATERFALL_KEY, bool(on))
        _update_wanted(win)
        redraw(win)

    def on_det(on):
        _settings().setValue(DETECTIONS_KEY, bool(on))
        redraw(win)

    def on_minutes(n):
        n = clamp_minutes(n)
        _settings().setValue(MINUTES_KEY, n)
        win._ba_wf.set_span(n * 60.0)
        win._ba_store.set_span(n * 60.0)
        redraw(win)

    wf_cb.toggled.connect(on_wf)
    det_cb.toggled.connect(on_det)
    spin.valueChanged.connect(on_minutes)
    plot.scene().sigMouseMoved.connect(lambda pos: _hover(win, pos))

    self._band_activity_win = win
    win.hide()
    _update_wanted(win)               # record from the start, window open or not
    timer = QtCore.QTimer(win)
    timer.setInterval(TIMER_MS)
    timer.timeout.connect(lambda: tick(self, win))
    timer.start()
    win._ba_timer = timer
    return win


def _update_wanted(win) -> None:
    """The engine records waterfall rows whenever Waterfall is ticked, even
    while the window is hidden, minimised or being moved, so the picture is
    still there when it comes back.  A gap while unticked shows as blank rows."""
    wf = getattr(win, "_ba_wf", None)
    if wf is None:
        return
    wf.wanted = bool(win._ba_wf_cb.isChecked())


def set_highlighted_calls(win, calls) -> None:
    """Calls whose markers get a black outline (hook for a 'needed' list)."""
    win._ba_highlight = {str(c).upper() for c in (calls or []) if str(c).strip()}
    redraw(win)


def note_decode_record(win, rec: dict) -> None:
    """Main decode queue → MSK144 detections (GUI thread)."""
    if win is None:
        return
    try:
        win._ba_store.add(detection_from_record(rec))
    except Exception:
        pass


def _latest_results(eng) -> dict:
    out = dict(getattr(eng, "_ft8_results", None) or {})
    res = getattr(eng, "_ft8_result", None)
    if res is not None and res.mode not in out:
        out[res.mode] = res
    return out


def tick(eng, win, now: float | None = None) -> None:
    """Once a second: collect FT8/FT4 periods; repaint while visible."""
    try:
        for mode, res in _latest_results(eng).items():
            if res is win._ba_last_res.get(mode):
                continue
            win._ba_last_res[mode] = res
            win._ba_store.add_period_result(res)
        if win.isVisible():
            redraw(win, now=now)
        else:
            win._ba_store.trim(now)
    except Exception as e:                       # never let a bad frame kill the panel
        win._ba_status.setText(f"redraw error: {e}")


def _now(win, snap, now: float | None) -> float:
    if now is not None:
        return float(now)
    eng = getattr(win, "_ba_eng", None)
    if getattr(eng, "source_mode", "") == "wav":
        # Replay: follow the file's clock, not the wall clock.
        cands = [x for x in (snap[2] if snap else None, win._ba_store.newest())
                 if x is not None]
        if cands:
            return max(cands)
    return time.time()


def _x_span(win, snap) -> tuple[float, float] | None:
    if snap is not None:
        return snap[3], snap[4]
    span = win._ba_wf.span_mhz()
    if span is not None:
        return span
    eng = getattr(win, "_ba_eng", None)
    center = getattr(eng, "center_freq_mhz", None)
    fs = getattr(eng, "sample_rate", None)
    try:
        if center and fs:
            return float(center) - float(fs) / 2e6, float(center) + float(fs) / 2e6
    except (TypeError, ValueError):
        pass
    items = win._ba_store.items
    if items:
        lo = min(d.f_lo_mhz for d in items)
        hi = max(d.f_hi_mhz for d in items)
        pad = max(0.001, 0.05 * (hi - lo))
        return lo - pad, hi + pad
    return None


def redraw(win, now: float | None = None) -> None:
    show_wf = win._ba_wf_cb.isChecked()
    show_det = win._ba_det_cb.isChecked()
    snap = win._ba_wf.snapshot() if show_wf else None
    t_top = _now(win, snap, now)
    span_s = win._ba_spin.value() * 60.0
    win._ba_store.trim(t_top)

    if snap is not None:
        rows, t0, t1, f0, f1 = snap
        lo, hi = color_levels(rows)
        # ImageItem: first index = x (frequency), second = y (time, up).
        win._ba_img.setImage(rows.T, autoLevels=False, levels=(lo, hi))
        win._ba_img.setRect(QtCore.QRectF(f0, t0, f1 - f0, t1 - t0))
        win._ba_img.show()
    else:
        win._ba_img.hide()

    if show_det:
        win._ba_markers.set_detections(win._ba_store.items, win._ba_highlight,
                                       translucent=show_wf)
        win._ba_markers.show()
    else:
        win._ba_markers.set_detections([])
        win._ba_markers.hide()

    xs = _x_span(win, snap)
    vb = win._ba_plot.getViewBox()
    vb.disableAutoRange()
    vb.setYRange(t_top - span_s, t_top, padding=0)
    if xs is not None:
        key = (round(xs[0], 6), round(xs[1], 6))
        if key != win._ba_x_key:                 # new band/span: show it all
            vb.setXRange(xs[0], xs[1], padding=0)
            win._ba_x_key = key
        _sync_subbands(win, xs)
    _update_status(win, snap, show_wf, show_det)


def _sync_subbands(win, xs) -> None:
    key = (round(xs[0], 6), round(xs[1], 6))
    if key == win._ba_subband_key:
        return
    win._ba_subband_key = key
    bands = subbands(*xs)
    regions = win._ba_regions
    for i, (mode, lo, hi, _dial) in enumerate(bands):
        r, g, b = mode_color(mode)
        if i < len(regions):
            reg = regions[i]
            reg.setRegion((lo, hi))
        else:
            reg = pg.LinearRegionItem(values=(lo, hi), orientation="vertical",
                                      movable=False, pen=pg.mkPen(None))
            reg.setZValue(2)
            win._ba_plot.addItem(reg)
            regions.append(reg)
        reg.setBrush(pg.mkBrush(r, g, b, SUBBAND_ALPHA))
        reg.show()
    for reg in regions[len(bands):]:
        reg.hide()
    win._ba_subbands = bands


def _update_status(win, snap, show_wf: bool, show_det: bool) -> None:
    parts = []
    if show_det:
        c = win._ba_store.counts()
        calls = {d.call for d in win._ba_store.items if d.call}
        parts.append(" · ".join(f"{m} {c.get(m, 0)}" for m in MODE_ORDER if c.get(m, 0))
                     or "no decodes yet")
        parts.append(f"{len(calls)} stations in {win._ba_spin.value()} min")
    if show_wf:
        parts.append("waterfall: waiting for the radio…" if snap is None
                     else f"waterfall {snap[3]:.3f}–{snap[4]:.3f} MHz")
    if not parts:
        parts.append("Tick Waterfall or Detections to show the band")
    win._ba_status.setText("  ·  ".join(parts))


def subband_at(win, f_mhz: float):
    for mode, lo, hi, dial in getattr(win, "_ba_subbands", []):
        if lo <= f_mhz <= hi:
            return mode, dial
    return None


def tooltip_at(win, f_mhz: float, t: float, px_mhz: float, py_s: float) -> str | None:
    """Text for the mouse at (``f_mhz``, ``t``): a marker, else a sub-band."""
    if win._ba_det_cb.isChecked():
        d = win._ba_store.nearest(f_mhz, t, f_tol_mhz=4.0 * px_mhz, t_tol_s=3.0 * py_s)
        if d is not None:
            return d.tooltip()
    sb = subband_at(win, f_mhz)
    if sb is not None:
        return f"{sb[0]} sub-band (dial {sb[1]:.4f} MHz)"
    return None


def _hover(win, scene_pos) -> None:
    try:
        vb = win._ba_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            QtWidgets.QToolTip.hideText()
            return
        p = vb.mapSceneToView(scene_pos)
        px, py = vb.viewPixelSize()
        text = tooltip_at(win, p.x(), p.y(), float(px), float(py))
        if text is None:
            QtWidgets.QToolTip.hideText()
            return
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), text, win._ba_plot)
    except Exception:
        pass
