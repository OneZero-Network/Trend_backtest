"""
Affiliate picks + forward test.

Every published pick is a dated prediction. Alongside each pick we record BASELINE products
drawn at random from the same category's bestseller list on the same day. Both are tracked
for 7 / 14 / 28 days. If picks don't beat the baseline, the trend score isn't working —
whatever the affiliate clicks say.

Pre-registered hit definition (do not change after seeing results):
  HIT at horizon h  =  product is still in its category's top-100 at pick_date + h days
                       AND its best rank that day is ≤ its rank on the pick date.
  Not observed (we have no history for that day yet) → pending, not a miss.

Posts deliberately contain NO prices (Amazon Associates rules restrict how prices may be shown
without the official API) and always end with the Associates disclosure line.
"""
import random
from datetime import datetime, timedelta
import pandas as pd

HORIZONS = (7, 14, 28)
BASELINE_PER_PICK = 3
MAX_PER_CATEGORY = 2
DISCLOSURE = "#ad · As an Amazon Associate I earn from qualifying purchases."


def affiliate_link(asin, tag):
    return f"https://www.amazon.in/dp/{asin}" + (f"?tag={tag}" if tag else "")


MIN_DAYS_ON_LIST = 3      # one-day entrants at the tail of the list are churn, not trends
MIN_RANK_GAIN = 10        # a pick must have actually climbed ≥10 places (week 1: a product that FELL was picked)


def _truthy(series):
    return series.astype(str).str.lower().isin(["true", "1", "1.0"])


def _hard_filters(s, rej, today, need_climb):
    """Filters EVERY public pick must pass, whichever arm it comes from (momentum or durable):
    not a bill-payment/voucher tile, never explosives, in stock (card + price, or priced in the last days),
    seasonal only inside its promotion window, not quarantined / discount-driven, and — v1.51 — not FALLING
    (a product that just lost 10+ places is the early part of leaving; the post must not call it 'climbing' or 'lasting').
    `need_climb`: the momentum arm additionally needs a real climb and ≥3 days on the lists."""
    import season
    import compliance
    _np = [compliance.non_product(t) for t in s["title"].fillna("").astype(str)] if "title" in s else []
    if any(_np):                                    # v1.51: "Credit Card Bill" sat at #1 in Bags & Luggage for 17 days
        rej += [(t, w) for t, w in zip(s["title"], _np) if w]
        s = s[[not w for w in _np]]
    if "compliance" in s:
        bad = s["compliance"].astype(str).str.contains("EXPLOSIVES")        # head-of-title rule lives in compliance.py
        rej += [(t, "explosives/firecracker") for t in s.loc[bad, "title"]]
        s = s[~bad]
    if need_climb and "rank_change" in s:
        rc = pd.to_numeric(s["rank_change"], errors="coerce")
        s = s[rc >= MIN_RANK_GAIN]
    if need_climb and "days_on_list_14d" in s and "bs_rank_today" in s:
        # 7–15 products enter each category's top 100 every day and most are gone the next (tail churn).
        # A climber counts only if it has been on the lists 3+ of the last 14 days, or entered high (top 30).
        days = pd.to_numeric(s["days_on_list_14d"], errors="coerce").fillna(0)
        rk = pd.to_numeric(s["bs_rank_today"], errors="coerce").fillna(999)
        s = s[(days >= MIN_DAYS_ON_LIST) | (rk <= 30)]
    if "exit_flag" in s:
        fall = s["exit_flag"].fillna("").astype(str) == "falling"
        if fall.any():
            notes = s["exit_note"].fillna("").astype(str) if "exit_note" in s else pd.Series([""] * len(s), index=s.index)
            rej += [(t, f"falling — {n or 'lost 10+ places in 3 days'}") for t, n in zip(s.loc[fall, "title"], notes[fall])]
            s = s[~fall]
    if "stock_state" in s:
        # v1.50: three different things used to share the label "out of stock" (see capture.py):
        #   unavailable → Amazon rendered the card and there was no price (9 Nov: 5 of 8 posted picks were like this)
        #   unknown     → tail-rank placeholder, no card seen today and no price in the last days: NOT verified
        #   carried     → tail-rank placeholder but priced within the last few days: allowed
        stt = s["stock_state"].astype(str)
        rej += [(t, "sold out on today's list (card shown, no price)") for t in s.loc[stt == "unavailable", "title"]]
        rej += [(t, "price not captured today (tail rank) and none in the last days → unverified, not posted")
                for t in s.loc[stt == "unknown", "title"]]
        s = s[~stt.isin(["unavailable", "unknown"])]
    elif "price_inr" in s:                                  # older signal tables without stock_state
        has_price = pd.to_numeric(s["price_inr"], errors="coerce").fillna(0) > 0
        rej += [(t, "no price on today's list → probably out of stock / unavailable") for t in s.loc[~has_price, "title"]]
        s = s[has_price]
    if "seasonal" in s and today is not None:
        keep = []
        for _, r in s.iterrows():
            w = season.window(r.get("seasonal", ""), today)
            keep.append(w["affiliate_ok"])
            if not w["affiliate_ok"]:
                rej.append((r["title"], w["note"]))
        s = s[keep] if len(s) else s
    for col, bad in (("actionable", False), ("discount_driven", True), ("short_dip", True)):
        if col in s:
            s = s[s[col].astype(str).str.lower() != str(bad).lower()]
    return s[s["title"].astype(str).str.len() > 3]


