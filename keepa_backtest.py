#!/usr/bin/env python3
"""
Keepa backtest harness — Bazaar Radar v2 protocol (Go/No-Go gate).

Commands
  discover  build a first-seen cohort ASIN list via Keepa Product Finder   (KEEPA_API_KEY)
  fetch     download + cache product histories                              (KEEPA_API_KEY)
  pilot     measure entry rate & winner rate, size the full budget          (no tokens)
  run       full backtest → REPORT.md, products.csv, merge_events.csv       (no tokens)

Pipeline (run)
  load → gap-aware merge cleaning → cohort → flag (top-N entry ≤ ENTRY_CUTOFF)
  → score on entry+FLAG_DELAY_DAYS using only data ≤ that day → label (locked)
  → evaluate (bootstrap lift + AUC, precision curve) → decision (only if LOCKED)
"""
import os, sys, json, glob, argparse, time
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C
from discount_rule import discount_status, reverted_after

KEEPA_EPOCH_OFFSET_MIN = 21564000
CSV_NEW_PRICE, CSV_SALES, CSV_COUNT_NEW, CSV_RATING, CSV_REVIEWS = 1, 3, 11, 16, 17
CSV_AMAZON, CSV_LIST_PRICE, CSV_BUY_BOX_SHIPPING = 0, 4, 18   # 18 = [t, price, shipping] triplets; needs buybox=1
D = lambda n: pd.Timedelta(days=n)

def keepa_min_to_ts(km):
    return pd.to_datetime((np.asarray(km, dtype="int64") + KEEPA_EPOCH_OFFSET_MIN) * 60, unit="s")

