#!/usr/bin/env python3
"""Synthetic Keepa-format NEW-LISTING cohort with known ground truth — tests the harness only.
Writes <ASIN>.json per product + _truth.csv. Never mix with a real Keepa cache.

Kinds: never (most listings, never reach top-100) · winner · spike · climber_fade · steady
Planted artefacts: ASIN merges (dense obs), Keepa gaps hiding big organic growth,
a site-wide sale lift, ~10% listed outside the first-seen window (must be excluded).
--signal = probability a product's pre-score behaviour matches its true outcome (0 = null world)."""
import os, json, argparse
import numpy as np, pandas as pd

OFF = 21564000
def km(ts): return int(pd.Timestamp(ts).value // 60_000_000_000) - OFF

def flat(days, vals, keep):
    out = []
    for d, v, k in zip(days, vals, keep):
        if k and np.isfinite(v):
            out += [km(d) + int(np.random.randint(0, 600)), int(round(v))]
    return out

ENTERING = ["winner", "spike", "climber_fade", "steady"]
ENTER_P = np.array([.05, .12, .08, .20]) / .45

def trajectory(kind, entry, rng, n):
    """Daily log-rank and review-rate path for one behaviour type."""
    lr = np.log(rng.uniform(400, 3000)) + rng.normal(0, .15, n)
    rate = np.full(n, rng.uniform(0.1, 1.5))
    if kind == "winner":
        lr[entry - 14:entry + 1] = np.linspace(lr[entry - 14], np.log(rng.uniform(60, 95)), 15)
        lr[entry + 1:] = np.log(rng.uniform(8, 35)) + rng.normal(0, .2, n - entry - 1)
        top = rng.uniform(8, 30)
        rate[entry - 14:entry + 6] = np.linspace(rate[0], top, 20); rate[entry + 6:] = top
    elif kind == "spike":
        lr[entry] = np.log(rng.uniform(30, 95)); lr[entry + 1:entry + 4] = np.log(rng.uniform(130, 300))
        lr[entry + 4:] = np.log(rng.uniform(300, 1500)) + rng.normal(0, .2, n - entry - 4)
        rate[entry - 3:entry + 3] = rng.uniform(2, 10)
    elif kind == "climber_fade":
        lr[entry - 14:entry + 1] = np.linspace(lr[entry - 14], np.log(rng.uniform(50, 95)), 15)
        lr[entry + 1:entry + 30] = np.log(rng.uniform(40, 99)) + rng.normal(0, .2, 29)
        lr[entry + 30:] = np.log(rng.uniform(150, 600)) + rng.normal(0, .3, n - entry - 30)
        rate[entry - 14:entry + 25] = rng.uniform(3, 10)
    elif kind == "steady":
        lr[entry] = np.log(rng.uniform(70, 99))
        lr[entry + 1:] = np.log(rng.uniform(60, 200)) + rng.normal(0, .3, n - entry - 1)
        rate[entry:] = rng.uniform(1, 4)
    else:
        lr = np.maximum(lr, np.log(140))
    return lr, rate

def make(i, cat, kind, rng, days, sale, signal, outside, dead_p=0.0, buybox=False):
    n = len(days)
    w0 = int(np.searchsorted(days, pd.Timestamp("2025-02-01")))
    w1 = int(np.searchsorted(days, pd.Timestamp("2025-03-15")))
    cut = int(np.searchsorted(days, pd.Timestamp("2025-04-15")))
    li = int(rng.integers(w1 + 5, w1 + 30)) if outside else int(rng.integers(w0, w1 + 1))
    entry = min(li + int(rng.integers(5, 40)), cut - 1)
    lr, rate = trajectory(kind, entry, rng, n)
    # Pre-score behaviour (through entry+3) follows the true kind with probability `signal`,
    # else a random entering kind → signal=0 makes early data independent of the outcome.
    if kind != "never" and rng.random() >= signal:
        pre = rng.choice(ENTERING, p=ENTER_P)
        lr_p, rate_p = trajectory(pre, entry, rng, n)
        s = entry + 4
        lr[:s], rate[:s] = lr_p[:s], rate_p[:s]
    rank = np.exp(lr)
    rank[sale] /= 2.2
    rev = rng.uniform(0, 20) + np.cumsum(rng.poisson(rate))
    rating = np.full(n, rng.uniform(3.6, 4.5)) + rng.normal(0, .02, n)
    keep = rng.random(n) < .7
    keep[:li] = False
    keep[li] = True
    if kind != "never":
        keep[entry] = True      # entry must be observed, or the harness finds a later entry
                                # from post-score data and the null world leaks outcome info
    if kind == "never" and rng.random() < dead_p:          # listing dies: data stops
        keep[int(rng.integers(li + 10, n - 70)):] = False
    tm = tg = None
    if kind != "never" and rng.random() < .10:
        d = int(rng.integers(entry + 5, n - 10)); keep[d - 3:d + 3] = True
        rev[d:] += max(rev[d] * rng.uniform(.6, 2.5), 200); rating[d:] += rng.choice([-.2, .2])
        tm = days[d].date()
    elif kind != "never" and rng.random() < .08:
        d = int(rng.integers(entry + 15, n - 10)); keep[d - 10:d] = False; keep[d] = True
        bump = max(rev[d] * .4, 150)
        rev[d - 10:] += np.r_[np.linspace(0, bump, 10), np.full(n - d, bump)]
        tg = days[d].date()
    asin = f"B0SYN{i:05d}"
    csv = [[] for _ in range(19)]
    # prices: list price, buy box (+shipping, triplets), Amazon's own price on ~30% of products.
    base_p = rng.choice([rng.uniform(199, 499), rng.uniform(500, 999), rng.uniform(1000, 1999),
                         rng.uniform(2000, 4999), rng.uniform(5000, 15000)], p=[.3, .3, .2, .15, .05])
    pr = np.full(n, base_p)
    if kind != "never" and rng.random() < (.4 if kind == "winner" else .1):
        pr[entry + 25:] *= rng.uniform(.6, .8)           # cut during outcome window ("bought" outcome)
    # flash sale around entry: common for spikes/fades (bought rank), rare for winners (intro pricing)
    flash = rng.random() < {"spike": .6, "climber_fade": .3, "winner": .05}.get(kind, 0)
    # late flash: cut starts the day BEFORE the score date (entry+2), lasts 8 days — the
    # "score-day false negative" case. Without quarantine it is scored unpenalised.
    late_flash = (not flash) and kind in ("spike", "climber_fade") and rng.random() < .5
    if late_flash:
        pr[entry + 2:entry + 10] *= rng.uniform(.55, .7)
        keep[entry + 1:entry + 6] = True
    if flash:
        pr[max(entry - 2, 0):entry + 10] *= rng.uniform(.55, .75)
    # hijacker: one-day ~50% dip on the score date (entry+3), snaps back next day. Organic kinds only.
    hijack = kind in ("winner", "steady") and not flash and not late_flash and rng.random() < .2
    if hijack:
        d = entry + 3
        pr[d] *= rng.uniform(.4, .6)
        keep[d] = keep[d + 1] = True
    csv[0] = flat(days, pr * 100, keep) if rng.random() < .3 else []
    csv[1] = flat(days, pr * 100, keep)
    csv[4] = flat(days, np.full(n, base_p * rng.uniform(1.2, 2.5)) * 100, keep)
    bb = []
    for d_, v, k_ in zip(days, pr, keep):
        if k_:
            bb += [km(d_) + int(np.random.randint(0, 600)), int(round(v * 100)), int(rng.choice([0, 0, 4000]))]
    csv[18] = bb if buybox else []
    csv[3] = flat(days, rank, keep)
    csv[11] = flat(days, np.full(n, rng.integers(1, 30)), keep)
    csv[16] = flat(days, rating * 10, keep)
    csv[17] = flat(days, rev, keep)
    prod = {"asin": asin, "title": f"Synthetic {kind} {i}", "rootCategory": cat,
            "listedSince": km(days[li]), "csv": csv}
    return prod, {"asin": asin, "kind": kind, "category": cat, "outside_window": bool(outside),
                  "true_merge": tm, "true_gap": tg,
                  "hijack": hijack, "late_flash": bool(late_flash),
                  "flash_sale": bool(flash)}

# Category profiles: (P(never enters top-100), share of cohort). 1001 is consolidated
# (electronics-like), 1003 fragmented (fashion-like).
CAT_PROFILE = {1001: .85, 1002: .55, 1003: .35}

def generate(out, n=1500, seed=7, signal=1.0, dead_p=0.0, stratified_per_cat=None, buybox=False):
    rng = np.random.default_rng(seed); np.random.seed(seed)
    os.makedirs(out, exist_ok=True)
    days = pd.date_range("2024-12-01", "2025-07-31")
    sale = np.asarray((days >= "2025-05-01") & (days <= "2025-05-06"))
    cats = ([c for c in CAT_PROFILE for _ in range(stratified_per_cat)] if stratified_per_cat
            else list(rng.choice(list(CAT_PROFILE), n)))
    truth = []
    for i, cat in enumerate(cats):
        pn = CAT_PROFILE[cat]
        k = rng.choice(["never"] + ENTERING, p=[pn] + list(ENTER_P * (1 - pn)))
        p, t = make(i, int(cat), k, rng, days, sale, signal, outside=rng.random() < .10, dead_p=dead_p, buybox=buybox)
        json.dump(p, open(os.path.join(out, f"{p['asin']}.json"), "w")); truth.append(t)
    pd.DataFrame(truth).to_csv(os.path.join(out, "_truth.csv"), index=False)
    print(f"wrote {len(cats)} synthetic listings (signal={signal}, dead_p={dead_p}) → {out}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="synth_cache"); ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=7); ap.add_argument("--signal", type=float, default=1.0)
    ap.add_argument("--dead", type=float, default=0.25); ap.add_argument("--per-cat", type=int, default=None)
    a = ap.parse_args(); generate(a.out, a.n, a.seed, a.signal, a.dead, a.per_cat)