def _take(s, sort_cols, n, max_per_cat):
    """Best first, at most `max_per_cat` per category, up to n."""
    sort_cols = [c for c in sort_cols if c in s] or ["title"]
    s = s.assign(**{f"_k{i}": pd.to_numeric(s[c], errors="coerce").fillna(-1) for i, c in enumerate(sort_cols)})
    s = s.sort_values([f"_k{i}" for i in range(len(sort_cols))], ascending=False)
    out, per = [], {}
    for _, r in s.iterrows():
        cat = r.get("category_slug") or r.get("category")
        if per.get(cat, 0) >= max_per_cat:
            continue
        per[cat] = per.get(cat, 0) + 1
        out.append(r.drop(labels=[c for c in r.index if str(c).startswith("_k")]))
        if len(out) >= n:
            break
    return pd.DataFrame(out)


def select_picks(signals, n=8, max_per_cat=MAX_PER_CATEGORY, today=None, rejected=None):
    """MOMENTUM arm — top actionable, organic climbers with category diversity.
    Hard filters (reverse-logic fixes from week 1):
      * must have climbed ≥ MIN_RANK_GAIN places on the bestseller list (not just a new-release score)
      * seasonal only inside its promotion window (season.py) — never on/after the festival
      * never anything flagged EXPLOSIVES, never a non-product, never a falling product (v1.51)
    `rejected` (list) collects (title, reason) so the admin chat can see what was dropped."""
    rej = rejected if rejected is not None else []
    s = _hard_filters(signals.copy(), rej, today, need_climb=True)
    out = _take(s, ["trend_score"], n, max_per_cat)
    if len(out):
        out["arm"] = "momentum"
    return out


def select_durable(signals, n=8, max_per_cat=MAX_PER_CATEGORY, today=None, rejected=None):
    """DURABLE arm (v1.51) — products that have been on a bestseller list most of the last 14 days at a real rank
    (durable.py), in stock, not falling. The post claim is only 'on the list N of the last M days', which is a fact.
    Sorted by durability, then trend score. A durable product that has just started falling is NOT posted (see exit_flags)."""
    rej = rejected if rejected is not None else []
    s = signals.copy()
    if "durable" not in s:
        return pd.DataFrame()
    s = s[_truthy(s["durable"])]
    s = _hard_filters(s, rej, today, need_climb=False)
    out = _take(s, ["durable_score", "trend_score"], n, max_per_cat)
    if len(out):
        out["arm"] = "durable"
    return out


def drop_holders(ch, hist, today, rejected=None):
    """v1.50: the post prints a trend line from OUR rank history ('Climbed #38 → #4', 'Holding #51'). A pick that passed
    the signal filter but whose history line says 'holding' (or fell) contradicts the 'rising' claim — week 3 posted
    'Holding #51' under a 'Rising' header. Such picks are dropped before they reach the public pool."""
    if ch is None or len(ch) == 0:
        return ch
    keep = []
    for _, p in ch.iterrows():
        f = trend_facts(p["asin"], p.get("category_slug") or p.get("category"), hist, today)
        now, then = f["rank_now"], f["rank_then"]
        if now is not None and then is not None and not pd.isna(now) and not pd.isna(then) and then <= now:
            if rejected is not None:
                rejected.append((p["title"], f"own rank history shows no climb (#{int(then)} → #{int(now)}) — would read 'holding'"))
            continue
        keep.append(p)
    return pd.DataFrame(keep) if keep else ch.iloc[0:0]


