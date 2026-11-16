"""
Durability + exit logic (v1.51) — "what lasts, and what is dying" instead of only "what is rising".

Why this exists. A population-level test of 17 days of HISTORY (v1.50 review, ~800 items) found:
  * momentum (climbing today) predicted almost nothing about being on the list a week later — it is mostly noise
  * persistence (already on the list most days) predicted staying on the list strongly
  * a product that has just FALLEN is the one that leaves next
So the daily score ranked the least predictive signal first. This module measures the predictive one and
turns the third into an early-exit warning. Everything here is derived from our own HISTORY only.

What it does NOT claim: durable ≠ profitable. A product that sits in the top 100 for weeks is also the one
every other seller already sells (crowded), and rank is not sales. It says "demand has been present", nothing about margin.

Three pure functions, no I/O, nothing random:

  durability(hist, today)   per ASIN: days present, median rank, stability, score, durable flag, plain-words note
  exit_flags(hist, today)   per ASIN: 'falling' when it slid ≥ FALL places in the last LOOKBACK days
  fader_rates(hist)         observed base rates: how often did a faller / a steady item leave the list within a week?
"""
import pandas as pd

WINDOW = 14           # days looked back
MIN_WINDOW = 7        # fewer observed days than this → nothing is called durable (not enough evidence)
PRESENCE = 0.80       # share of observed days on the list to count as durable
MAX_MEDIAN_RANK = 70  # a long stay at #95 is not demand
LOOKBACK = 3          # exit flag compares today with this many observed days ago
FALL = 10             # places lost
MIN_BASE_N = 30       # base rates are shown only with at least this many item-days per cell
BANDS = ((1, 30), (31, 60), (61, 100))


def _bs(hist, today=None):
    """Bestseller rows → asin, category_slug, date, rank (best rank per asin/day). Rows after `today` are dropped."""
    cols = ["asin", "category_slug", "date", "rank"]
    if hist is None or len(hist) == 0 or "list" not in hist:
        return pd.DataFrame(columns=cols)
    b = hist[hist["list"] == "bestsellers"][["asin", "category_slug", "date", "rank"]].copy()
    b["rank"] = pd.to_numeric(b["rank"], errors="coerce")
    b = b.dropna(subset=["rank"])
    b["date"] = b["date"].astype(str)
    if today is not None:
        b = b[b["date"] <= str(today)]
    return b.groupby(["asin", "category_slug", "date"], as_index=False)["rank"].min()


def durability(hist, today, window=WINDOW):
    """→ DataFrame indexed by asin (only ASINs on the bestseller list `today`):
       durable_days   days on the list inside the window
       durable_window days the window actually covers for that ASIN's category (a missed fetch is not a miss)
       presence       durable_days / durable_window
       median_rank, rank_std
       durable_score  0-100: 60% presence + 25% rank strength + 15% rank stability
       durable        True when presence ≥ PRESENCE, median rank ≤ MAX_MEDIAN_RANK, on the list today and the
                      window covers ≥ MIN_WINDOW observed days
       durable_note   'on the list 13 of the last 14 days · median #22'"""
    cols = ["durable_days", "durable_window", "presence", "median_rank", "rank_std", "durable_score", "durable", "durable_note"]
    b = _bs(hist, today)
    if b.empty or str(today) not in set(b["date"]):
        return pd.DataFrame(columns=cols)
    all_days = sorted(b["date"].unique())
    win = all_days[-window:]
    b = b[b["date"].isin(win)]
    fetched = b.groupby("category_slug")["date"].apply(set)           # days each category's list was observed at all
    cat_days = fetched.map(len)
    t = b[b["date"] == str(today)].set_index("asin")
    rows = []
    for asin, g in b.groupby("asin"):
        if asin not in t.index:
            continue
        cat = t.loc[asin, "category_slug"]
        if isinstance(cat, pd.Series):
            cat = cat.iloc[0]
        mine = g.groupby("date")["rank"].min()
        n_days = int(len(mine))
        n_win = int(cat_days.get(cat, len(win)))
        n_win = max(n_win, n_days, 1)
        pres = n_days / n_win
        med = float(mine.median())
        sd = float(mine.std(ddof=0)) if n_days > 1 else 0.0
        strength = max(0.0, min(1.0, 1 - (med - 1) / 99))
        stab = max(0.0, min(1.0, 1 - sd / 30))
        score = 100 * (0.60 * pres + 0.25 * strength + 0.15 * stab)
        ok = pres >= PRESENCE and med <= MAX_MEDIAN_RANK and n_win >= MIN_WINDOW
        rows.append({"asin": asin, "durable_days": n_days, "durable_window": n_win, "presence": round(pres, 2),
                     "median_rank": round(med, 1), "rank_std": round(sd, 1), "durable_score": round(score, 1),
                     "durable": bool(ok),
                     "durable_note": f"on the list {n_days} of the last {n_win} days · median #{int(round(med))}"})
    return pd.DataFrame(rows, columns=["asin"] + cols).set_index("asin")


