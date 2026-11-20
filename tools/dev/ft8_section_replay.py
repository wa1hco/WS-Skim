#!/usr/bin/env python3
# Copyright (C) 2026  Jeff Millar, WA1HCO <wa1hco@gmail.com>
# Licensed under GPL-3.0-or-later.
"""Before/after replay of the FT8/FT4 decode-history section filter.

Replays saved periods (Diagnostics -> Save FT8 period IQ writes them to
``<output>/ft8_iq/*.npz``) in time order through the Costas search twice:

* **before**: every candidate opens or joins a jt9 section (no history);
* **after**: the decode-history filter (``ft8_engine.DecodeHistory``), fed
  with the "after" decodes of the earlier periods, as the app runs it.

With jt9 available both section lists are decoded (at the depth the app
would pick for that many sections) and decodes are compared by message;
the "after" decodes also go through the false-decode filter
(``decode_filter``), and what it sets aside is listed and counted.
Without jt9 only candidates and sections are reported.

    python tools/dev/ft8_section_replay.py DIR [--jt9 PATH] [--limit N] [--dial MHZ]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ws_skim import decode_filter, ft8_decode, ft8_engine  # noqa: E402


def load(path: str) -> dict:
    d = np.load(path, allow_pickle=False)
    return {"iq": d["iq"], "fs": float(d["fs"]), "t0_s": float(d["t0_s"]),
            "p0": float(d["period_start_utc"]), "mode": str(d["mode"]),
            "center": float(d["center_freq_mhz"])}


def run(paths, jt9: str | None, dial_mhz: float | None, out=print) -> dict:
    hist = ft8_engine.DecodeHistory()
    rows = []
    out(f"{'period':>8} {'scan':>5} | {'cand':>4} {'sec':>3} {'d':>1} {'dec':>4} |"
        f" {'cand':>4} {'set':>4} {'sec':>3} {'d':>1} {'dec':>4} | lost gained  sections after (kHz)")
    for p in paths:
        r = load(p)
        dial_off = (dial_mhz - r["center"]) * 1e6 if dial_mhz else None
        kw = dict(t0_s=r["t0_s"], period_start_utc=r["p0"], center_freq_mhz=r["center"],
                  dial_offset_hz=dial_off)
        before = ft8_engine.search_period(r["iq"], r["fs"], r["mode"], **kw)
        after = ft8_engine.search_period(r["iq"], r["fs"], r["mode"], history=hist, **kw)
        row = {"p0": r["p0"], "full": after.full_scan,
               "b_cand": len(before.candidates), "b_sec": len(before.sections),
               "a_cand": len(after.candidates), "a_set": len(after.dropped_candidates),
               "a_sec": len(after.sections),
               "b_depth": ft8_decode.depth_for(len(before.sections)),
               "a_depth": ft8_decode.depth_for(len(after.sections))}
        if jt9:
            dec = {}
            for name, res, depth in (("b", before, row["b_depth"]), ("a", after, row["a_depth"])):
                t = time.perf_counter()
                ds = ft8_decode.decode_period(r["iq"], r["fs"], r["mode"], res.sections,
                                              t0_s=r["t0_s"], period_start_utc=r["p0"],
                                              center_freq_mhz=r["center"], jt9=jt9, depth=depth)
                row[f"{name}_jt9_s"] = time.perf_counter() - t
                dec[name] = ds
            mb = {d.message for d in dec["b"]}
            ma = {d.message for d in dec["a"]}
            row.update(b_dec=len(mb), a_dec=len(ma), lost=len(mb - ma), gained=len(ma - mb))
            row["a_raw"] = len(dec["a"])
            kept, rej = decode_filter.filter_period(dec["a"])
            row["a_kept"] = len(kept)
            for reason, n in decode_filter.counts(rej).items():
                row[f"f_{reason}"] = n
            row["filtered_detail"] = [(x.reason, x.decode.message, x.detail) for x in rej]
            row["lost_detail"] = sorted((d.rf_hz / 1e3, d.snr_db, d.message)
                                        for d in dec["b"] if d.message in mb - ma)
            k = int(np.floor(r["p0"] / ft8_engine.cs.MODES[r["mode"]].period_s + 1e-9))
            hist.observe(r["mode"], r["center"], k, kept)
        rows.append(row)
        secs = " ".join(f"{(r['center'] * 1e6 + s.base_hz) / 1e3:.1f}" for s in after.sections)
        hms = time.strftime("%H:%M:%S", time.gmtime(r["p0"]))
        out(f"{hms:>8} {'full' if row['full'] else 'hist':>5} | {row['b_cand']:4d} {row['b_sec']:3d}"
            f" {row['b_depth']:1d} {row.get('b_dec', '-'):>4} | {row['a_cand']:4d} {row['a_set']:4d}"
            f" {row['a_sec']:3d} {row['a_depth']:1d} {row.get('a_dec', '-'):>4} |"
            f" {row.get('lost', '-'):>4} {row.get('gained', '-'):>6}  {secs}")
        for khz, snr, msg in row.get("lost_detail", []):
            out(f"{'':>8}   lost: {khz:.3f} kHz {snr:+d} dB  {msg}")
        for reason, msg, detail in row.get("filtered_detail", []):
            out(f"{'':>8}   filtered ({reason}): {msg}  [{detail}]")
    return summarize(rows, out)


def summarize(rows, out=print) -> dict:
    if not rows:
        return {}
    def mean(key, sel=lambda r: True):
        v = [r[key] for r in rows if key in r and sel(r)]
        return float(np.mean(v)) if v else float("nan")
    s = {"periods": len(rows),
         "before_sections": mean("b_sec"), "after_sections": mean("a_sec"),
         "after_sections_filtered": mean("a_sec", lambda r: not r["full"]),
         "before_candidates": mean("b_cand"), "after_candidates": mean("a_cand"),
         "before_depth3": sum(r["b_depth"] == 3 for r in rows),
         "after_depth3": sum(r["a_depth"] == 3 for r in rows)}
    if "b_dec" in rows[0]:
        s.update(before_decodes=sum(r["b_dec"] for r in rows),
                 after_decodes=sum(r["a_dec"] for r in rows),
                 lost=sum(r["lost"] for r in rows), gained=sum(r["gained"] for r in rows),
                 before_jt9_s=mean("b_jt9_s"), after_jt9_s=mean("a_jt9_s"),
                 after_raw=sum(r.get("a_raw", 0) for r in rows),
                 after_kept=sum(r.get("a_kept", 0) for r in rows),
                 **{f"filtered_{k}": sum(r.get(f"f_{k}", 0) for r in rows)
                    for k in decode_filter.REASONS})
    out("summary: " + ", ".join(f"{k} {v:.2f}" if isinstance(v, float) else f"{k} {v}"
                                for k, v in s.items()))
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir")
    ap.add_argument("--jt9", default=None, help="jt9 path (default: find it; 'none' to skip)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dial", type=float, default=None, help="always-decoded dial (MHz)")
    a = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(a.dir, "*.npz")))
    if a.limit:
        paths = paths[:a.limit]
    jt9 = None
    if a.jt9 != "none":
        from ws_skim.detection import find_jt9
        jt9 = a.jt9 or find_jt9()
    print(f"{len(paths)} periods, jt9: {jt9 or 'not used'}")
    run(paths, jt9, a.dial)
    return 0


if __name__ == "__main__":
    sys.exit(main())
