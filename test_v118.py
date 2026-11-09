"""v1.18 checks: season windows, pick filters, firecracker flag, anchor sampling, estimator."""
import os, sys
os.environ.setdefault("BR_MODE", "daily")
import pandas as pd
import season, compliance, picks, estimate
import trend_fetcher as tf

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

# season
check("on Diwali day → no affiliate, no stock", not season.window("SEASONAL: Diwali", "2026-11-08")["affiliate_ok"])
check("19 days before → affiliate ok, too late to stock",
      season.window("SEASONAL: Diwali", "2026-10-20")["affiliate_ok"] and not season.window("SEASONAL: Diwali", "2026-10-20")["stock_ok"])
check("~3 months before → stock window", season.window("SEASONAL: Diwali", "2027-08-01")["stock_ok"])
check("day after → next year too early", not season.window("SEASONAL: Diwali", "2026-11-09")["stock_ok"])
check("unknown future date → blocked", not season.window("SEASONAL: Holi", "2027-04-01")["affiliate_ok"])
check("blank cell read as NaN → not seasonal", season.window(float("nan"), "2026-11-09")["affiliate_ok"] and season.window("nan", "2026-11-09")["affiliate_ok"])
check("not seasonal → ok", season.window("", "2026-11-09")["affiliate_ok"])
check("winter wraps year end", season.window("SEASONAL: Winter", "2027-01-10")["affiliate_ok"])

# firecrackers
check("firecracker flagged", "EXPLOSIVES" in compliance.compliance_flags("Green crackers sparkler box", "kitchen"))
check("bath bomb not flagged", "EXPLOSIVES" not in compliance.compliance_flags("Lavender bath bombs", "beauty"))

# picks filters
sig = pd.DataFrame([
    {"asin": "A1", "title": "Steel water bottle 1L", "category_slug": "kitchen", "trend_score": 60, "rank_change": 40, "seasonal": "", "compliance": "NOT FLAGGED"},
    {"asin": "A2", "title": "Solar light outdoor", "category_slug": "garden", "trend_score": 59, "rank_change": -1, "seasonal": "", "compliance": "NOT FLAGGED"},
    {"asin": "A3", "title": "Diwali string light", "category_slug": "kitchen", "trend_score": 58, "rank_change": 50, "seasonal": "SEASONAL: Diwali", "compliance": "NOT FLAGGED"},
    {"asin": "A4", "title": "Diwali line cracker", "category_slug": "sports", "trend_score": 57, "rank_change": 60, "seasonal": "SEASONAL: Diwali", "compliance": "REVIEW: EXPLOSIVES (firecrackers)"},
    {"asin": "A5", "title": "Gym gloves pair", "category_slug": "sports", "trend_score": 50, "rank_change": 12, "seasonal": "", "compliance": "NOT FLAGGED"},
])
rej = []
ch = picks.select_picks(sig, n=8, today="2026-11-08", rejected=rej)
check("only genuinely rising, in-window, non-explosive picks", list(ch["asin"]) == ["A1", "A5"], list(ch["asin"]))
check("rejections explained", {r[1].split(" ")[0] for r in rej} >= {"explosives/firecracker", "Diwali"}, rej)

# anchor sampling
bs = pd.DataFrame({"category_slug": ["garden"] * 100, "rank": list(range(1, 101)), "asin": [f"G{i}" for i in range(1, 101)]})
t = tf.detail_targets(pd.DataFrame({"asin": ["S1", "S2", "S3", "S4"]}), {"bestsellers": bs}, 6, "2026-11-09")
check("detail targets = 3 top signals + 3 spread-rank anchors", t == ["S1", "S2", "S3", "G1", "G5", "G15"], t)

# product keywords: superseded in v1.19 by the generic-word test at the end (needs ≥4 titles per word)

# estimator on synthetic data: anchors at ranks 1/10/50 → curve; product at rank 10
days = [f"2026-11-{d:02d}" for d in range(1, 11)]
H = []
for d in days:
    for r in range(1, 101):
        H.append({"date": d, "list": "bestsellers", "category_slug": "kitchen", "asin": f"K{r}", "rank": r,
                  "price_inr": "500", "rating": "", "reviews": "", "title": f"Kitchen thing number {r}"})
H = pd.DataFrame(H).astype(str)
DH = pd.DataFrame({"date": ["2026-11-10"] * 3, "asin": ["K1", "K10", "K50"], "bought_past_month_min": ["5000", "1000", "200"]})
data = {"HISTORY": H, "DETAIL_HISTORY": DH, "TREND_SIGNALS": pd.DataFrame({"asin": ["K10"], "trend_score": ["50"]})}
r = estimate.analyze("K10", data, members=1000, today="2026-11-10")
check("units/month for rank 10 bracket the anchor (1000+)", r["units_month_listing"] and r["units_month_listing"][0] < 1000 * 1.9 and r["units_month_listing"][2] > 1000,
      r["units_month_listing"])
check("stable list → long median life", r["median_life_days"] >= 60, r["median_life_days"])
check("affiliate range ordered P10≤P50≤P90", r["aff_3m"][0] <= r["aff_3m"][1] <= r["aff_3m"][2], r["aff_3m"])
check("no quote → no selling ₹, says what's missing", r["flip_3m"] is None and any("profit" in b for b in r["blockers"]))
check("assumptions listed, grade D", r["grade"] == "D" and "commission 2–8%" in r["assumptions"], (r["grade"], r["assumptions"]))
data["COMMISSION"] = pd.DataFrame({"category_slug": ["kitchen"], "commission_pct": ["6"]})
r2 = estimate.analyze("K10", data, members=1000, today="2026-11-10")
check("filled commission replaces the assumption", "commission 2–8%" not in r2["assumptions"])
check("unknown ASIN handled", "error" in estimate.analyze("NOPE", data))
check("report renders", "P10 / P50 / P90" in estimate.report(r))

# v1.19: Telegram 429 retry honours retry_after, then succeeds
import requests as _rq
calls = []
class _R:
    def __init__(s, c): s.status_code = c; s.text = ""
    def json(s): return {"parameters": {"retry_after": 0}}
seq = iter([429, 200])
_orig_post, _orig_sleep = _rq.post, tf.time.sleep
_rq.post = lambda *a, **k: (calls.append(1), _R(next(seq)))[1]
tf.time.sleep = lambda s: None
r = tf._tg_post("u", data={})
_rq.post, tf.time.sleep = _orig_post, _orig_sleep
check("Telegram 429 → waits and retries instead of losing the message", r.status_code == 200 and len(calls) == 2)

# v1.19: keyword in BR_CATEGORIES is rejected
import subprocess
out = subprocess.run([sys.executable, "-c", "import trend_fetcher as t; print(sorted(t.CATEGORIES), t.BAD_CATEGORIES)"],
                     env={**os.environ, "BR_CATEGORIES": "kitchen,solar light,wall decor"}, capture_output=True, text=True).stdout
check("search words in BR_CATEGORIES ignored + reported", "['kitchen'] ['solar light', 'wall decor']" in out, out)

kw = tf.product_keywords(pd.DataFrame({"title": ["Pedigree Adult Wet Dog Food Chicken", "Drools Dog Food Adult", "Purepet Dog Food",
                                                 "Royal Dog Food Puppy", "Tekcool Umbrella Big"],
                                       "trend_score": [9, 8, 7, 6, 5], "actionable": ["True"] * 5, "seasonal": [""] * 5}), n=3)
check("state keywords are generic ('dog food'), brands dropped, no reversed duplicates", kw == ["dog food"], kw)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
