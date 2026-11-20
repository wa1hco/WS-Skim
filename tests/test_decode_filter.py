"""The FT8 / FT4 false-decode filter (ws_skim/decode_filter.py)."""
import json

import pytest

from ws_skim import decode_filter as df
from ws_skim import ft8_decode as fd
from ws_skim import ft8_engine


def _d(msg, snr=-10, rf=14_075_500.0, ap=0, mode="FT8"):
    return fd.Decode(mode=mode, period_start_utc=1_795_159_530.0, snr_db=snr, dt_s=0.2,
                     audio_hz=rf - 14_074_000.0, rf_hz=rf, dial_hz=14_074_000.0,
                     message=msg, ap_type=ap)


def _reason(msg, **kw):
    r = df.check_decode(_d(msg, **kw))
    return r[0] if r else None


# Junk from WS Skim's own 40 m / 20 m output (2026-11-19/20): random calls,
# unallocated prefixes, grids on the wrong continent, jt9 default calls.
JUNK = [
    ("B30LWF Y27VQU LP17", "call"),        # Y2: not an allocated prefix
    ("VD5WOL OH0BKN GK67", "grid"),        # Aland Islands in the S Atlantic
    ("K1ABC <...> RR26", "ap"),
    ("W9XYZ K1ABC -12", "ap"),
    ("TN3ZTW WY4EHT/P R HH02", "grid"),
    ("<...> 9G/KUVJX98Y RRR", "call"),
    ("TN0ZSP/P 9K3BGE/P NP72", "grid"),
    ("CQ 3DZU0XAE613", "call"),
    ("0R4ZGT ZN3EHF/P MI53", "call"),
    ("CQ FT2LN EI50", "grid"),
    ("CQ MI3UPT HH91", "grid"),
    ("KW8JBS 9V0TZQ R CN37", "grid"),
    ("CQ AZ5UOP ME24", "grid"),
    ("CQ 5KT0AIXIYX4", "call"),
    ("H30HZY/P 2Z0MYB/P R AQ47", "call"),
    ("CQ SA3ZBC RN57", "grid"),
    ("LD6NWN BM5VTI HP91", "grid"),
    ("EF2ORI SN1MUB DJ58", "grid"),
    ("D2IAC V7TVN EL19", "grid"),
    ("E13SNY EE9YMJ RI04", "grid"),
    ("6B5QTJ/P Z91FKU/P PH98", "call"),
    ("MT3BSS 7X6CMM R DK64", "grid"),
    ("O63VYK UH8WWK/P PI93", "grid"),
    ("H13TQG SR2NOC R DN02", "grid"),
    ("VN9ANN V5LSX ER11", "grid"),
    ("U72ZZT 4M5UQR R CG95", "grid"),
    ("CQ C28UVD IQ23", "grid"),
    ("RP9PSY JH5CYA BN10", "grid"),
    ("Z55OHH 4N7PKH R FC01", "call"),
    ("CQ VN0IKB EQ17", "call"),
    ("7U3BRM/P A25IQE/P R FI52", "grid"),
    ("CQ GB5OOQ AP49", "grid"),
    ("CQ 6B3ARW IR58", "grid"),
    ("8X1RQB/P NS7IZM R GI61", "grid"),
]

# Real traffic (from WSJT-X and WS Skim on the same bands) that must pass.
REAL = [
    "CQ ON2OD JO20", "CT1DZY W5AJ -14", "W5AJ CT1DZY R-09", "CT1DZY W5AJ RR73",
    "W5AJ CT1DZY 73", "CQ K1JT FN20", "CQ DX JA1XYZ PM95", "CQ POTA N1MM FN42",
    "CQ NA VE3XYZ FN03", "CQ 290 W1AW FN31", "CQ 210WAI", "CQ PC40EUDXF",
    "PC40EUDXF W1AW FN31", "CQ NL7FJ FN32", "CQ UE0DX PN53", "WA4CB KB6DAN DB24", "CQ 3D2AG RH91", "CQ 9A1A JN75",
    "<K1ABC> W1AW/5", "<...> W1AW RR73", "W1AW <PJ4/K1XX> -05", "CQ PJ4/K1XX FK52",
    "CQ KH6/W1AW BL11", "CQ W1AW/MM IL38", "CQ KH8/N6XX AH45", "CQ NH6Y BL01",
    "CQ VK2ABC QF56", "CQ VK6ABC OF78", "CQ ZL1ABC RF72", "CQ PY2XYZ GG66",
    "CQ LU1ABC GF05", "CQ UA9XYZ MO06", "CQ RA0FF QN16", "CQ BY1RX OM89",
    "CQ JA1ABC PM95", "CQ 4X1ABC KM72", "CQ ZS6ABC KG33", "CQ EA8ABC IL18",
    "CQ VP8LP GD18", "CQ G4ABC IO91", "CQ DL1ABC JO62", "CQ F5ABC JN18",
    "Y95OWI KC7UG -25", "X78XHA W2RWC -16",            # real station, miscopied call
    "K1JT RR73; W9ABC <TO1XX> -08", "W1AW RR73; K2XX <3Y0J> +02",
    "TNX 73 GL", "HELLO WORLD", "CQ CQ CQ", "73 ES GL OM",
    "W1AW K1JT R+05", "W1AW K1JT RRR", "W1AW K1JT RR73", "W1AW K1JT 73",
    "CQ VE7ABC CN89", "CQ VY0ABC EQ79", "CQ VK0ABC QD95",
]