def best_ranks(hist):
    """(asin, date) → best bestseller rank that day, plus the set of (category, date) fetched."""
    bs = hist[hist["list"] == "bestsellers"].copy()
    bs["rank"] = pd.to_numeric(bs["rank"], errors="coerce")
    ranks = bs.groupby(["asin", "date"])["rank"].min().to_dict()
    fetched = set(zip(bs["category_slug"], bs["date"]))
    return ranks, fetched


RANK_MATCH = 15          # v1.50: baselines start within ±15 ranks of the pick (week-3 check: unmatched baselines
                         # started at median rank 54 vs the picks' 26, so they were not a like-for-like comparison)


def make_rows(picks, hist, today, seed=None):
    """Pick rows + same-category baseline rows (deterministic per day), rank-matched when the pool allows.
    `baseline_type` records which: 'matched' (±RANK_MATCH ranks of the pick) or 'random' (whole category).
    `arm` (v1.51) is 'momentum' or 'durable' and is copied to the pick's baselines, so each arm is scored against its
    own like-for-like comparison. Durable picks are compared with rank-matched products of ANY durability — the
    question is whether 'durable' adds anything beyond simply having a similar rank today."""
    ranks, _ = best_ranks(hist)
    bs = hist[(hist["list"] == "bestsellers") & (hist["date"] == today)]
    rng = random.Random(seed if seed is not None else today)
    chosen = set(picks["asin"])
    rows = []
    for _, p in picks.iterrows():
        cat = p.get("category_slug") or p.get("category")
        r0 = ranks.get((p["asin"], today))
        arm = "durable" if str(p.get("arm", "")) == "durable" else "momentum"
        rows.append({"pick_date": today, "role": "pick", "asin": p["asin"], "category_slug": cat,
                     "title": str(p["title"])[:120], "trend_score": p.get("trend_score"), "rank_at_pick": r0,
                     "arm": arm})
        # Baselines must be in stock too (a price on today's list). Picks are; sold-out items drop off the
        # list fast, so unfiltered baselines made picks look better than they are.
        inst = bs[pd.to_numeric(bs["price_inr"], errors="coerce").fillna(0) > 0] if "price_inr" in bs else bs
        pool = sorted(set(inst.loc[inst["category_slug"] == cat, "asin"]) - chosen)
        near = [a for a in pool if r0 is not None and ranks.get((a, today)) is not None
                and abs(ranks[(a, today)] - r0) <= RANK_MATCH]
        kind, src = ("matched", near) if len(near) >= BASELINE_PER_PICK else ("random", pool)
        for b in rng.sample(src, min(BASELINE_PER_PICK, len(src))):
            chosen.add(b)
            rows.append({"pick_date": today, "role": "baseline", "asin": b, "category_slug": cat,
                         "title": "", "trend_score": "", "rank_at_pick": ranks.get((b, today)),
                         "baseline_type": kind, "arm": arm})
    return pd.DataFrame(rows)


def update_tracking(tracker, hist):
    """Fill rank_d7/d14/d28 and hit_d7/... where the horizon date has been observed."""
    if tracker.empty:
        return tracker
    t = tracker.copy()
    ranks, fetched = best_ranks(hist)
    for h in HORIZONS:
        rk, hit = [], []
        for _, r in t.iterrows():
            d = (datetime.strptime(str(r["pick_date"])[:10], "%Y-%m-%d") + timedelta(days=h)).strftime("%Y-%m-%d")
            if (r["category_slug"], d) not in fetched:           # day not observed (yet) → pending
                rk.append(r.get(f"rank_d{h}", "")); hit.append(r.get(f"hit_d{h}", ""))
                continue
            now = ranks.get((r["asin"], d))
            r0 = pd.to_numeric(r["rank_at_pick"], errors="coerce")
            rk.append(now if now is not None else "out")
            hit.append(int(now is not None and pd.notna(r0) and now <= r0))
        t[f"rank_d{h}"], t[f"hit_d{h}"] = rk, hit
        # second, fairer measure: still in the top 100 at all (picks were chosen right after a jump, so
        # "rank ≤ rank at pick" is biased against them by regression to the mean)
        t[f"alive_d{h}"] = ["" if str(x) == "" or (isinstance(x, float) and pd.isna(x)) else int(str(x) != "out") for x in rk]
    return t


def daily_wins(tracker, h=14, col="hit"):
    """Per pick date: did picks beat that day's baselines? → (days picks won, days compared).
    Picks made on the same day share one market wave, so days — not picks — are the independent units."""
    c = f"{col}_d{h}"
    if c not in tracker:
        return 0, 0
    d = tracker[pd.to_numeric(tracker[c], errors="coerce").notna()].copy()
    d[c] = pd.to_numeric(d[c])
    won = n = 0
    for _, g in d.groupby("pick_date"):
        p, b = g.loc[g["role"] == "pick", c], g.loc[g["role"] == "baseline", c]
        if len(p) and len(b):
            n += 1
            won += int(p.mean() > b.mean())
    return won, n


