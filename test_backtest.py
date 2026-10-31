#!/usr/bin/env python3
"""Harness self-tests (synthetic data only). Run: python test_backtest.py
Generates its own synthetic cohorts in ./_selftest/ and checks:
  1  config horizon check passes for the shipped config and fails for a bad one
  2  cohort excludes listings outside the first-seen window
  3  merge detector: recall/precision vs planted merges
  4  Keepa gaps hiding big organic growth → unresolvable, never merges
  5  no look-ahead: scrambling data after the score date leaves scores unchanged
  6  persistence & launch_speed actually vary (not constant)
  7  null world (no early signal): decision is not GO
  8  shuffled scores: gate passes in <= 1/20 runs (false-GO control)
  8b category-only score fools pooled AUC but fails the within-category gate
  9  power: simulated true 2x lift at n=900, base 8% → power reported, >= 60%
"""
import os, sys, copy, shutil
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, keepa_backtest as kb, synthetic

W = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest")
ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond); print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)

shutil.rmtree(W, ignore_errors=True)
synthetic.generate(os.path.join(W, "sig"), n=1500, seed=7, signal=1.0)
synthetic.generate(os.path.join(W, "null"), n=1500, seed=8, signal=0.0)
truth = pd.read_csv(os.path.join(W, "sig", "_truth.csv")).set_index("asin")

# 1 config
probs, end = kb.check_config()
check("shipped config horizon OK", not probs, f"latest outcome end {end.date()}")
old = C.ENTRY_CUTOFF; C.ENTRY_CUTOFF = "2025-05-15"
check("bad ENTRY_CUTOFF is rejected", bool(kb.check_config()[0])); C.ENTRY_CUTOFF = old

# 2-6 on signal cohort
prods = kb.load_dir(os.path.join(W, "sig"))
cohort = [p for p in prods if kb.in_cohort(p)]
outside = set(truth[truth.outside_window].index)
check("cohort excludes out-of-window listings", not ({p["asin"] for p in cohort} & outside),
      f"{len(cohort)} in cohort, {len(outside)} planted outside")

ev = []
for p in cohort:
    e, p["reviews_clean"] = kb.detect_merges(p); ev += e
ev = pd.DataFrame(ev)
ms = set(ev[ev.type == "merge_suspect"].asin); gp = set(ev[ev.type == "unresolvable_gap"].asin)
in_c = {p["asin"] for p in cohort}
tm = set(truth[truth.true_merge.notna()].index) & in_c
tg = set(truth[truth.true_gap.notna()].index) & in_c
check("merge recall >= 90%", len(ms & tm) >= .9 * len(tm), f"{len(ms & tm)}/{len(tm)}")
check("merge precision >= 80%", not ms or len(ms & tm) >= .8 * len(ms), f"{len(ms - tm)} false of {len(ms)}")
check("gaps never flagged as merges", not (ms & tg), f"{len(gp & tg)}/{len(tg)} gaps → unresolvable")

g0, g1 = pd.Timestamp(C.FIRST_SEEN_START) - pd.Timedelta(days=90), pd.Timestamp(C.HORIZON_LIMIT)
cm = kb.category_logrank_median(cohort, g0, g1)
diff = tested = 0
for p in cohort:
    entry = kb.first_entry(p)
    if entry is None: continue
    t = entry + pd.Timedelta(days=C.FLAG_DELAY_DAYS)
    a = kb.signals_at(kb.truncate(p, t), entry, t, cm.get(p["category"], {}))["score"]
    q = copy.deepcopy(p); cut = t + pd.Timedelta(days=1)
    for k in ("rank", "reviews", "reviews_clean", "rating", "sellers", "price"):
        q[k] = q[k].where(q[k].index < cut, q[k] * 37 + 999)
    b = kb.signals_at(kb.truncate(q, t), entry, t, cm.get(p["category"], {}))["score"]
    tested += 1; diff += abs(a - b) > 1e-9
    if tested >= 150: break
check("no look-ahead", diff == 0, f"{tested} products scrambled after score date")