def exit_flags(hist, today, lookback=LOOKBACK, fall=FALL):
    """→ DataFrame indexed by asin: exit_flag ('falling' | ''), exit_note, rank_now, rank_then.
    'falling' = on the list today and ≥ `fall` places WORSE than `lookback` observed days ago (and was on the list then).
    A falling product is the early part of leaving: the v1.50 population test found fallers leaving far more often than steady items."""
    cols = ["exit_flag", "exit_note", "rank_now", "rank_then"]
    b = _bs(hist, today)
    if b.empty or str(today) not in set(b["date"]):
        return pd.DataFrame(columns=cols)
    days = sorted(b["date"].unique())
    if len(days) <= lookback:
        return pd.DataFrame({"exit_flag": "", "exit_note": "", "rank_now": float("nan"), "rank_then": float("nan")},
                            index=b[b["date"] == str(today)]["asin"].unique()).rename_axis("asin")
    ref = days[-1 - lookback]
    now = b[b["date"] == str(today)].groupby("asin")["rank"].min()
    then = b[b["date"] == ref].groupby("asin")["rank"].min()
    out = pd.DataFrame({"rank_now": now}).join(then.rename("rank_then"))
    drop = out["rank_now"] - out["rank_then"]
    out["exit_flag"] = ["falling" if pd.notna(d) and d >= fall else "" for d in drop]
    out["exit_note"] = [f"fell #{int(t)} → #{int(n)} in {lookback} days" if f else ""
                        for f, t, n in zip(out["exit_flag"], out["rank_then"].fillna(0), out["rank_now"])]
    return out[cols].rename_axis("asin")


def _band(rank):
    for lo, hi in BANDS:
        if lo <= rank <= hi:
            return f"#{lo}-{hi}"
    return ""


def fader_rates(hist, lookback=LOOKBACK, fall=FALL, horizon=7):
    """Observed base rates from our own history: for every (asin, day) that was on the list and had a reading
    `lookback` days earlier and a verdict `horizon` days later — did it stay on the list?
    → DataFrame: band, state ('falling' | 'steady'), n, left, left_rate.
    Item-days are strongly autocorrelated (the same product counts on consecutive days), so these are descriptive
    rates, not a statistical test. Cells under MIN_BASE_N are returned with show=False."""
    cols = ["band", "state", "n", "left", "left_rate", "show"]
    b = _bs(hist)
    if b.empty:
        return pd.DataFrame(columns=cols)
    days = sorted(b["date"].unique())
    pos = {d: i for i, d in enumerate(days)}
    lst = {(a, d): r for a, d, r in zip(b["asin"], b["date"], b["rank"])}
    cat_days = {(c, d) for c, d in zip(b["category_slug"], b["date"])}
    recs = []
    for a, c, d, r in zip(b["asin"], b["category_slug"], b["date"], b["rank"]):
        i = pos[d]
        if i < lookback or i + horizon >= len(days):
            continue
        d0, d1 = days[i - lookback], days[i + horizon]
        r0 = lst.get((a, d0))
        if r0 is None or (c, d1) not in cat_days:          # no earlier reading, or the later list was not observed
            continue
        state = "falling" if r - r0 >= fall else "steady"
        recs.append({"band": _band(r), "state": state, "left": int((a, d1) not in lst)})
    if not recs:
        return pd.DataFrame(columns=cols)
    g = pd.DataFrame(recs).groupby(["band", "state"])["left"].agg(["count", "sum"]).reset_index()
    g.columns = ["band", "state", "n", "left"]
    g["left_rate"] = (g["left"] / g["n"]).round(3)
    g["show"] = g["n"] >= MIN_BASE_N
    return g[cols]


def fader_line(rates):
    """One plain sentence for the digest / brief, or '' when no cell has enough item-days."""
    if rates is None or len(rates) == 0:
        return ""
    r = rates[rates["show"]]
    f, s = r[r["state"] == "falling"], r[r["state"] == "steady"]
    if f.empty or s.empty:
        return ""
    nf, ns = int(f["n"].sum()), int(s["n"].sum())
    lf, ls = int(f["left"].sum()), int(s["left"].sum())
    return (f"Observed here: after a fall of {FALL}+ places in {LOOKBACK} days, {lf / nf:.0%} of products were off the "
            f"top 100 a week later (n={nf} item-days) vs {ls / ns:.0%} of steady ones (n={ns}). Item-days overlap — descriptive, not a proof.")