def scorecard(tracker):
    """Hit rate of picks vs baseline per horizon (only resolved rows)."""
    rows = []
    for h in HORIZONS:
        col = f"hit_d{h}"
        if col not in tracker:
            continue
        d = tracker[pd.to_numeric(tracker[col], errors="coerce").notna()]
        for role in ("pick", "baseline"):
            x = pd.to_numeric(d.loc[d["role"] == role, col], errors="coerce")
            rows.append({"horizon_days": h, "role": role, "resolved": int(len(x)),
                         "hits": int(x.sum()) if len(x) else 0,
                         "hit_rate": round(float(x.mean()), 3) if len(x) else None})
    sc = pd.DataFrame(rows)
    return sc


def wilson(k, n, z=1.96):
    """95% Wilson interval for k hits of n → (low, high). Small samples get wide, honest bands."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fair_scorecard(tracker, match=RANK_MATCH):
    """Every horizon × metric (hit / alive) × cohort, with intervals. Cohorts:
         all                 every resolved row (the pre-registered comparison)
         rank-matched        each pick vs baselines of the SAME DAY and category starting within ±match ranks of it
         non-festival-days   only pick days that are NOT within ±10 days of a festival (season.py) — a Diwali-week
                             pick is expected to fade after Diwali whatever the trend score said
    The pre-registered verdict() still decides on hit_d14 / all; this table is what you read it against."""
    import season
    cols = ["horizon_days", "metric", "cohort", "role", "resolved", "successes", "rate", "ci_low", "ci_high"]
    if tracker is None or tracker.empty:
        return pd.DataFrame(columns=cols)
    t = tracker.copy()
    t["_r0"] = pd.to_numeric(t.get("rank_at_pick"), errors="coerce")
    out = []
    for h in HORIZONS:
        for metric in ("hit", "alive"):
            c = f"{metric}_d{h}"
            if c not in t:
                continue
            d = t[pd.to_numeric(t[c], errors="coerce").notna()].copy()
            d[c] = pd.to_numeric(d[c])
            cohorts = {"all": d,
                       "non-festival-days": d[[not season.near_festival(x) for x in d["pick_date"]]] if len(d) else d}
            rows_m = []
            for (day, cat), g in d.groupby(["pick_date", "category_slug"]):
                bl = g[g["role"] == "baseline"]
                for _, p in g[g["role"] == "pick"].iterrows():
                    if pd.isna(p["_r0"]):
                        continue
                    near = bl[(bl["_r0"] - p["_r0"]).abs() <= match]
                    if len(near):
                        rows_m.append(p); rows_m.extend(r for _, r in near.iterrows())
            cohorts["rank-matched"] = pd.DataFrame(rows_m) if rows_m else d.iloc[0:0]
            for name, dd in cohorts.items():
                for role in ("pick", "baseline"):
                    x = dd.loc[dd["role"] == role, c] if len(dd) else pd.Series(dtype=float)
                    n, k = int(len(x)), (int(x.sum()) if len(x) else 0)
                    lo, hi = wilson(k, n)
                    out.append({"horizon_days": h, "metric": metric, "cohort": name, "role": role, "resolved": n,
                                "successes": k, "rate": round(k / n, 3) if n else None,
                                "ci_low": round(lo, 3), "ci_high": round(hi, 3)})
    return pd.DataFrame(out, columns=cols)


def arm_of(tracker):
    """Series of 'momentum' | 'durable' per tracker row. Rows from before v1.51 have no arm → momentum."""
    if tracker is None or len(tracker) == 0:
        return pd.Series([], dtype=str)
    if "arm" not in tracker:
        return pd.Series(["momentum"] * len(tracker), index=tracker.index)
    return tracker["arm"].fillna("").astype(str).where(lambda x: x == "durable", "momentum")


def arm_rows(tracker, arm):
    """The tracker rows of one arm (picks AND their baselines)."""
    if tracker is None or len(tracker) == 0:
        return tracker
    return tracker[arm_of(tracker) == arm]


def arm_scorecard(tracker, match=RANK_MATCH):
    """fair_scorecard per arm, one table with an `arm` column (momentum first). The momentum arm is the one the
    pre-registered verdict (hit at 14 days, ≥10 pick days) is about; the durable arm is judged on 'still in the top 100'."""
    parts = []
    for arm in ("momentum", "durable"):
        sub = arm_rows(tracker, arm)
        if sub is None or len(sub) == 0:
            continue
        fs = fair_scorecard(sub, match=match)
        if not fs.empty:
            fs.insert(0, "arm", arm)
            parts.append(fs)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["arm", "horizon_days", "metric", "cohort", "role", "resolved", "successes", "rate", "ci_low", "ci_high"])


def fair_line(fs, horizon=7, arm="momentum", metrics=(("hit", "kept/improved rank"), ("alive", "still in top-100"))):
    """One line for the daily digest: what the like-for-like comparison says so far (default 7d — the first
    horizon that resolves). Says 'inconclusive' unless the intervals actually separate."""
    if fs is None or fs.empty:
        return ""
    if "arm" in fs:
        fs = fs[fs["arm"] == arm]
    d = fs[(fs["horizon_days"] == horizon) & (fs["cohort"] == "rank-matched")]
    parts = []
    for metric, label in metrics:
        p = d[(d["metric"] == metric) & (d["role"] == "pick")]
        b = d[(d["metric"] == metric) & (d["role"] == "baseline")]
        if p.empty or b.empty or int(p.iloc[0]["resolved"]) == 0:
            continue
        p, b = p.iloc[0], b.iloc[0]
        sep = "picks ahead" if p["ci_low"] > b["ci_high"] else "picks behind" if p["ci_high"] < b["ci_low"] else "inconclusive"
        parts.append(f"{label} {p['rate']:.0%} vs matched baseline {b['rate']:.0%} (n={int(p['resolved'])}, {sep})")
    head = "Like-for-like" if arm == "momentum" else "🧱 Durable picks"
    return f"{head} ({horizon}d): " + " · ".join(parts) if parts else ""


def durable_line(fs, horizon=7):
    """Digest line for the durable arm: only 'still in the top 100' is the claim it makes."""
    return fair_line(fs, horizon, arm="durable", metrics=(("alive", "still in top-100"),))


def momentum_verdict(tracker):
    """The pre-registered verdict (hit at 14 days, ≥ MIN_PICK_DAYS pick days) concerns the MOMENTUM arm only —
    rows from before v1.51 count as momentum. '' when that arm has no rows."""
    m = arm_rows(tracker, "momentum")
    if m is None or len(m) == 0:
        return ""
    sc = scorecard(m)
    return verdict(sc, tracker=m) if not sc.empty else ""


MIN_PICK_DAYS = 10        # distinct pick dates needed before any verdict (one week = one market wave)


def verdict(sc, min_resolved=40, tracker=None):
    """Plain-language reading of the scorecard (no p-hacking: one horizon, decided in advance = 14d).
    With `tracker`, also requires ≥ MIN_PICK_DAYS distinct pick dates and reports days won + survival."""
    if tracker is not None and not tracker.empty and "hit_d14" in tracker:
        won, n = daily_wins(tracker, 14, "hit")
        if 0 < n < MIN_PICK_DAYS or n == 0:
            base = _verdict_core(sc, min_resolved)
            return (f"Forward test (14d): results from {n} pick day(s) so far — needs {MIN_PICK_DAYS} different days "
                    f"before judging (daily picks share the same market wave). " + ("" if n == 0 else f"So far: {base.split(': ', 1)[-1]}"))
        aw, an = daily_wins(tracker, 14, "alive")
        core = _verdict_core(sc, min_resolved)
        extra = f" Picks beat random on {won} of {n} days"
        if an:
            extra += f"; still in the top 100 after 14 days: better than random on {aw} of {an} days"
        if "beat random" in core and won < 0.6 * n:
            core = core.replace("picks beat random", "ahead overall, but NOT consistently by day — not proven")
        return core + extra + "."
    return _verdict_core(sc, min_resolved)


def _verdict_core(sc, min_resolved=40):
    d = sc[sc["horizon_days"] == 14]
    if d.empty:
        return "Forward test: no 14-day results yet."
    p = d[d["role"] == "pick"].iloc[0]
    b = d[d["role"] == "baseline"].iloc[0]
    if p["resolved"] < min_resolved:
        return (f"Forward test (14d): {p['resolved']}/{min_resolved} picks resolved — too early to judge "
                f"(picks {p['hit_rate'] if p['hit_rate'] is not None else '–'} vs random {b['hit_rate'] if b['hit_rate'] is not None else '–'}).")
    lift = (p["hit_rate"] / b["hit_rate"]) if b["hit_rate"] else None
    if lift is None:
        return "Forward test (14d): baseline has no hits — cannot compute lift yet."
    tone = ("picks beat random" if lift >= 1.3 else "no clear edge over random" if lift >= 0.9 else
            "picks do WORSE than random")
    return f"Forward test (14d): picks {p['hit_rate']:.0%} vs random {b['hit_rate']:.0%} → {lift:.2f}× ({tone})."


def post_text(picks, tag, week_label, scorecard_line=""):
    lines = [f"🔍 Hidden Amazon Finds — {week_label}", "Products climbing Amazon.in's bestseller lists this week:", ""]
    for i, (_, p) in enumerate(picks.iterrows(), 1):
        seas = f" ({p['seasonal'].replace('SEASONAL: ', '')})" if str(p.get("seasonal", "")).startswith("SEASONAL") else ""
        lines.append(f"{i}. {str(p['title'])[:90]}{seas}\n   {affiliate_link(p['asin'], tag)}")
    lines += ["", DISCLOSURE]
    if scorecard_line:
        lines += ["", scorecard_line]
    return "\n".join(lines)


# ── one message per product (v1.8) ───────────────────────────────────
SHORT_AD = "#ad · Amazon Associate link"


def trend_facts(asin, cat, hist, today, lookback=7):
    """Why this product is in the post — from OUR rank history only (no prices, no scraped claims).
    → dict(rank_now, rank_then, days_listed, window)"""
    bs = hist[(hist["list"] == "bestsellers") & (hist["category_slug"] == cat)].copy()
    bs["rank"] = pd.to_numeric(bs["rank"], errors="coerce")
    days = sorted(d for d in bs["date"].unique() if d <= today)
    window = days[-(lookback + 1):]
    mine = bs[bs["asin"] == asin].groupby("date")["rank"].min()
    rank_now = mine.get(today)
    rank_then = mine.get(window[0]) if window and window[0] != today else None
    listed = int(sum(d in mine.index for d in window[1:])) if len(window) > 1 else int(today in mine.index)
    return {"rank_now": rank_now, "rank_then": rank_then, "days_listed": listed,
            "window": max(len(window) - 1, 1)}


def _int(v):
    try:
        x = float(v)
        return None if pd.isna(x) else int(x)
    except (TypeError, ValueError):
        return None


def facts_for(p, hist, today):
    """trend_facts for one pick row, plus the durable claim when the pick came from the durable arm.
    The durable claim is 'on the list N of the last M days' — N and M come from durable.py (carried on the row),
    recomputed from HISTORY if the row does not have them. If neither works the pick falls back to the plain facts."""
    cat = p.get("category_slug") or p.get("category")
    f = trend_facts(p["asin"], cat, hist, today)
    if str(p.get("arm", "")) != "durable":
        return f
    d, w = _int(p.get("durable_days")), _int(p.get("durable_window"))
    if d is None or w is None:
        try:
            import durable
            dd = durable.durability(hist, today)
            if p["asin"] in dd.index:
                d, w = int(dd.loc[p["asin"], "durable_days"]), int(dd.loc[p["asin"], "durable_window"])
        except Exception:
            d = w = None
    if d is not None and w:
        f = dict(f, arm="durable", dur_days=d, dur_window=w)
    return f


def _durable(f):
    return f.get("arm") == "durable" and f.get("dur_window")


def _icon(f):
    return "🧱" if _durable(f) else "📈"


def trend_line(f, cat_name):
    now, then = f["rank_now"], f["rank_then"]
    if _durable(f):
        at = f" · now <b>#{int(now)}</b>" if now is not None and not pd.isna(now) else ""
        return f"🧱 On the list <b>{f['dur_days']} of the last {f['dur_window']} days</b> in {cat_name}{at}"
    if now is None or pd.isna(now):
        head = f"Trending in {cat_name}"
    elif then is None or pd.isna(then):
        head = f"New in the {cat_name} top 100 — now <b>#{int(now)}</b>"
    elif then > now:
        head = f"Climbed <b>#{int(then)} → #{int(now)}</b> in {cat_name} this week"
    else:
        head = f"Holding <b>#{int(now)}</b> in {cat_name}"
    return f"📈 {head} · on the list {f['days_listed']} of the last {f['window']} days"


def _h(x):
    import html
    return html.escape(str(x), quote=False)


import os as _os
# "single" (default): ONE message, every pick a tappable name · "per_product": header + one message per product
POST_STYLE = (_os.environ.get("AFF_POST_STYLE") or "single").strip().lower()


def short_title(t, n=60):
    """'Purepet Adult Dry Cat Food 1kg | Ocean Fish…' → 'Purepet Adult Dry Cat Food 1kg'."""
    import re
    s = re.split(r"\s[|–]\s|,\s|\s\(", str(t))[0].strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def _compact_line(f):
    now, then = f["rank_now"], f["rank_then"]
    if _durable(f):
        at = f" · now #{int(now)}" if now is not None and not pd.isna(now) else ""
        return f"on the list {f['dur_days']} of the last {f['dur_window']} days{at}"
    if now is None or pd.isna(now):
        return "trending"
    if then is None or pd.isna(then):
        return f"new at #{int(now)}"
    if then > now:
        return f"#{int(then)} → #{int(now)}"
    return f"holding #{int(now)}"


def post_messages(picks_df, hist, tag, today, cat_names=None, week_label="", style=None):
    """Telegram HTML. style 'single' → [one message]; 'per_product' → [header, msg1, msg2, ...].
    Biggest climbers first. No prices (Associates rules)."""
    style = (style or POST_STYLE)
    cat_names = cat_names or {}
    items = []
    for _, p in picks_df.iterrows():
        cat = p.get("category_slug") or p.get("category")
        f = facts_for(p, hist, today)
        climb = (f["rank_then"] - f["rank_now"]) if f["rank_then"] is not None and f["rank_now"] is not None \
            and not pd.isna(f["rank_then"]) and not pd.isna(f["rank_now"]) else -1
        items.append((climb, p, f, cat_names.get(cat, cat)))
    items.sort(key=lambda x: x[0], reverse=True)
    if style == "single":
        lines = [f"🔍 <b>Hidden Amazon Finds</b> — {_h(week_label)}",
                 "On Amazon.in's bestseller lists (tracked daily): new, climbing or holding. Tap a name for today's price.", ""]
        for i, (_, p, f, cname) in enumerate(items, 1):
            seas = str(p.get("seasonal", ""))
            tag_s = f" · 📅 {_h(seas.replace('SEASONAL: ', ''))}" if seas.startswith("SEASONAL") else ""
            lines.append(f"{i}. <a href=\"{_h(affiliate_link(p['asin'], tag))}\">{_h(short_title(p['title']))}</a>\n"
                         f"    {_icon(f)} {_h(cname)} · {_compact_line(f)}{tag_s}")
        if any(_durable(f) for _, _, f, _ in items):
            lines += ["", "🧱 = steady on the list most days · 📈 = climbing or new"]
        lines += ["", f"<i>{_h(DISCLOSURE)}</i>"]
        return ["\n".join(lines)]
    header = (f"🔍 <b>Hidden Amazon Finds</b> — {_h(week_label)}\n"
              f"{len(items)} products climbing Amazon.in bestseller lists, picked from daily rank tracking.\n\n"
              f"<i>{_h(DISCLOSURE)}</i>")
    msgs = [header]
    for _, p, f, cname in items:
        seas = str(p.get("seasonal", ""))
        seas_line = f"\n📅 Seasonal: {_h(seas.replace('SEASONAL: ', ''))}" if seas.startswith("SEASONAL") else ""
        link = _h(affiliate_link(p["asin"], tag))
        # title and a button-style line are hyperlinks; no price (Associates rules: prices only via PA-API)
        msgs.append(f"<a href=\"{link}\"><b>{_h(str(p['title'])[:110])}</b></a>\n\n{trend_line(f, _h(cname))}{seas_line}\n\n"
                    f"👉 <a href=\"{link}\"><b>Check today's price on Amazon</b></a>\n\n<i>{SHORT_AD}</i>")
    return msgs


# ── v1.11: X + WhatsApp share kit ─────────────────────────────────────
from urllib.parse import quote as _q
X_URL_LEN = 23          # X counts every link as 23 characters
X_LIMIT = 280


def _plain_line(f, cname):
    now, then = f["rank_now"], f["rank_then"]
    if _durable(f):
        at = f" (now #{int(now)})" if now is not None and not pd.isna(now) else ""
        return f"On the list {f['dur_days']} of the last {f['dur_window']} days in {cname}{at}"
    if now is None or pd.isna(now):
        return f"Trending in {cname}"
    if then is None or pd.isna(then):
        return f"New in the {cname} top 100 (now #{int(now)})"
    if then > now:
        return f"Climbed #{int(then)} → #{int(now)} in {cname} this week"
    return f"Holding #{int(now)} in {cname}"


def x_weight(text):
    """Approximate X length: links count 23; characters outside basic Latin count 2 (emoji, ₹, →)."""
    import re
    n = 0
    for part in re.split(r"(https?://\S+)", text):
        if part.startswith("http"):
            n += X_URL_LEN
        else:
            n += sum(1 if ord(ch) < 0x1100 else 2 for ch in part)
    return n


def x_text(p, f, cname, tag):
    link = affiliate_link(p["asin"], tag)
    line = _plain_line(f, cname)
    title = str(p["title"])
    for cut in range(len(title), 10, -5):              # shorten the title until it fits
        t = title[:cut].rstrip(" ,|-") + ("…" if cut < len(title) else "")
        txt = f"{t}\n\n{_icon(f)} {line}\n\n{link}\n\n#ad #AmazonFinds"
        if x_weight(txt) <= X_LIMIT:
            return txt
    return f"{_icon(f)} {line}\n\n{link}\n\n#ad"


def wa_text(p, f, cname, tag):
    """WhatsApp formatting: *bold*, plain link (WhatsApp shows the preview)."""
    seas = str(p.get("seasonal", ""))
    s = f"\n📅 Seasonal: {seas.replace('SEASONAL: ', '')}" if seas.startswith("SEASONAL") else ""
    return (f"*{str(p['title'])[:110]}*\n\n{_icon(f)} {_plain_line(f, cname)}{s}\n\n{affiliate_link(p['asin'], tag)}\n\n"
            f"_#ad · Amazon Associate link_")


def share_kit(picks_df, hist, tag, today, cat_names=None):
    """→ list of dicts per pick: x_text, wa_text, x_intent (one-tap tweet), wa_share (one-tap WhatsApp)."""
    out = []
    for _, p in picks_df.iterrows():
        cat = p.get("category_slug") or p.get("category")
        f = facts_for(p, hist, today)
        cname = (cat_names or {}).get(cat, cat)
        xt, wt = x_text(p, f, cname, tag), wa_text(p, f, cname, tag)
        out.append({"asin": p["asin"], "x_text": xt, "wa_text": wt,
                    "x_intent": "https://twitter.com/intent/tweet?text=" + _q(xt),
                    "wa_share": "https://wa.me/?text=" + _q(wt)})
    return out


def wa_bundle(picks_df, hist, tag, today, cat_names=None, n=5, label=""):
    """ONE WhatsApp post with the day's top picks. WhatsApp can't hyperlink words, so every product
    gets its full link on its own line (WhatsApp makes it tappable)."""
    lines = [f"*🔍 OneZero Deals — {label or today}*", "On Amazon.in's bestseller lists today (new, climbing or holding):", ""]
    for i, (_, p) in enumerate(picks_df.head(n).iterrows(), 1):
        cat = p.get("category_slug") or p.get("category")
        f = facts_for(p, hist, today)
        cname = (cat_names or {}).get(cat, cat)
        lines += [f"{i}. *{short_title(p['title'], 55)}*", f"{_icon(f)} {_plain_line(f, cname)}",
                  f"👉 {affiliate_link(p['asin'], tag)}", ""]
    lines.append("_#ad · As an Amazon Associate I earn from qualifying purchases._")
    text = "\n".join(lines)
    return text, "https://wa.me/?text=" + _q(text)


def kit_message(i, k):
    """Admin-chat message with one-tap buttons (Telegram HTML)."""
    return (f"<b>{i}.</b> {_h(k['x_text'].split(chr(10))[0][:80])}\n"
            f"<a href=\"{_h(k['x_intent'])}\">𝕏 Post on X</a>  ·  <a href=\"{_h(k['wa_share'])}\">🟢 Share on WhatsApp</a>")


def post_to_x(kit, creds, max_posts, client=None):
    """Optional paid auto-posting (X API pay-per-use). Returns [(asin, tweet_id or error)].
    Posts at most max_posts — your spend cap."""
    if client is None:
        import tweepy
        client = tweepy.Client(consumer_key=creds["api_key"], consumer_secret=creds["api_secret"],
                               access_token=creds["access_token"], access_token_secret=creds["access_secret"])
    done = []
    for k in kit[:max_posts]:
        try:
            r = client.create_tweet(text=k["x_text"])
            done.append((k["asin"], str((r.data or {}).get("id", ""))))
        except Exception as exc:
            done.append((k["asin"], f"ERROR {type(exc).__name__}: {str(exc)[:120]}"))
            if "402" in str(exc) or "403" in str(exc):     # billing / permission problem → stop, don't burn calls
                break
    return done
