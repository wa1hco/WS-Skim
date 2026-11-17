# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Shared plot axis typography."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_style_plot_sets_label_and_tick_fonts(qapp):
    import pyqtgraph as pg
    from ws_skim.plot_style import AXIS_LABEL_PT, AXIS_LEFT_WIDTH, AXIS_TICK_PT, style_plot

    plot = pg.PlotWidget()
    plot.setLabel("left", "Frequency (MHz)")
    plot.setLabel("bottom", "Start time in period (s)")
    style_plot(plot)
    for name in ("left", "bottom"):
        axis = plot.getAxis(name)
        font = axis.style["tickFont"]
        assert font is not None and font.pointSize() == AXIS_TICK_PT
        assert axis.labelStyle.get("font-size") == f"{AXIS_LABEL_PT}pt"
    # setWidth stores a reserved width; the laid-out QGraphicsWidget width
    # stays at the default until the plot is shown.
    assert plot.getAxis("left").fixedWidth == AXIS_LEFT_WIDTH
    plot.resize(800, 500)
    plot.show()
    qapp.processEvents()
    assert plot.getAxis("left").width() == AXIS_LEFT_WIDTH

    # A later setLabel keeps the shared label size.
    plot.setLabel("left", "V  Freq (MHz)", color="k")
    axis = plot.getAxis("left")
    assert axis.labelStyle.get("font-size") == f"{AXIS_LABEL_PT}pt"
    assert axis.style["tickFont"].pointSize() == AXIS_TICK_PT
