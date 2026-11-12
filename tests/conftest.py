# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Pytest hooks for the WS Skim test suite."""

from __future__ import annotations

import logging
import threading

import pytest

log = logging.getLogger(__name__)

_WORKER_NAME_MARKERS = ("tfmf-period", "extract_and_decode")


@pytest.fixture(autouse=True)
def _join_engine_background_workers():
    """After each test, join Engine TFMF / decode workers left running."""
    yield
    from ws_skim.engine import stop_all_background_workers
    stop_all_background_workers(timeout_s=15.0)


def _live_worker_threads() -> list[threading.Thread]:
    return [
        t for t in threading.enumerate()
        if t is not threading.main_thread()
        and any(mark in t.name for mark in _WORKER_NAME_MARKERS)
        and t.is_alive()
    ]


def pytest_sessionfinish(session, exitstatus):
    # Last-chance join in case a test created workers without an Engine
    # instance in the live set. Then report anything still running so a
    # shutdown abort has a clear cause in the log.
    from ws_skim.engine import stop_all_background_workers
    stop_all_background_workers(timeout_s=15.0)
    leftover = _live_worker_threads()
    if leftover:
        names = ", ".join(f"{t.name}(daemon={t.daemon})" for t in leftover)
        log.warning(
            "pytest_sessionfinish: %d leftover worker thread(s): %s",
            len(leftover), names,
        )
        print(
            f"\nWARNING: leftover worker threads at session end: {names}",
            flush=True,
        )
