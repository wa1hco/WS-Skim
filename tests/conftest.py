# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Pytest hooks for the WS Skim test suite."""

from __future__ import annotations

import os

_exitstatus = 0


def pytest_sessionfinish(session, exitstatus):
    global _exitstatus
    _exitstatus = int(exitstatus)


def pytest_unconfigure(config):
    # Some tests leave C++/Qt/numba worker threads behind. During
    # interpreter shutdown those can call std::terminate ("terminate
    # called without an active exception") and make a fully passing
    # run exit 134 in GitHub Actions. On CI, skip that destructor
    # path and exit with pytest's already-determined status.
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    os._exit(_exitstatus)