def ts_to_keepa_min(ts):
    return int(pd.Timestamp(ts).value // 60_000_000_000) - KEEPA_EPOCH_OFFSET_MIN

# ══════════════════════════════════════════════════════════════════════
# CONFIG SANITY
# ══════════════════════════════════════════════════════════════════════

def outcome_window(score_date):
    s = score_date + D(C.LEAD_DAYS)
    return s, s + pd.Timedelta(weeks=C.OUTCOME_WEEKS) - D(1)

def check_config():
    """Latest possible outcome end must stay before HORIZON_LIMIT."""
    last_score = pd.Timestamp(C.ENTRY_CUTOFF) + D(C.FLAG_DELAY_DAYS + C.QUARANTINE_DAYS)
    _, last_end = outcome_window(last_score)
    problems = []
    if last_end > pd.Timestamp(C.HORIZON_LIMIT):
        problems.append(f"latest outcome window ends {last_end.date()} > HORIZON_LIMIT "
                        f"{C.HORIZON_LIMIT}: move ENTRY_CUTOFF earlier")
    if pd.Timestamp(C.ENTRY_CUTOFF) < pd.Timestamp(C.FIRST_SEEN_END):
        problems.append("ENTRY_CUTOFF is before FIRST_SEEN_END")
    if any(w < 0 for w in C.WEIGHTS.values()) or sum(C.WEIGHTS.values()) <= 0:
        problems.append("WEIGHTS must be non-negative with a positive sum")
    return problems, last_end

# ══════════════════════════════════════════════════════════════════════
# LOAD
# ══════════════════════════════════════════════════════════════════════

def _series(arr, scale=1.0):
    if not arr or len(arr) < 2:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    a = np.asarray(arr, dtype="int64")
    t, v = a[0::2], a[1::2].astype(float)
    v[v < 0] = np.nan
    s = pd.Series(v / scale, index=keepa_min_to_ts(t)).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()

def _series_triplet(arr, scale=100.0):
    """Keepa *_SHIPPING arrays are [t, price, shipping, ...]; value = price + shipping."""
    if not arr or len(arr) < 3:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    a = np.asarray(arr[: len(arr) // 3 * 3], dtype="int64").reshape(-1, 3)
    v = np.where((a[:, 1] >= 0), a[:, 1] + np.maximum(a[:, 2], 0), -1).astype(float)
    v[v < 0] = np.nan
    s = pd.Series(v / scale, index=keepa_min_to_ts(a[:, 0])).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()

def load_product(p):
    csv = p.get("csv") or []
    get = lambda i: csv[i] if len(csv) > i else None
    rank = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    root, sr = p.get("rootCategory"), (p.get("salesRanks") or {})
    for k in sr:
        if str(k) == str(root):
            rank = _series(sr[k]); break
    if rank.empty:
        rank = _series(get(CSV_SALES))
    listed = p.get("listedSince") or 0
    tracking = p.get("trackingSince") or 0
    first_obs = rank.index.min() if len(rank) else None
    first_seen = (keepa_min_to_ts([listed])[0] if listed > 0 else
                  keepa_min_to_ts([tracking])[0] if tracking > 0 else first_obs)
    return {"asin": p["asin"], "category": p.get("rootCategory"), "title": p.get("title") or "",
            "rank": rank, "reviews": _series(get(CSV_REVIEWS)),
            "rating": _series(get(CSV_RATING), 10.0), "price": _series(get(CSV_NEW_PRICE), 100.0),
            "price_amazon": _series(get(CSV_AMAZON), 100.0),
            "price_list": _series(get(CSV_LIST_PRICE), 100.0),
            "price_buybox": _series_triplet(get(CSV_BUY_BOX_SHIPPING)),
            "sellers": _series(get(CSV_COUNT_NEW)), "first_seen": first_seen,
            "first_seen_source": "listedSince" if listed > 0 else
                                 "trackingSince" if tracking > 0 else "first_observation"}

def load_dir(path):
    prods = []
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        with open(f) as fh:
            doc = json.load(fh)
        items = doc.get("products") if isinstance(doc, dict) and "products" in doc else \
                doc if isinstance(doc, list) else [doc]
        for p in items:
            if p.get("_missing"):
                continue
            try:
                prods.append(load_product(p))
            except Exception as exc:
                print(f"[WARN] skip product in {f}: {exc}")
    print(f"Loaded {len(prods)} products from {path}")
    return prods

# ══════════════════════════════════════════════════════════════════════
# GAP-AWARE MERGE DETECTOR (daily rates, no interpolation)
# ══════════════════════════════════════════════════════════════════════

def _nearest_before(s, ts):
    s = s[s.index <= ts]
    return s.iloc[-1] if len(s) else np.nan

def detect_merges(prod):
    rv = prod["reviews"]
    if len(rv) < 3:
        return [], rv.copy()
    t, vals = rv.index, rv.values.astype(float)
    dt = np.r_[np.nan, np.diff(t.values).astype("timedelta64[s]").astype(float) / 86400]
    delta = np.r_[np.nan, np.diff(vals)]
    rate = delta / np.where(dt > 0, dt, np.nan)
    cleaned, offset, events = vals.copy(), 0.0, []
    for i in range(1, len(rv)):
        cleaned[i] = vals[i] - offset
        win = (t > t[i] - D(8)) & (t < t[i])
        r = rate[win]
        med = np.nanmedian(r) if win.any() and np.isfinite(r).any() else np.nan
        med = max(med, 0.5) if np.isfinite(med) else 0.5
        prior = vals[i - 1]
        if not (delta[i] >= C.MERGE_MIN_ABS and prior > 0 and
                delta[i] > C.MERGE_SHARE_OF_TOTAL * prior and rate[i] > C.MERGE_RATIO_VS_MEDIAN * med):
            continue
        base = {"asin": prod["asin"], "ts": t[i], "jump": delta[i], "gap_days": round(dt[i], 1)}
        if dt[i] > C.MERGE_MAX_OBS_GAP_D:
            events.append({**base, "type": "unresolvable_gap", "corroboration": ""})
            continue
        r0, r1 = _nearest_before(prod["rating"], t[i - 1]), _nearest_before(prod["rating"], t[i])
        k0, k1 = _nearest_before(prod["rank"], t[i - 1]), _nearest_before(prod["rank"], t[i])
        corr = []
        if np.isfinite(r0) and np.isfinite(r1) and abs(r1 - r0) >= C.RATING_SHIFT:
            corr.append("rating_shift")
        if np.isfinite(k0) and np.isfinite(k1) and k1 > C.RANK_FLAT_RATIO * k0:
            corr.append("rank_flat")
        events.append({**base, "type": "merge_suspect", "corroboration": ",".join(corr) or "none"})
        offset += delta[i] - med * dt[i]         # re-baseline: keep a typical increment only
        cleaned[i] = vals[i] - offset
    return events, pd.Series(cleaned, index=t)

# ══════════════════════════════════════════════════════════════════════
# DAILY GRID (LOCF + age of last observation)
# ══════════════════════════════════════════════════════════════════════

def daily(s, start, end):
    idx = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="D")
    if s.empty:
        return pd.Series(np.nan, index=idx), pd.Series(np.nan, index=idx)
    s = s.groupby(s.index.normalize()).last()
    full = idx.union(s.index)
    v = s.reindex(full).ffill().reindex(idx)
    last_obs = pd.Series(s.index, index=s.index).reindex(full).ffill().reindex(idx)
    return v, (pd.Series(idx, index=idx) - last_obs).dt.days

# ══════════════════════════════════════════════════════════════════════
# COHORT + FLAG
# ══════════════════════════════════════════════════════════════════════

def in_cohort(p):
    if C.CATEGORIES and p["category"] not in C.CATEGORIES:
        return False
    if C.COHORT_MODE == "first_seen":
        fs = p["first_seen"]
        return fs is not None and pd.Timestamp(C.FIRST_SEEN_START) <= fs <= \
            pd.Timestamp(C.FIRST_SEEN_END) + D(1)
    return True

def first_entry(p):
    """First day rank <= ENTRY_TOP_N. first_seen mode: from listing to ENTRY_CUTOFF."""
    r = p["rank"]
    if C.COHORT_MODE == "first_seen":
        start = p["first_seen"]
    else:
        start = pd.Timestamp(C.FIRST_SEEN_START)
    end = pd.Timestamp(C.ENTRY_CUTOFF) + D(1)
    w = r[(r.index >= start) & (r.index < end)]
    hit = w[w <= C.ENTRY_TOP_N]
    if hit.empty:
        return None
    if C.COHORT_MODE != "first_seen":
        before = r[r.index < hit.index[0]]
        if len(before) and before.iloc[-1] <= C.ENTRY_TOP_N and (hit.index[0] - before.index[-1]).days <= 3:
            return None
    return hit.index[0].normalize()

def truncate(p, t):
    """Everything a signal function may see. Enforces no look-ahead (end of day t)."""
    cut = t + D(1) - pd.Timedelta(seconds=1)
    return {k: (v[v.index <= cut] if isinstance(v, pd.Series) else v) for k, v in p.items()}

# ══════════════════════════════════════════════════════════════════════
# SIGNALS (point-in-time)
# ══════════════════════════════════════════════════════════════════════

def signals_at(p, entry, t, cat_med):
    t0 = t - D(60)
    rk, _ = daily(p["rank"], t0, t)
    rv, rv_age = daily(p["reviews_clean"], t0, t)
    o = {}
    # momentum: 7-day log-rank gain minus category median (removes site-wide lifts)
    r_now, r_7 = rk.iloc[-1], rk.iloc[-8]
    raw = float(np.log(r_7 / r_now)) if np.isfinite(r_now) and np.isfinite(r_7) and r_now > 0 else 0.0
    o["momentum_raw"] = raw
    o["momentum_rel"] = raw - cat_med.get(t, 0.0)
    o["momentum"] = float(np.clip(o["momentum_rel"] / np.log(10), 0, 1))
    # mover (≈ Movers & Shakers): best 1-day % rank gain within entry-3 .. t
    win = rk[rk.index >= entry - D(3)]
    g = [(a - b) / b * 100 for a, b in zip(win.values[:-1], win.values[1:])
         if np.isfinite(a) and np.isfinite(b) and b > 0]
    o["mover_pct"] = max(g) if g else 0.0
    o["mover"] = float(np.clip(np.log10(max(o["mover_pct"], 0) + 1) / 4, 0, 1))
    # review velocity (merge-cleaned, last 14 days, needs fresh data)
    v = rv.iloc[-15:].dropna()
    if len(v) >= 2 and rv_age.iloc[-1] <= 7:
        o["reviews_per_day"] = max((v.iloc[-1] - v.iloc[0]) / max((v.index[-1] - v.index[0]).days, 1), 0)
    else:
        o["reviews_per_day"] = np.nan
    o["review_vel"] = float(min(np.log10(o["reviews_per_day"] + 1) / 2.5, 1)) \
        if np.isfinite(o["reviews_per_day"]) else 0.0
    # launch_speed: days from first_seen to top-N entry (replaces constant new_launch)
    fs = p["first_seen"]
    o["days_to_entry"] = (entry - fs.normalize()).days if fs is not None else np.nan
    o["launch_speed"] = float(np.clip(1 - o["days_to_entry"] / C.LAUNCH_SPEED_FULL_DAYS, 0, 1)) \
        if np.isfinite(o["days_to_entry"]) else 0.0
    # persistence: share of days in top-N since entry (inclusive)
    since = rk[rk.index >= entry]
    o["persistence"] = float((since <= C.ENTRY_TOP_N).mean()) if len(since) else 0.0
    o["reviews_at_score"] = v.iloc[-1] if len(v) else np.nan
    o["sellers_at_score"] = _nearest_before(p["sellers"], t)
    # ── Price (lowest-new by default; Buy Box only if FETCH_BUYBOX pulled it)
    pb = p["price_buybox"] if len(p["price_buybox"]) else p["price"]
    o["price_source"] = "buybox" if len(p["price_buybox"]) else "lowest_new"
    o["price_at_score"] = _nearest_before(pb, t)
    # ── DISCOUNT PENALTY (pre-registered, in the score; shared rule: discount_rule.py)
    t_end = t + D(1) - pd.Timedelta(seconds=1)
    climbing = o["momentum"] > 0 or o["mover"] > 0
    ds = discount_status(pb, t_end, C.DISCOUNT_DROP_PCT, C.DISCOUNT_LOOKBACK_DAYS, C.DISCOUNT_SUSTAIN_HOURS,
                         fresh_hours=C.DISCOUNT_FRESH_HOURS)
    o["price_drop_pct"], o["discount_status"] = ds["drop_pct"], ds["status"]
    o["discount_run_hours"], o["discount_threshold"] = ds["run_hours"], ds["threshold"]
    o["price_age_hours"] = ds["age_hours"]
    o["discount_driven"] = bool(ds["status"] == "sustained" and climbing)
    o["momentum_unpenalized"], o["mover_unpenalized"] = o["momentum"], o["mover"]
    if o["discount_driven"]:
        o["momentum"] *= C.DISCOUNT_PENALTY
        o["mover"] *= C.DISCOUNT_PENALTY
    # shadow variants: same inputs, different rule; report only
    o["_shadow_triggers"] = {}
    for name, cfg in C.DISCOUNT_SHADOWS.items():
        if cfg is None:
            o["_shadow_triggers"][name] = False
        elif cfg == "stale":
            o["_shadow_triggers"][name] = o["discount_driven"]
        else:
            st = discount_status(pb, t_end, cfg["drop_pct"], C.DISCOUNT_LOOKBACK_DAYS, cfg["sustain_hours"],
                                 fresh_hours=C.DISCOUNT_FRESH_HOURS)
            o["_shadow_triggers"][name] = bool(st["status"] == "sustained" and climbing)
    # ── EXPLORATORY price features (reported only; never gated)
    p14 = _nearest_before(pb, t - D(14))
    o["price_chg_14d_pct"] = (o["price_at_score"] / p14 - 1) * 100 \
        if np.isfinite(o["price_at_score"]) and np.isfinite(p14) and p14 > 0 else np.nan
    lp = _nearest_before(p["price_list"], t)
    o["discount_vs_list_pct"] = (1 - o["price_at_score"] / lp) * 100 \
        if np.isfinite(lp) and lp > 0 and np.isfinite(o["price_at_score"]) else np.nan
    pa = p["price_amazon"]
    o["amazon_sells"] = bool(len(pa[(pa.index > t - D(14)) & (pa.index <= t + D(1))]))
    wsum = sum(C.WEIGHTS.values())
    o["score"] = 100 * sum(o[k] * w for k, w in C.WEIGHTS.items()) / wsum
    unp = {**o, "momentum": o["momentum_unpenalized"], "mover": o["mover_unpenalized"]}
    o["score_unpenalized"] = 100 * sum(unp[k] * w for k, w in C.WEIGHTS.items()) / wsum
    for name, trig in o.pop("_shadow_triggers").items():
        f = C.DISCOUNT_PENALTY if trig else 1.0
        sh = {**unp, "momentum": unp["momentum"] * f, "mover": unp["mover"] * f}
        if C.DISCOUNT_SHADOWS[name] == "stale":
            age = o["price_age_hours"]
            if np.isfinite(age) and age > C.STALE_DEGRADE["after_hours"]:
                steps = np.ceil((age - C.STALE_DEGRADE["after_hours"]) / 12)
                sh["momentum"] *= max(0.0, 1 - C.STALE_DEGRADE["per_12h"] * steps)
        o[f"score_shadow_{name}"] = 100 * sum(sh[k] * w for k, w in C.WEIGHTS.items()) / wsum
        o[f"trig_shadow_{name}"] = trig
    return o

def in_holdout(asin):
    """Deterministic concurrent-holdout membership from a salted hash (not RNG state)."""
    import hashlib
    h = int(hashlib.sha256(f"{C.HOLDOUT_SALT}:{asin}".encode()).hexdigest()[:8], 16)
    return h / 0xFFFFFFFF < C.CONCURRENT_HOLDOUT_FRACTION

def _by_cat(prods):
    out = {}
    for p in prods:
        out.setdefault(p["category"], []).append(p)
    return out

def category_logrank_median(prods, start, end):
    meds = {}
    for cat, ps in _by_cat(prods).items():
        cols = [np.log(r.shift(7) / r) for r in (daily(p["rank"], start, end)[0] for p in ps)]
        meds[cat] = pd.concat(cols, axis=1).median(axis=1, skipna=True).fillna(0).to_dict()
    return meds

def category_rank_factor(prods, start, end):
    """Multiply ranks by baseline/today to undo cohort-wide (sale) moves. Capped 0.5-2."""
    out = {}
    for cat, ps in _by_cat(prods).items():
        med = pd.concat([daily(p["rank"], start, end)[0] for p in ps], axis=1).median(axis=1, skipna=True)
        base = med.rolling(28, min_periods=7, center=True).median()
        out[cat] = (base / med).clip(0.5, 2.0).fillna(1.0).to_dict()
    return out

# ══════════════════════════════════════════════════════════════════════
# LABEL (locked)
# ══════════════════════════════════════════════════════════════════════

def label(p, t, fac):
    s, e = outcome_window(t)
    # price cut during the outcome window vs price at score → "bought" outcome check
    pb = p["price_buybox"] if len(p["price_buybox"]) else p["price"]
    p0 = _nearest_before(pb, t)
    pw = pb[(pb.index >= s) & (pb.index <= e + D(1))]
    cut = (pw.median() / p0 - 1) * 100 if len(pw) and np.isfinite(p0) and p0 > 0 else np.nan
    rk, age = daily(p["rank"], s, e)
    if C.WIN_CATEGORY_RELATIVE:
        rk = rk * pd.Series([fac.get(d, 1.0) for d in rk.index], index=rk.index)
    rk[age > 7] = np.nan
    observable = rk.notna().mean() >= C.MIN_OBSERVED_SHARE
    weeks_top = int((rk.groupby(np.arange(len(rk)) // 7).median() <= C.WIN_TOP_N).sum())
    rv, _ = daily(p["reviews_clean"], s - D(1), e)
    rv = rv.dropna()
    if len(rv) >= 2:
        r0, r1 = rv.iloc[0], rv.iloc[-1]
        gain = r1 - r0
        growth = gain / r0 if r0 > 0 else (np.inf if gain > 0 else 0.0)
    else:
        gain, growth = np.nan, np.nan
    win = bool(observable and weeks_top >= C.WIN_MIN_WEEKS and np.isfinite(gain) and
               gain >= C.WIN_MIN_REVIEW_GAIN and growth >= C.WIN_MIN_REVIEW_GROWTH)
    return {"weeks_top": weeks_top, "review_gain": gain,
            "outcome_price_chg_pct": round(cut, 1) if np.isfinite(cut) else np.nan,
            "outcome_price_cut": bool(np.isfinite(cut) and cut <= -C.PRICE_CUT_PCT),
            "review_growth": round(growth, 3) if np.isfinite(growth) else growth,
            "observable": observable, "winner": int(win),
            "outcome_start": s.date(), "outcome_end": e.date()}

# ══════════════════════════════════════════════════════════════════════
# EVALUATION
# ══════════════════════════════════════════════════════════════════════

def auc(y, s):
    y, s = np.asarray(y), np.asarray(s)
    pos = y.sum(); neg = len(y) - pos
    if pos == 0 or neg == 0:
        return np.nan
    r = pd.Series(s).rank().values
    return (r[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)

def top_precision(y, s, frac):
    k = max(1, int(round(len(y) * frac)))
    return np.asarray(y)[np.argsort(-np.asarray(s), kind="stable")[:k]].mean()

def _within_auc_arr(y, s, cats):
    """Winner-weighted mean of per-category AUCs (categories with both classes only)."""
    num = den = 0.0
    for c in np.unique(cats):
        m = cats == c
        a = auc(y[m], s[m])
        if np.isfinite(a):
            w = y[m].sum(); num += a * w; den += w
    return num / den if den else np.nan

def evaluate(y, s, cats=None, n_boot=None, seed=None):
    """Gates: lift lower > GO_LIFT_LOWER, AUC lower > GO_AUC_LOWER and, when categories are
    given, within-category AUC lower > GO_WITHIN_AUC_LOWER. Bootstrap is stratified by
    category so the category mix is held fixed across resamples."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    cats = None if cats is None else np.asarray(cats).astype(str)
    rng = np.random.default_rng(C.SEED if seed is None else seed)
    n, base = len(y), y.mean()
    res = {"n": n, "winners": int(y.sum()), "base_rate": base,
           "top_precision": top_precision(y, s, C.TOP_FRACTION), "auc": auc(y, s)}
    res["lift"] = res["top_precision"] / base if base > 0 else np.nan
    groups = [np.where(cats == c)[0] for c in np.unique(cats)] if cats is not None else [np.arange(n)]
    tp, au, li, wa = [], [], [], []
    for _ in range(n_boot or C.BOOTSTRAP_N):
        i = np.concatenate([rng.choice(g, len(g)) for g in groups])
        yy, ss = y[i], s[i]
        p = top_precision(yy, ss, C.TOP_FRACTION)
        tp.append(p); au.append(auc(yy, ss)); li.append(p / yy.mean() if yy.mean() > 0 else np.nan)
        if cats is not None:
            wa.append(_within_auc_arr(yy, ss, cats[i]))
    a = (1 - C.CI_LEVEL) / 2
    q = lambda v: (float(np.nanquantile(v, a)), float(np.nanquantile(v, 1 - a)))
    res["top_precision_ci"], res["auc_ci"], res["lift_ci"] = q(tp), q(au), q(li)
    res["gates"] = {"lift": res["lift_ci"][0] > C.GO_LIFT_LOWER,
                    "auc": res["auc_ci"][0] > C.GO_AUC_LOWER}
    if cats is not None:
        res["within_auc"] = _within_auc_arr(y, s, cats)
        res["within_auc_ci"] = q(wa)
        res["gates"]["within_category_auc"] = res["within_auc_ci"][0] > C.GO_WITHIN_AUC_LOWER
    res["passes"] = bool(all(res["gates"].values()))
    res["precision_curve"] = {f: top_precision(y, s, f) for f in C.PRECISION_CURVE_FRACTIONS}
    return res

# ══════════════════════════════════════════════════════════════════════
# BUILD DATASET (shared by pilot + run)
# ══════════════════════════════════════════════════════════════════════

def build(data_dir):
    prods = load_dir(data_dir)
    cohort = [p for p in prods if in_cohort(p)]
    merge_log = []
    for p in cohort:
        ev, p["reviews_clean"] = detect_merges(p)
        merge_log.extend(ev)
    g0 = pd.Timestamp(C.FIRST_SEEN_START) - D(90)
    g1 = pd.Timestamp(C.HORIZON_LIMIT) + D(14)
    cat_med = category_logrank_median(cohort, g0, g1) if cohort else {}
    cat_fac = category_rank_factor(cohort, g0, g1) if cohort else {}
    sale_days = {d.normalize() for a, b, _ in C.SALE_EVENTS
                 for d in pd.date_range(pd.Timestamp(a) - D(C.SALE_BUFFER_DAYS),
                                        pd.Timestamp(b) + D(C.SALE_BUFFER_DAYS))}
    rows, counts = [], {"loaded": len(prods), "cohort": len(cohort), "no_entry": 0,
                        "unobservable": 0, "scored": 0}
    fs_src = pd.Series([p["first_seen_source"] for p in cohort]).value_counts().to_dict() if cohort else {}
    for p in cohort:
        entry = first_entry(p)
        if entry is None:
            counts["no_entry"] += 1; continue
        t = entry + D(C.FLAG_DELAY_DAYS)
        prev = p["rank"][p["rank"].index < entry]
        entry_gap = (entry - prev.index[-1].normalize()).days if len(prev) else np.nan
        sig = signals_at(truncate(p, t), entry, t, cat_med.get(p["category"], {}))
        quarantine = "none"
        if sig.get("discount_status") == "dip_unconfirmed" and C.QUARANTINE_DAYS > 0:
            first_status = sig["discount_status"]
            t = t + D(C.QUARANTINE_DAYS)                                 # resolution date (no look-ahead:
            sig = signals_at(truncate(p, t), entry, t, cat_med.get(p["category"], {}))   # data <= new t)
            quarantine = {"sustained": "resolved_penalised", "none": "resolved_organic"}.get(
                sig["discount_status"], "unresolved")
            sig["status_at_first_score"] = first_status
        sig["quarantine"] = quarantine
        lab = label(p, t, cat_fac.get(p["category"], {}))
        if sig.get("discount_status") == "dip_unconfirmed":           # POST-HOC, report only
            pb_full = p["price_buybox"] if len(p["price_buybox"]) else p["price"]
            sig["dip_reverted_48h"] = reverted_after(pb_full, t + D(1) - pd.Timedelta(seconds=1),
                                                     sig["discount_threshold"])
        else:
            sig["dip_reverted_48h"] = None
        if not lab["observable"]:
            counts["unobservable"] += 1; continue
        counts["scored"] += 1
        rows.append({"asin": p["asin"], "holdout": in_holdout(p["asin"]), "category": p["category"], "title": p["title"][:80],
                     "first_seen": p["first_seen"].date(), "entry_date": entry.date(),
                     "score_date": t.date(), "near_sale": t in sale_days,
                     "entry_obs_gap_days": entry_gap,
                     "merge_suspects": sum(e["asin"] == p["asin"] and e["type"] == "merge_suspect"
                                           for e in merge_log),
                     **sig, **lab})
    merges = pd.DataFrame(merge_log)
    n_m = int((merges["type"] == "merge_suspect").sum()) if len(merges) else 0
    n_u = int((merges["type"] == "unresolvable_gap").sum()) if len(merges) else 0
    mq = {"merge_suspect": n_m, "unresolvable_gap": n_u,
          "unresolvable_share": n_u / (n_m + n_u) if n_m + n_u else np.nan}
    mq["merge_filter_validatable"] = bool(n_m + n_u == 0 or mq["unresolvable_share"] <= C.UNRESOLVABLE_MAX_SHARE)
    return pd.DataFrame(rows), merges, counts, mq, fs_src

# ══════════════════════════════════════════════════════════════════════
# POWER ANALYSIS (simulation, matches the actual decision rule)
# ══════════════════════════════════════════════════════════════════════

def _sim_scores(n, base, lift, rng):
    """Latent-normal scores where top-decile precision ≈ lift × base."""
    y = (rng.random(n) < base).astype(int)
    if y.sum() == 0:
        y[0] = 1
    shift = _calibrate_shift(base, lift)
    s = rng.normal(0, 1, n) + shift * y
    return y, s

_SHIFT_CACHE = {}
def _calibrate_shift(base, lift):
    key = (round(base, 4), lift)
    if key in _SHIFT_CACHE:
        return _SHIFT_CACHE[key]
    rng = np.random.default_rng(0)
    n = 200_000
    y = (rng.random(n) < base).astype(int)
    z = rng.normal(0, 1, n)
    lo, hi = 0.0, 5.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if top_precision(y, z + mid * y, C.TOP_FRACTION) / max(y.mean(), 1e-9) < lift:
            lo = mid
        else:
            hi = mid
    _SHIFT_CACHE[key] = hi
    return hi

def power(n, base, lift=None, sims=150, n_boot=300):
    lift = C.TARGET_LIFT if lift is None else lift
    rng = np.random.default_rng(1)
    hits = 0
    for k in range(sims):
        y, s = _sim_scores(n, base, lift, rng)
        hits += evaluate(y, s, n_boot=n_boot, seed=k)["passes"]   # assumes no category confound
    return hits / sims

def required_n(base, lift=None, target=None):
    target = C.TARGET_POWER if target is None else target
    for n in (100, 200, 300, 400, 525, 700, 900, 1200, 1600, 2200, 3000, 4000, 6000):
        if base * n < 3:
            continue
        if power(n, base, lift, sims=80, n_boot=200) >= target:
            return n
    return None

# ══════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════

def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))

def survivorship_probe(prods):
    """Share of cohort whose rank data stops >= SURVIVORSHIP_STALE_DAYS before the dataset's
    latest observation. Real new-listing cohorts always contain dead listings; ~0% suggests
    the finder only returned survivors."""
    cohort = [p for p in prods if in_cohort(p) and len(p["rank"])]
    if not cohort:
        return np.nan, 0
    latest = max(p["rank"].index.max() for p in cohort)
    stale = sum((latest - p["rank"].index.max()).days >= C.SURVIVORSHIP_STALE_DAYS for p in cohort)
    return stale / len(cohort), stale

def cmd_pilot(data_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    problems, last_end = check_config()
    df, merges, counts, mq, fs_src = build(data_dir)
    prods = load_dir(data_dir)
    cohort = [p for p in prods if in_cohort(p)]
    stale_share, stale_n = survivorship_probe(prods)
    cats = sorted({p["category"] for p in cohort}, key=str)

    rows = []
    for c in cats:
        n_c = sum(p["category"] == c for p in cohort)
        d_c = df[df["category"] == c] if len(df) else df
        scored = len(d_c)
        wins = int(d_c["winner"].sum()) if scored else 0
        lo, hi = wilson(scored, n_c)
        rows.append({"category": C.CATEGORIES.get(c, c), "cat_id": c, "cohort": n_c,
                     "scored": scored, "scored_rate": scored / n_c if n_c else 0,
                     "scored_rate_lo": lo, "scored_rate_hi": hi, "winners": wins,
                     "reliable": scored >= 10})
    t = pd.DataFrame(rows)
    base = df["winner"].mean() if len(df) else 0
    L = ["# Pilot — per-category rates & budget sizing", "",
         f"Config problems: {problems or 'none'} (latest outcome end {last_end.date()})", "",
         "| Category | Cohort | Scored (entered top-100, observable) | Rate [95% CI] | Winners | Usable? |",
         "|---|---|---|---|---|---|",
         *[f"| {r.category} | {r.cohort} | {r.scored} | {r.scored_rate:.1%} "
           f"[{r.scored_rate_lo:.1%}, {r.scored_rate_hi:.1%}] | {r.winners} | "
           f"{'yes' if r.reliable else 'NO: <10 scored, rate is noise'} |" for r in t.itertuples()],
         f"| **All** | {len(cohort)} | {len(df)} | | {int(df['winner'].sum()) if len(df) else 0} "
         f"(base {base:.1%}) | |", "",
         "## Survivorship probe", "",
         (f"{stale_n} of {len(cohort)} listings ({stale_share:.1%}) stop reporting rank ≥ "
          f"{C.SURVIVORSHIP_STALE_DAYS} days before the latest data point (likely delisted/dead). "
          + ("**≈0% is a red flag:** the finder probably returned survivors only. Do not proceed "
             "until Keepa support answers the delisted-ASIN question." if stale_share < 0.02 else
             "Dead listings are present, so the finder is not returning survivors only. "
             "Still confirm with Keepa support.")) if np.isfinite(stale_share) else "No data.", "",
         "## Merge filter", "",
         f"Suspects {mq['merge_suspect']} · unresolvable gaps {mq['unresolvable_gap']} · share "
         + (f"{mq['unresolvable_share']:.0%} (limit {C.UNRESOLVABLE_MAX_SHARE:.0%})"
            if np.isfinite(mq['unresolvable_share']) else "n/a"), "",
         "## Budget sizing", ""]
    unreliable = t[~t["reliable"]]["category"].tolist() if len(t) else []
    if len(df) >= 10 and base > 0:
        need = required_n(base)
        if need:
            # Stratified full pull: equal listings per category. Size on the LOWER CI of each
            # category's scored rate so a lucky pilot doesn't under-budget.
            per_cat_scored_lo = t["scored_rate_lo"].sum()
            per_cat_scored_pt = t["scored_rate"].sum()
            l_pt = int(np.ceil(need / per_cat_scored_pt)) if per_cat_scored_pt else None
            l_lo = int(np.ceil(need / per_cat_scored_lo)) if per_cat_scored_lo else None
            def tokens(lst):
                listings = lst * len(t)
                pages = int(np.ceil(lst / 100)) * len(t)
                return listings, listings * C.TOKENS_PER_PRODUCT + pages * C.FINDER_TOKENS_PER_REQUEST
            L += [f"Power target {C.TARGET_POWER:.0%} for a true {C.TARGET_LIFT}× lift at base rate "
                  f"{base:.1%} → **~{need} scored products**.", "",
                  "| Scenario | Listings / category | Total listings | Tokens | Days at "
                  f"{C.KEEPA_REFILL_PER_MIN}/min |", "|---|---|---|---|---|"]
            for name, lst in (("point estimate", l_pt), ("conservative (lower CI)", l_lo)):
                if lst:
                    n_l, tok = tokens(lst)
                    L.append(f"| {name} | {lst:,} | {n_l:,} | {tok:,} | "
                             f"{tok / C.KEEPA_REFILL_PER_MIN / 1440:.1f} |")
                else:
                    L.append(f"| {name} | lower-CI rate is 0 — cannot bound | | | |")
            L += ["", "Budget on the **conservative** row. Token costs are unverified config values."]
        else:
            L += ["**Base rate too low: >6,000 scored products needed.** Loosen the winner definition "
                  "or change the cohort *before* locking."]
    else:
        L += ["**Pilot too small to size the budget** (need ≥10 scored products and ≥1 winner)."]
    if unreliable:
        L += ["", f"⚠ Categories with <10 scored products: {unreliable}. Their rates are noise — "
              "top up those categories with a second pilot round (`discover --per-cat` higher; "
              "already-pulled ASINs are skipped) before sizing."]
    L += ["", "Winner rate is pooled across categories (too few winners per category to split). "
          "If the full pull's category mix differs from the pilot's, re-size."]
    open(os.path.join(out_dir, "PILOT.md"), "w").write("\n".join(L) + "\n")
    t.to_csv(os.path.join(out_dir, "pilot_categories.csv"), index=False)
    print("\n".join(L))

def within_category_auc(df):
    """Winner-weighted mean of per-category AUCs. If pooled AUC >> this, the score is
    partly identifying high-turnover categories rather than trends within them."""
    parts = []
    for c, g in df.groupby("category"):
        a = auc(g["winner"], g["score"])
        if np.isfinite(a):
            parts.append((c, a, int(g["winner"].sum()), len(g)))
    if not parts:
        return np.nan, []
    w = sum(p[2] for p in parts)
    return sum(p[1] * p[2] for p in parts) / w, parts

def cmd_run(data_dir, out_dir, unseal=False):
    os.makedirs(out_dir, exist_ok=True)
    problems, last_end = check_config()
    df, merges, counts, mq, fs_src = build(data_dir)
    if len(merges):
        merges.to_csv(os.path.join(out_dir, "merge_events.csv"), index=False)
    if len(df):
        df.sort_values("score", ascending=False).to_csv(os.path.join(out_dir, "products.csv"), index=False)
    if len(df) < 10 or df["winner"].nunique() < 2:
        print(f"[FATAL] too few products/outcome variety: {counts}")
        return None
    res = evaluate(df["winner"].values, df["score"].values, cats=df["category"].values)
    res["power"] = power(len(df), res["base_rate"])
    res["component_auc"] = {k: auc(df["winner"], df[k]) for k in C.WEIGHTS}
    res["within_cat_auc"], res["within_cat_detail"] = within_category_auc(df)
    if problems:
        decision = "INVALID (config problems)"
    elif not C.LOCKED:
        decision = "DIAGNOSTIC ONLY (config not LOCKED)"
    elif not mq["merge_filter_validatable"]:
        decision = ("GO" if res["passes"] else "NO-GO") + " — merge filter NOT validated"
    else:
        decision = "GO" if res["passes"] else "NO-GO"
    if decision.startswith("NO-GO") and res["power"] < C.TARGET_POWER:
        decision += f" (UNDERPOWERED: {res['power']:.0%} power — inconclusive, not a failure)"
    write_report(res, df, counts, mq, fs_src, problems, decision, out_dir)
    ho = df[df["holdout"].astype(bool)]
    sealed = ["# SEALED concurrent holdout — open only after choosing a shadow on train", "",
              f"n={len(ho)}, winners={int(ho['winner'].sum())}, power for a true {C.TARGET_LIFT}× lift ≈ "
              f"{power(len(ho), max(ho['winner'].mean(), 1e-3)) if len(ho) >= 30 else 0:.0%}", "",
              *shadow_table(ho)]
    path = os.path.join(out_dir, "HOLDOUT_SEALED.md")
    open(path, "w").write("\n".join(sealed) + "\n")
    print(("\n".join(sealed)) if unseal else f"Holdout written sealed → {path} (use --unseal-holdout to print)")
    return res, decision

def shadow_table(d):
    w = d["winner"] == 1
    base = d["winner"].mean()
    L = ["| Variant | Triggered | Winners penalised | AUC | Top-10% lift | Within-cat AUC |",
         "|---|---|---|---|---|---|"]
    rows_ = [("**primary** (15%, 48h)", "score", "discount_driven")] + \
            [(n, f"score_shadow_{n}", f"trig_shadow_{n}") for n in C.DISCOUNT_SHADOWS]
    for name, sc, tg in rows_:
        trg = d[tg].astype(bool)
        if d["winner"].nunique() < 2:
            L.append(f"| {name} | {trg.mean():.1%} | – | – | – | – |"); continue
        wa = _within_auc_arr(d["winner"].values, d[sc].values, d["category"].astype(str).values)
        L.append(f"| {name} | {trg.mean():.1%} | {int((trg & w).sum())}/{int(w.sum())} | "
                 f"{auc(d['winner'], d[sc]):.3f} | "
                 f"{top_precision(d['winner'].values, d[sc].values, C.TOP_FRACTION) / base:.2f}× | {wa:.3f} |")
    return L

def price_section(df):
    """Exploratory price diagnostics. Not part of the gate; do not tune weights on these."""
    L = ["## Discount penalty (pre-registered, IN the score)", ""]
    if "discount_driven" in df:
        dd = df["discount_driven"].astype(bool)
        w = df["winner"] == 1
        L += [f"Rule: price at score ≤ {-C.DISCOUNT_DROP_PCT}% vs max of prior {C.DISCOUNT_LOOKBACK_DAYS}d while "
              f"climbing → momentum & mover × {C.DISCOUNT_PENALTY}. Price source: {df['price_source'].value_counts().to_dict()}.", "",
              f"- Triggered on {dd.mean():.1%} of scored products · winner rate when triggered "
              f"{df.loc[dd, 'winner'].mean() if dd.any() else float('nan'):.1%} vs {df.loc[~dd, 'winner'].mean():.1%} otherwise",
              f"- **Winners penalised (collateral damage):** {int((dd & w).sum())} of {int(w.sum())} "
              f"({(dd & w).sum() / max(w.sum(), 1):.0%}). High = penalty is hurting genuine launches with intro pricing.",
              f"- AUC with penalty {auc(df['winner'], df['score']):.3f} vs without {auc(df['winner'], df['score_unpenalized']):.3f} "
              "(report-only: the gate uses the penalised score; switching after seeing this invalidates the test)", ""]
        L += [f"- Discount status at score: {df['discount_status'].value_counts().to_dict()}. "
              f"High `stale` = Keepa samples too sparsely for the rule to judge; those products are never penalised.", ""]
        du = df["discount_status"] == "dip_unconfirmed"
        if du.any():
            rv = df.loc[du, "dip_reverted_48h"]
            L += [f"- **Short dips not penalised** (`dip_unconfirmed`, < {C.DISCOUNT_SUSTAIN_HOURS}h or 1 observation): "
                  f"{int(du.sum())}. Post-hoc, {int((rv == True).sum())} snapped back above threshold within 48h "
                  f"(hijacker *or* Lightning Deal), {int((rv == False).sum())} stayed down (penalty would have "
                  f"fired later), {int(rv.isna().sum())} unknown (no follow-up observation).", ""]
        tr = df[~df["holdout"].astype(bool)]
        L += [f"### Shadow thresholds — TRAIN slice only ({len(tr)} products, holdout of "
              f"{int(df['holdout'].sum())} sealed)", "", *shadow_table(tr),
              "", f"Precommitment: a better-looking shadow does **not** change this decision. To adopt one: "
              f"pick it here on train → `run --unseal-holdout` (concurrent 20%, checks overfitting to products; "
              f"low power, so it can reject but rarely confirm) → fresh pre-registered test on "
              f"{C.HOLDOUT_WINDOW[0]} → {C.HOLDOUT_WINDOW[1]} (checks drift over time).", ""]
        q = df["quarantine"].value_counts().to_dict()
        L += ["### Quarantine (mirrors live: dip on score date → decide 2 days later)", "",
              f"- Outcomes: {q}. `resolved_penalised` = flash sales that would have slipped through "
              f"unpenalised without quarantine.", ""]
        age = pd.to_numeric(df["price_age_hours"], errors="coerce")
        fresh = age <= C.FRESH_PRICE_HOURS
        L += ["### Price freshness at score (Keepa artefact — live prices are never stale)", ""]
        for nm, m in (("fresh ≤%dh" % C.FRESH_PRICE_HOURS, fresh), ("stale", ~fresh & age.notna())):
            d = df[m]
            if len(d) >= 10 and d["winner"].nunique() == 2:
                L.append(f"- {nm}: n={len(d)}, winners {int(d['winner'].sum())}, AUC {auc(d['winner'], d['score']):.3f}")
            else:
                L.append(f"- {nm}: n={len(d)} (too few to score)")
        L += ["", "If the model only separates winners on stale-price products, the discount rule is "
              "being blinded by Keepa sampling — investigate before trusting a GO.", ""]
    L += ["## Price diagnostics (EXPLORATORY — not gated)", ""]
    if "price_at_score" not in df or df["price_at_score"].notna().sum() < 10:
        return L + ["Too little price data."]
    base = df["winner"].mean()
    bands = pd.cut(df["price_at_score"], C.PRICE_BANDS, right=False,
                   labels=[f"₹{a:,}–{b - 1:,}" if np.isfinite(b) else f"₹{a:,}+"
                           for a, b in zip(C.PRICE_BANDS[:-1], C.PRICE_BANDS[1:])])
    t = df.groupby(bands, observed=False).agg(n=("winner", "size"), winners=("winner", "sum"))
    L += ["| Price band (at score) | Products | Winners | Winner rate | 95% CI |", "|---|---|---|---|---|"]
    for band, r in t.iterrows():
        if r.n:
            lo, hi = wilson(int(r.winners), int(r.n))
            L.append(f"| {band} | {int(r.n)} | {int(r.winners)} | {r.winners / r.n:.1%} | [{lo:.1%}, {hi:.1%}] |")
    L += ["", "| Feature at score date | AUC vs winner | Non-missing |", "|---|---|---|"]
    for f in ("price_chg_14d_pct", "discount_vs_list_pct", "sellers_at_score"):
        m = df[f].notna()
        L.append(f"| {f} | {auc(df.loc[m, 'winner'], -df.loc[m, f] if f == 'price_chg_14d_pct' else df.loc[m, f]):.3f} | {m.sum()} |")
    am = df.groupby("amazon_sells")["winner"].agg(["size", "mean"])
    L.append("")
    L.append("Amazon itself selling at score date: " + ", ".join(
        f"{'yes' if k else 'no'} → {r['mean']:.1%} winners (n={int(r['size'])})" for k, r in am.iterrows()))
    w = df[df["winner"] == 1]
    if len(w):
        share = w["outcome_price_cut"].mean()
        L += ["", f"**Bought winners:** {share:.0%} of winners had a ≥{C.PRICE_CUT_PCT}% price cut during the "
              f"outcome window (non-winners: {df.loc[df['winner'] == 0, 'outcome_price_cut'].mean():.0%}). "
              + ("⚠ High: part of 'winning' was bought with discounts — margin may not survive."
                 if share > 0.3 else "Low: winners mostly held price.")]
    return L

def write_report(r, df, counts, mq, fs_src, problems, decision, out_dir):
    ci = lambda c: f"[{c[0]:.3f}, {c[1]:.3f}]"
    us = mq["unresolvable_share"]
    L = [f"# Bazaar Radar — Keepa backtest: **{decision}**", "",
         f"Generated {datetime.now():%Y-%m-%d %H:%M} · config LOCKED={C.LOCKED} · "
         f"cohort={C.COHORT_MODE} ({C.FIRST_SEEN_START}→{C.FIRST_SEEN_END}, entries ≤ {C.ENTRY_CUTOFF}) · "
         f"scored on entry+{C.FLAG_DELAY_DAYS}d", "",
         f"Winner = category-relative weekly median rank ≤ {C.WIN_TOP_N} in ≥{C.WIN_MIN_WEEKS}/"
         f"{C.OUTCOME_WEEKS} weeks starting {C.LEAD_DAYS}d after scoring, AND ≥{C.WIN_MIN_REVIEW_GAIN} "
         f"merge-cleaned reviews AND ≥{C.WIN_MIN_REVIEW_GROWTH:.0%} review growth in that window.", "",
         "| Metric | Point | 95% CI |", "|---|---|---|",
         f"| Scored products | {r['n']} | |",
         f"| Winners (base rate) | {r['winners']} ({r['base_rate']:.1%}) | |",
         f"| Top {C.TOP_FRACTION:.0%} precision | {r['top_precision']:.3f} | {ci(r['top_precision_ci'])} |",
         f"| **Lift vs base** (gate: lower > {C.GO_LIFT_LOWER}) | {r['lift']:.2f}× | {ci(r['lift_ci'])} |",
         f"| **AUC** (gate: lower > {C.GO_AUC_LOWER}) | {r['auc']:.3f} | {ci(r['auc_ci'])} |",
         f"| **Within-category AUC** (gate: lower > {C.GO_WITHIN_AUC_LOWER}) | {r['within_auc']:.3f} | "
         f"{ci(r['within_auc_ci'])} |",
         "| Gates | " + ", ".join(k + (": PASS" if v else ": FAIL") for k, v in r["gates"].items()) + " | |",
         f"| Power to detect a true {C.TARGET_LIFT}× lift at this n | {r['power']:.0%} | "
         f"assumes signal is within-category; low-winner categories widen the within-AUC CI |", "",
         f"**Category-confound diagnostic** (report-only): pooled AUC {r['auc']:.3f} vs within-category "
         f"{r['within_cat_auc']:.3f}"
         + (" — ⚠ pooled AUC is >0.05 higher: part of the signal is *which category*, not trend."
            if r['auc'] - r['within_cat_auc'] > 0.05 else " — no material category confound.")
         + "  Per category (id, AUC, winners, n): " + ", ".join(
             f"{c}: {a:.2f} ({w}/{n})" for c, a, w, n in r["within_cat_detail"]), "",
         "## Precision curve (report-only, not part of the gate)", "",
         "| Top fraction | Precision | Lift |", "|---|---|---|",
         *[f"| {f:.0%} | {p:.3f} | {p / r['base_rate']:.2f}× |" for f, p in r["precision_curve"].items()], "",
         "## Single-component AUC (which parts carry signal)", "",
         "| Component | Weight | AUC |", "|---|---|---|",
         *[f"| {k} | {C.WEIGHTS[k] / sum(C.WEIGHTS.values()):.2f} | {v:.3f} |" for k, v in r["component_auc"].items()], "",
         *price_section(df), "",
         "## Data quality", "",
         f"- Funnel: {counts}",
         f"- first_seen source: {fs_src}",
         f"- Merge detector: {mq['merge_suspect']} merge suspects, {mq['unresolvable_gap']} unresolvable "
         f"gaps → unresolvable share {us:.0%} (limit {C.UNRESOLVABLE_MAX_SHARE:.0%}; denominator = "
         f"suspected step-jumps) → validatable: {mq['merge_filter_validatable']}" if np.isfinite(us) else
         "- Merge detector: no suspected step-jumps",
         f"- Entry-date uncertainty (days between last pre-entry Keepa observation and first "
         f"top-{C.ENTRY_TOP_N} observation): median {df['entry_obs_gap_days'].median():.0f}, "
         f"{(df['entry_obs_gap_days'] > 3).mean():.0%} of products > 3 days. Large gaps mean "
         f"true entry may be earlier → launch_speed/persistence are biased for those products.",
         f"- Scored within ±{C.SALE_BUFFER_DAYS}d of a listed sale: {df['near_sale'].mean():.1%}",
         f"- Config problems: {problems or 'none'}", "",
         "## Caveats (keep when sharing)", "",
         "- Score is an *approximation* of live v1: Movers & Shakers ≈ 1-day rank % gain; "
         "New Releases replaced by launch_speed; breadth dropped.",
         "- Cohort = new listings only. Results say nothing about established products climbing.",
         "- Products Keepa no longer returns may be missing (survivorship); confirm with Keepa.",
         "- A NO-GO at low power is inconclusive. A NO-GO at adequate power means the "
         "*approximated* model failed; read component AUCs before concluding v1 is wrong."]
    open(os.path.join(out_dir, "REPORT.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L[:16]))

# ── Keepa API (rate-limited + resumable: see keepa_client.py) ─────────

def _client():
    from keepa_client import KeepaClient
    k = os.environ.get("KEEPA_API_KEY")
    if not k:
        sys.exit("Set KEEPA_API_KEY")
    return KeepaClient(k, refill_per_min=C.KEEPA_REFILL_PER_MIN)

def cmd_discover(out_file, per_cat):
    """Stratified first-seen cohort: equal quota per category.
    VERIFY selection keys against Keepa Product Finder docs before spending tokens."""
    import keepa_client
    if not C.CATEGORIES:
        sys.exit("Fill CATEGORIES in config.py first")
    lo = ts_to_keepa_min(C.FIRST_SEEN_START)
    hi = ts_to_keepa_min(pd.Timestamp(C.FIRST_SEEN_END) + D(1))
    sel = {C.FINDER_FIRST_SEEN_FIELD + "_gte": lo, C.FINDER_FIRST_SEEN_FIELD + "_lte": hi}
    got = keepa_client.discover(_client(), list(C.CATEGORIES), sel, per_cat, out_file,
                                C.FINDER_TOKENS_PER_REQUEST)
    print(f"Per-category cohort: {got}")

def read_asin_file(path):
    """Lines 'ASIN' or 'ASIN,category' → (asins, {asin: category})."""
    asins, cats = [], {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        a, _, c = line.partition(",")
        asins.append(a)
        if c:
            cats[a] = c
    return asins, cats

def cmd_fetch(asin_file, cache_dir):
    import keepa_client
    asins, _ = read_asin_file(asin_file)
    keepa_client.fetch(_client(), asins, cache_dir, C.TOKENS_PER_PRODUCT,
                       batch=C.FETCH_BATCH, extra_params={"rating": 1, "history": 1, **({"buybox": 1} if C.FETCH_BUYBOX else {})})

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("discover"); a.add_argument("--out", default="asins.txt")
    a.add_argument("--per-cat", type=int, default=C.PILOT_PER_CATEGORY)
    b = sub.add_parser("fetch"); b.add_argument("--asins", required=True); b.add_argument("--data", default="keepa_cache")
    c = sub.add_parser("pilot"); c.add_argument("--data", default="keepa_cache"); c.add_argument("--out", default="results")
    d = sub.add_parser("run"); d.add_argument("--data", default="keepa_cache"); d.add_argument("--out", default="results")
    d.add_argument("--unseal-holdout", action="store_true")
    g = ap.parse_args()
    {"discover": lambda: cmd_discover(g.out, g.per_cat), "fetch": lambda: cmd_fetch(g.asins, g.data),
     "pilot": lambda: cmd_pilot(g.data, g.out), "run": lambda: cmd_run(g.data, g.out, getattr(g, "unseal_holdout", False))}[g.cmd]()
