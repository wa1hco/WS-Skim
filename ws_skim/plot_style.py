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
"""Shared pyqtgraph axis typography for WS Skim plots.

pyqtgraph's default tick and axis-label fonts are about 8–9 pt, which is
hard to read on a station display.  :func:`style_plot` applies one pair of
sizes to every plot window.
"""
from __future__ import annotations

from PyQt5.QtGui import QFont

#: Axis title (the "Frequency (MHz)" text), in points.
AXIS_LABEL_PT = 12
#: Tick numbers, in points.
AXIS_TICK_PT = 11
#: Left/right axis width so 11 pt ticks (and a 12 pt label) are not clipped.
AXIS_LEFT_WIDTH = 72
#: Bottom/top axis height for the same reason, when the axis is visible.
AXIS_BOTTOM_HEIGHT = 48
#: Collapsed-viewbox time/frequency ruler widget heights.
RULER_MIN_HEIGHT = 56
RULER_MAX_HEIGHT = 68
_TICK_TEXT_OFFSET = 8


def _label_font() -> QFont:
    font = QFont()
    font.setPointSize(AXIS_LABEL_PT)
    return font


def _tick_font() -> QFont:
    font = QFont()
    font.setPointSize(AXIS_TICK_PT)
    return font


def _apply_axis(axis, name: str) -> None:
    """Apply tick and label fonts to one AxisItem."""
    axis.setStyle(
        tickFont=_tick_font(),
        tickTextOffset=_TICK_TEXT_OFFSET,
        autoExpandTextSpace=True,
    )
    style = dict(getattr(axis, "labelStyle", {}) or {})
    style["font-size"] = f"{AXIS_LABEL_PT}pt"
    axis.labelStyle = style
    if getattr(axis, "label", None) is not None:
        axis.label.setFont(_label_font())
    update = getattr(axis, "_updateLabel", None)
    if callable(update):
        try:
            update()
        except Exception:
            pass
    if name in ("left", "right"):
        axis.setWidth(AXIS_LEFT_WIDTH)
    elif name in ("bottom", "top") and axis.isVisible():
        # Don't force height on a hidden bottom axis: data plots that share a
        # ruler below them keep a zero-height bottom row so the viewboxes
        # line up.
        if axis.height() < AXIS_BOTTOM_HEIGHT:
            axis.setHeight(AXIS_BOTTOM_HEIGHT)


def style_plot(plot) -> None:
    """Apply the standard axis label and tick fonts to a ``PlotWidget``.

    Later ``setLabel`` calls keep the 12 pt label size.  Safe to call more
    than once on the same plot.
    """
    for name in ("left", "right", "top", "bottom"):
        try:
            axis = plot.getAxis(name)
        except Exception:
            continue
        if axis is None:
            continue
        _apply_axis(axis, name)

    if not getattr(plot, "_ws_skim_styled", False):
        orig = plot.setLabel

        def setLabel(axis, text=None, units=None, unitPrefix=None, **args):
            args = dict(args)
            args.setdefault("font-size", f"{AXIS_LABEL_PT}pt")
            result = orig(axis, text=text, units=units, unitPrefix=unitPrefix, **args)
            # Re-apply tick font; setLabel can rebuild the axis label item.
            try:
                _apply_axis(plot.getAxis(axis), axis)
            except Exception:
                pass
            return result

        plot.setLabel = setLabel
        plot._ws_skim_styled = True
