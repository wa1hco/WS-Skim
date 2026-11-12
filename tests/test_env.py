# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""WS_SKIM_* environment variables, with the old MAP144_* names as a fallback."""
from ws_skim import env


def test_new_name_wins(monkeypatch):
    monkeypatch.setenv("WS_SKIM_JT9", "/new/jt9")
    monkeypatch.setenv("MAP144_JT9", "/old/jt9")
    assert env.getenv("JT9") == "/new/jt9"


def test_old_name_still_works(monkeypatch):
    monkeypatch.delenv("WS_SKIM_JT9", raising=False)
    monkeypatch.setenv("MAP144_JT9", "/old/jt9")
    assert env.getenv("JT9") == "/old/jt9"


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("WS_SKIM_JT9", raising=False)
    monkeypatch.delenv("MAP144_JT9", raising=False)
    assert env.getenv("JT9", "dflt") == "dflt"
    assert env.getenv("JT9") is None


def test_empty_new_value_is_used(monkeypatch):
    # An explicitly empty WS_SKIM_ value is a value, not "unset".
    monkeypatch.setenv("WS_SKIM_USE_BLOCKS", "")
    monkeypatch.setenv("MAP144_USE_BLOCKS", "1")
    assert env.getenv("USE_BLOCKS") == ""
