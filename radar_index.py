"""
Radar index — v1.60. The daily file the shopper bot answers from (cloudflare_worker/worker.js).

Written by the daily run to output/bot/radar_index.json; the archive step pushes it to the data-archive branch as
bot/radar_index.json (beside archive/, which holds only dated folders), where the Worker reads it with its GitHub token.

Per ASIN seen on a bestseller list in the last 14 days (compact arrays keep the file small):
  t  short title          c  category slug        r  rank today (null if not on the list today)
  r7 rank 7 observed days ago (null if not there) d  days on a list in the window   w  days in the window
  s  state: durable | climbing | falling | steady | new | gone     l  last date seen
  x  (falling only) the exit note, e.g. 'fell #15 → #30 in 3 days'
  k  (v1.81, sellers) certification to check before selling, e.g. 'BIS (toys QCO)'; absent when none flagged
  g  (v1.81, sellers) weight in kg read from the title, for the bot's /sell and /margin; absent when unknown
Plus `top`: per category, up to 5 durable, in-stock, not-falling ASINs (for /top), and `cats`: slug → name.

Only facts we observed. No prices (Associates rules) and no forecasts.
"""
import json
import os
import re

import pandas as pd

import fees

WINDOW = 14


def _short(t, n=80):
    s = re.split(r"\s[|–]\s|,\s|\s\(", str(t or ""))[0].strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def build(signals, hist, today, cat_names=None, window=WINDOW, version=""):
    b = hist[hist["list"] == "bestsellers"][["asin", "category_slug", "date", "rank", "title"]].copy() if hist is not None and len(hist) else pd.DataFrame()
    out = {"date": str(today), "version": version, "window": window, "items": {}, "top": {}, "cats": dict(cat_names or {})}
    if b.empty:
        return out
    b["rank"] = pd.to_numeric(b["rank"], errors="coerce")
    b = b.dropna(subset=["rank"])
    b["date"] = b["date"].astype(str)
    b = b[b["date"] <= str(today)]
    days = sorted(b["date"].unique())[-window:]
    b = b[b["date"].isin(days)]
    if b.empty:
        return out
    ref7 = days[-8] if len(days) >= 8 else None
    best = b.groupby(["asin", "date"])["rank"].min()
    sig = signals.set_index("asin") if signals is not None and len(signals) and "asin" in signals else pd.DataFrame()
    titles = b.dropna(subset=["title"]).sort_values("date").groupby("asin")["title"].last()
    cats = b.sort_values("date").groupby("asin")["category_slug"].last()
    cat_days = b.groupby("category_slug")["date"].nunique()
    for asin, g in best.groupby(level=0):
        g = g.droplevel(0)
        r = int(g.get(str(today))) if str(today) in g.index else None
        r7 = int(g.get(ref7)) if ref7 and ref7 in g.index else None
        cat = cats.get(asin, "")
        w = int(min(len(days), cat_days.get(cat, len(days))))
        d = int(len(g))
        srow = sig.loc[asin] if len(sig) and asin in sig.index else None
        if isinstance(srow, pd.DataFrame):
            srow = srow.iloc[0]
        dur = srow is not None and str(srow.get("durable", "")).lower() == "true"
        fall = srow is not None and str(srow.get("exit_flag", "")) == "falling"
        nonp = srow is not None and str(srow.get("non_product", "") or "") not in ("", "nan")
        if nonp:
            continue
        if r is None:
            state = "gone"
        elif fall:
            state = "falling"
        elif dur:
            state = "durable"
        elif r7 is None:
            state = "new"
        elif r7 - r >= 10:
            state = "climbing"
        else:
            state = "steady"
        out["items"][asin] = {"t": _short(titles.get(asin, "")), "c": cat, "r": r, "r7": r7, "d": d, "w": w,
                              "s": state, "l": g.index.max()}
        if state == "falling" and srow is not None and str(srow.get("exit_note", "") or "") not in ("", "nan"):
            out["items"][asin]["x"] = str(srow.get("exit_note"))           # 'fell #15 → #30 in 3 days' (durable.py)
        comp = str(srow.get("compliance", "") or "") if srow is not None else ""
        if comp.startswith("REVIEW"):
            out["items"][asin]["k"] = comp.replace("REVIEW:", "").split(";")[0].split("—")[0].strip()[:40]
        kg = fees.weight_kg(titles.get(asin, ""))
        if kg is not None:
            out["items"][asin]["g"] = kg
    if len(sig):
        s = signals.copy()
        s = s[s["durable"].astype(str).str.lower() == "true"] if "durable" in s else s.iloc[0:0]
        for col, bad in (("exit_flag", "falling"), ("stock_state", "unavailable"), ("stock_state", "unknown")):
            if col in s:
                s = s[s[col].fillna("").astype(str) != bad]
        if "durable_score" in s:
            s = s.assign(_d=pd.to_numeric(s["durable_score"], errors="coerce")).sort_values("_d", ascending=False)
        for cat, g in (s.groupby("category_slug") if "category_slug" in s else []):
            out["top"][cat] = [a for a in g["asin"] if a in out["items"]][:5]
    return out


def write(index, out_dir):
    path = os.path.join(out_dir, "bot", "radar_index.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
    return path