df, merges, counts, mq, _ = kb.build(os.path.join(W, "sig"))
check("persistence varies", df["persistence"].nunique() > 1, f"values {sorted(df['persistence'].round(2).unique())[:6]}")
check("launch_speed varies", df["launch_speed"].std() > 0.05, f"std {df['launch_speed'].std():.2f}")
r_sig = kb.evaluate(df["winner"], df["score"])
print(f"      signal world: n={r_sig['n']} base={r_sig['base_rate']:.1%} lift={r_sig['lift']:.2f} "
      f"CI={tuple(round(x, 2) for x in r_sig['lift_ci'])} AUC={r_sig['auc']:.2f} funnel={counts}")
check("signal world passes gate", r_sig["passes"])

# 7 null world
dfn, _, cn, _, _ = kb.build(os.path.join(W, "null"))
r_null = kb.evaluate(dfn["winner"], dfn["score"])
print(f"      null world:   n={r_null['n']} base={r_null['base_rate']:.1%} lift={r_null['lift']:.2f} "
      f"CI={tuple(round(x, 2) for x in r_null['lift_ci'])} AUC={r_null['auc']:.2f}")
check("null world (no early signal) does not pass", not r_null["passes"])

# 8 shuffled
rng = np.random.default_rng(3)
passes = sum(kb.evaluate(df["winner"], rng.permutation(df["score"].values), n_boot=300, seed=k)["passes"]
             for k in range(20))
check("shuffled scores pass <= 1/20", passes <= 1, f"{passes}/20")

# 8a price diagnostics recover planted price cuts (40% of winner-kind, 10% of others)
x = df.join(truth["kind"], on="asin")
w_cut = x.loc[x["kind"] == "winner", "outcome_price_cut"].mean()
o_cut = x.loc[x["kind"] != "winner", "outcome_price_cut"].mean()
check("outcome price-cut detection ≈ planted rates", abs(w_cut - .4) < .12 and abs(o_cut - .1) < .06,
      f"winner-kind {w_cut:.0%} (planted 40%), others {o_cut:.0%} (planted 10%)")
check("default pull uses lowest-new price (no Buy Box tokens)", (df["price_source"] == "lowest_new").all()
      and df["price_at_score"].notna().mean() > .95)
trip = kb._series_triplet([kb.ts_to_keepa_min("2025-03-01"), 49900, 4000,
                           kb.ts_to_keepa_min("2025-03-02"), -1, 0])
check("Buy Box triplet parser (kept for stage 2)", len(trip) == 1 and abs(trip.iloc[0] - 539.0) < 1e-9,
      f"{trip.to_dict()}")

# 8c discount penalty (pre-registered, in the score)
x = df.join(truth[["kind", "flash_sale", "hijack", "late_flash"]], on="asin")
fs_mask = x["flash_sale"].astype(bool) | x["late_flash"].astype(bool)   # any real planted price cut
dd = x["discount_driven"].astype(bool)
climbed = (x["momentum_unpenalized"] > 0) | (x["mover_unpenalized"] > 0)
rec = (dd & fs_mask & climbed).sum() / max((fs_mask & climbed).sum(), 1)
fpr = (dd & ~fs_mask).sum() / max((~fs_mask).sum(), 1)
check("penalty catches planted flash sales (early + late, after quarantine)", rec >= .7,
      f"recall {rec:.0%} of {(fs_mask & climbed).sum()}")
check("penalty rarely fires without a price cut", fpr <= .05, f"false-trigger rate {fpr:.1%}")
check("penalised score <= unpenalised, equal when not triggered",
      (x["score"] <= x["score_unpenalized"] + 1e-9).all() and (x.loc[~dd, "score"] - x.loc[~dd, "score_unpenalized"]).abs().max() < 1e-9)
sp = x[x["kind"] == "spike"]
check("spikes lose score on average", (sp["score_unpenalized"] - sp["score"]).mean() > 0,
      f"mean spike score {sp['score_unpenalized'].mean():.1f} → {sp['score'].mean():.1f}")
hj = x["hijack"].astype(bool) & ~x["flash_sale"].astype(bool)
lf = x["late_flash"].astype(bool) & ((x["momentum_unpenalized"] > 0) | (x["mover_unpenalized"] > 0))
lfq = x.loc[lf, "quarantine"].value_counts().to_dict()
check("late flash sales (cut 1 day before score) go to quarantine", (x.loc[lf, "quarantine"] != "none").mean() >= .8,
      f"{int(lf.sum())} planted · {lfq}")