@pytest.mark.parametrize("msg,reason", JUNK)
def test_junk_is_rejected(msg, reason):
    assert _reason(msg) == reason


@pytest.mark.parametrize("msg", REAL)
def test_real_messages_kept(msg):
    assert df.check_decode(_d(msg)) is None, df.check_decode(_d(msg))


def test_ap_partner_decodes_rejected_cq_ap_kept():
    assert _reason("W1AW K1JT -05", ap=2) == "ap"
    assert _reason("W1AW K1JT RR73", ap=4) == "ap"
    assert _reason("CQ K1JT FN20", ap=1) is None


def test_rr73_is_not_a_grid():
    p = df.parse_message("W1AW K1JT RR73")
    assert p and p.grid is None
    assert df.parse_message("W1AW K1JT FN20").grid == "FN20"
    assert df.parse_message("TNX 73 GL") is None


def test_split_call_and_lookup():
    cty = df.country_file()
    assert df.split_call("PJ4/K1XX")[1] == "PJ4"
    assert df.split_call("W1AW/MM")[2] is True
    assert cty.lookup("KH6/W1AW").name == "Hawaii"
    assert cty.lookup("W1AW").name == "United States"
    assert cty.lookup("DL1ABC").name == "Fed. Rep. of Germany"


def test_dup_merge_keeps_best_snr():
    a = _d("CQ TJ1GD JJ53", snr=-14, rf=14_075_262.0)
    b = _d("CQ TJ1GD JJ53", snr=-9, rf=14_075_362.0)
    c = _d("CQ K1JT FN20", snr=-3)
    kept, rej = df.filter_period([a, b, c])
    assert kept == [b, c]
    assert [(x.decode, x.reason) for x in rej] == [(a, "dup")]


def test_summary_line():
    ds = [_d("W9XYZ K1JT -10"), _d("CQ 3DZU0XAE613"), _d("CQ K1JT FN20"),
          _d("CQ K1JT FN20", snr=-20)]
    kept, rej = df.filter_period(ds)
    assert len(kept) == 1
    assert df.summary(rej) == "filtered 3: ap 1, call 1, grid 0, dup 1"


def test_jt9_ap_annotation_parsed():
    out = ("000000 -21 -0.4  850 ~  W1AW K1JT RR73                       a2\n"
           "000000 -12  1.1 2010 ~  CQ JA1XYZ PM95                        a1\n"
           "000000  -5  0.3 1200 ~  CQ K1JT FN20\n")
    got = fd.parse_jt9_full(out)
    assert [(g[3], g[5]) for g in got] == [("W1AW K1JT RR73", 2), ("CQ JA1XYZ PM95", 1),
                                           ("CQ K1JT FN20", 0)]
    assert [g[:5] for g in got] == fd.parse_jt9_ex(out)


def test_jt9_runs_without_mycall_dxcall(tmp_path):
    from ws_skim import costas_search as cs
    sec = cs.Section(base_hz=0.0, low_audio_hz=280.0, high_audio_hz=3300.4, candidates=[])
    c = fd.jt9_command("jt9", "FT8", sec, tmp_path / "p.wav")
    assert c[c.index("-c") + 1] == "b" and c[c.index("-x") + 1] == "b"
    assert c[c.index("-d") + 1] == "3"


def test_filter_error_keeps_everything(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(df, "filter_period", boom)
    ds = [_d("CQ K1JT FN20")]
    assert ft8_engine.filter_decodes(ds) == (ds, [])


def test_rejected_jsonl_and_status_count(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_SKIM_OUTPUT_DIR", str(tmp_path))
    kept, rej = df.filter_period([_d("CQ 3DZU0XAE613"), _d("W1AW K1JT -05", ap=3)])
    assert kept == []
    ft8_engine.write_rejected(rej)
    recs = [json.loads(x) for x in (tmp_path / "rejected.jsonl").read_text().splitlines()]
    assert [(r["message"], r["reason"], r["ap"]) for r in recs] == [
        ("CQ 3DZU0XAE613", "call", ""), ("W1AW K1JT -05", "ap", "a3")]

    class E:
        pass
    e = E()
    ft8_engine._count_filtered(e, rej)
    assert ft8_engine.filter_text(e) == "2 filtered"
    assert ft8_engine.filter_detail(e) == "ap 1, call 1, grid 0, dup 0"


def test_append_bounded_rotates(tmp_path):
    p = tmp_path / "decodes.jsonl"
    ft8_engine.append_bounded(p, ["a" * 50], 40)
    ft8_engine.append_bounded(p, ["b"], 40)       # over 40 bytes: rotate first
    assert (tmp_path / "decodes.jsonl.1").read_text() == "a" * 50 + "\n"
    assert p.read_text() == "b\n"
    ft8_engine.append_bounded(p, ["c"], 40)
    assert p.read_text() == "b\nc\n"
