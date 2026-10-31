#!/usr/bin/env python3
"""Unit tests for discount_rule.py — edge cases the hijacker guard must handle."""
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discount_rule import discount_status, reverted_after
ok = True
def check(n, c, d=""):
    global ok; ok &= bool(c); print(f"{'PASS' if c else 'FAIL'}  {n}  {d}")
T = pd.Timestamp("2025-03-10 23:59:59")
def S(pts): return pd.Series({pd.Timestamp(k): v for k, v in pts.items()}).sort_index()
R = lambda s, **k: discount_status(s, T, 15, 7, 48, **k)

check("flat price → none", R(S({"2025-03-05": 1000, "2025-03-10 10:00": 1000}))["status"] == "none")
check("3-day sustained 30% cut → sustained",
      R(S({"2025-03-05": 1000, "2025-03-08 09:00": 700, "2025-03-09 09:00": 700, "2025-03-10 10:00": 700}))["status"] == "sustained")
check("single 12h hijacker dip on score day → dip_unconfirmed",
      R(S({"2025-03-05": 1000, "2025-03-09": 1000, "2025-03-10 10:00": 500}))["status"] == "dip_unconfirmed")
check("ONE stale low observation carried forward is NOT sustained (Keepa gap)",
      R(S({"2025-03-04": 1000, "2025-03-07 10:00": 600}), fresh_hours=24 * 5)["status"] == "dip_unconfirmed")
check("two low obs only 20h apart → dip_unconfirmed",
      R(S({"2025-03-05": 1000, "2025-03-09 14:00": 600, "2025-03-10 10:00": 600}))["status"] == "dip_unconfirmed")
check("dip, recovery, dip again: run restarts (not sustained)",
      R(S({"2025-03-05": 1000, "2025-03-07": 600, "2025-03-08": 1000, "2025-03-10 10:00": 600}))["status"] == "dip_unconfirmed")
check("dip that already recovered before score → none",
      R(S({"2025-03-05": 1000, "2025-03-08": 500, "2025-03-10 10:00": 1000}))["status"] == "none")
check("latest obs too old → stale", R(S({"2025-03-01": 1000, "2025-03-08": 600}))["status"] == "stale")
check("10% cut is below 15% threshold → none",
      R(S({"2025-03-05": 1000, "2025-03-08": 900, "2025-03-10 10:00": 900}))["status"] == "none")
check("future data ignored (no look-ahead)",
      R(S({"2025-03-05": 1000, "2025-03-10 10:00": 500, "2025-03-11": 500, "2025-03-12": 500}))["status"] == "dip_unconfirmed")
check("post-hoc reversion detection", reverted_after(S({"2025-03-11 08:00": 1000}), T, 850) is True
      and reverted_after(S({"2025-03-11 08:00": 500}), T, 850) is False
      and reverted_after(S({"2025-03-20": 1000}), T, 850) is None)
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