check("quarantine resolves late flash sales as PENALISED", (x.loc[lf, "quarantine"] == "resolved_penalised").mean() >= .6
      and x.loc[lf & (x["quarantine"] == "resolved_penalised"), "discount_driven"].all())
hq = x.loc[hj, "quarantine"].value_counts().to_dict()
check("quarantine releases hijacker dips as ORGANIC", (x.loc[hj, "quarantine"] == "resolved_organic").mean() >= .8, str(hq))
check("quarantined products scored on resolution date (entry+3+2)",
      (pd.to_datetime(x.loc[x["quarantine"] != "none", "score_date"]) -
       pd.to_datetime(x.loc[x["quarantine"] != "none", "entry_date"])).dt.days.eq(C.FLAG_DELAY_DAYS + C.QUARANTINE_DAYS).all())
hf = x["holdout"].mean()
check("concurrent holdout ≈ 20%, deterministic", .15 <= hf <= .25 and
      all(kb.in_holdout(a) == h for a, h in zip(x["asin"][:50], x["holdout"][:50])), f"{hf:.1%}")
check("price age recorded", x["price_age_hours"].notna().mean() > .95)
check("hijacker dips (1-day, score date) are NOT penalised", not (x.loc[hj, "discount_driven"]).any(),
      f"{int(hj.sum())} planted")
first = x.loc[hj, "status_at_first_score"] if "status_at_first_score" in x else pd.Series(dtype=object)
check("at first score the dip was visible (unguarded rule would have fired)", (first == "dip_unconfirmed").mean() >= .8,
      f"{(first == 'dip_unconfirmed').mean():.0%} of hijacks were dip_unconfirmed on the score date")
check("shadow columns present for every variant",
      all(f"score_shadow_{n}" in x for n in kb.C.DISCOUNT_SHADOWS))
check("no_penalty shadow == unpenalised score", (x["score_shadow_no_penalty"] - x["score_unpenalized"]).abs().max() < 1e-9)
wn = x[x["winner"] == 1]
print(f"      collateral: {wn['discount_driven'].mean():.0%} of winners penalised (planted intro-price cuts ~5%)")

# 8b category-only score: knows which category, nothing about the product.
#    Must pass the pooled gates (that's the trap) and fail the within-category gate.
rng2 = np.random.default_rng(5)
n = 1200
cats = rng2.choice(["fashion", "kitchen", "electronics"], n, p=[.5, .3, .2])
rate = {"fashion": .20, "kitchen": .08, "electronics": .02}
y = (rng2.random(n) < np.vectorize(rate.get)(cats)).astype(int)
s_cat = np.vectorize(rate.get)(cats) + rng2.normal(0, .001, n)
r_cat = kb.evaluate(y, s_cat, cats=cats, n_boot=500)
print(f"      category-only score: pooled AUC={r_cat['auc']:.2f} lift CI={tuple(round(x, 2) for x in r_cat['lift_ci'])} "
      f"within AUC={r_cat['within_auc']:.2f} CI={tuple(round(x, 2) for x in r_cat['within_auc_ci'])}")
check("category-only score fools the pooled AUC gate (the trap is real)", r_cat["gates"]["auc"],
      f"lift gate {'passed' if r_cat['gates']['lift'] else 'caught it'}")
check("category-only score FAILS within-category gate", not r_cat["gates"]["within_category_auc"] and not r_cat["passes"])
r_sig_c = kb.evaluate(df["winner"], df["score"], cats=df["category"], n_boot=500)
check("genuine signal passes all three gates", r_sig_c["passes"], str(r_sig_c["gates"]))

# 9 power
pw = kb.power(900, 0.08, 2.0, sims=100, n_boot=300)
check("power at n=900, base 8%, true 2x lift >= 60%", pw >= .6, f"{pw:.0%}")
pw_s = kb.power(150, 0.08, 2.0, sims=100, n_boot=300)
print(f"      (same effect at n=150: power {pw_s:.0%} — why small pilots can't decide GO/NO-GO)")

print("\nALL PASS" if ok else "\nSOME FAILED")
shutil.rmtree(W, ignore_errors=True)
sys.exit(0 if ok else 1)
