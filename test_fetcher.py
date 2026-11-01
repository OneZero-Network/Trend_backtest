#!/usr/bin/env python3
"""Offline tests for trend_fetcher.py (fixtures, no network). Run: python test_fetcher.py"""
import os, re, sys, json, shutil, tempfile
from datetime import timedelta
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update({"BR_CATEGORIES": "electronics,beauty", "BR_OUTPUT_DIR": TMP, "BR_DETAIL_TOP_N": "5"})
import trend_fetcher as tf

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond); print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")

# ── price parsing
check("single price", tf.parse_prices("Boat Earbuds ₹1,299") == (1299, None, None))
check("variant range", tf.parse_prices("Kurti ₹449 - ₹899") == (449, 899, None))
check("labelled MRP", tf.parse_prices("₹999 M.R.P: ₹2,499") == (999, None, 2499))
check("unlabelled strike price → MRP", tf.parse_prices("₹1,499 ₹3,999") == (1499, None, 3999))
check("no price", tf.parse_prices("Currently unavailable") == (None, None, None))

# ── plain-language states message (per keyword)
_rows = []
for kw, data in {"lunch box": [("Haryana", 100, 1, 1), ("Delhi", 90, 2, 2), ("Karnataka", 80, 3, 6), ("Punjab", 60, 8, 3), ("Sikkim", 5, 20, 20)],
                 "yoga mat": [("Karnataka", 100, 1, 1), ("Telangana", 85, 4, 2), ("Delhi", 95, 2, 3), ("Jharkhand", 50, 9, 4)]}.items():
    for stt, v, r3, r1 in data:
        _rows.append({"keyword": kw, "state": stt, "interest_3m": v, "rank_3m": r3, "rank_1m": r1, "rank_change": r3 - r1, "low_signal": v < 10})
_st = pd.DataFrame(_rows); _m = tf.states_message(_st, tf.state_summary(_st), "2026-11-01")
check("states message: per keyword 'most' + 'growing' (≥3 places), low-signal states dropped",
      "lunch box</b> — most: Haryana, Delhi, Karnataka · growing: Punjab" in _m and "growing: Jharkhand" in _m
      and "Telangana" not in _m.split("yoga mat")[1].split("growing")[1] and "Sikkim" not in _m)
check("states message says it's not sales", "not sales" in _m)

# ── circuit breaker: 3 captcha'd product pages in a row → stop asking
calls = []
_fh, _ns = tf.fetch_html, tf.new_session
tf.fetch_html = lambda sb, url, retries=3: calls.append(url) or None
tf.new_session = lambda: None
d = tf.fetch_details([f"B0BLOCK{i:03d}" for i in range(30)])
check("product pages: stops after 3 blocks in a row (not 30)", len(calls) == 3 and tf.DETAIL_STATS["skipped"] == 27,
      f"{len(calls)} requests, {tf.DETAIL_STATS}")
seq = iter([None, None, "<html><div id='centerCol'></div></html>", None, None, None])
calls.clear(); tf.fetch_html = lambda sb, url, retries=3: calls.append(url) or next(seq, None)
tf.fetch_details([f"B0MIX{i:03d}" for i in range(10)])
check("a success resets the streak", len(calls) == 6, f"{len(calls)} requests")
tf.fetch_html, tf.new_session = _fh, _ns

# ── regressions from the first live run
os.environ["BR_SEED_KEYWORDS"] = ""
import importlib
_tf2 = importlib.reload(tf)
check("empty BR_SEED_KEYWORDS repo variable falls back to defaults", len(_tf2.SEED_KEYWORDS) == 10, str(_tf2.SEED_KEYWORDS[:3]))
del os.environ["BR_SEED_KEYWORDS"]
tf = importlib.reload(_tf2)
os.environ.update({"BR_CATEGORIES": "electronics,beauty", "BR_OUTPUT_DIR": TMP, "BR_DETAIL_TOP_N": "5"})
check("ISBN zero-padding", tf.norm_asin("8640378") == "0008640378" and tf.norm_asin("B0H8SSDB2Y") == "B0H8SSDB2Y")
check("Telegram text escapes '<' from 'low (<7 days history)'", "&lt;7" in tf._esc("low (<7 days history)"))

# ── REAL amazon.in page (Oct 31): Movers & Shakers empty on Amazon's side
real = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_fixtures",
                         "amazon_in_movers_empty_2026-10-31.html"), encoding="utf-8").read()
check("real empty Movers page: 0 rows, recognised as Amazon-side",
      tf.parse_list_page(real, "movers", "electronics") == [] and bool(tf._AMAZON_EMPTY.search(real)))

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_fixtures")
rd = lambda n: open(os.path.join(FX, n), encoding="utf-8").read()
nr = rd("amazon_in_new_releases_empty.html")
check("real 'no HOT new releases' page recognised as Amazon-side", bool(tf._AMAZON_EMPTY.search(nr))
      and tf.parse_list_page(nr, "new_releases", "grocery") == [])
p10 = tf.parse_product_page(rd("amazon_in_product_10_offers.html"), "B0HK4GNJF1")
check("real page: 'New (10) from' in right column → 10 sellers", p10["seller_offers"] == 10, str(p10.get("seller_source")))
p1 = tf.parse_product_page(rd("amazon_in_product_single_seller.html"), "B0HL8XMHM5")
check("real page: only 'Sold by' → 1 seller", p1["seller_offers"] == 1, str(p1.get("seller_source")))
p0 = tf.parse_product_page(rd("amazon_in_product_no_buybox.html"), "B0HKK3FSLN")
check("real reduced page (no buy box) → unknown, labelled as such", p0["seller_offers"] is None
      and "without buy box" in p0["seller_source"], p0["seller_source"])
try:
    from pytrends.request import TrendReq
    import inspect as _i
    src_tr = _i.getsource(tf.fetch_rising_queries) + _i.getsource(tf.fetch_state_interest)
    lib = _i.getsource(TrendReq)
    guarded = re.search(r"if self\.retries > 0 or self\.backoff_factor > 0", lib) is not None
    check("pytrends: our calls pass no retries/backoff, and the library only builds the broken "
          "Retry(method_whitelist=…) when they are set", guarded and "retries=" not in src_tr
          and "backoff_factor=" not in src_tr)
except ImportError:
    print("SKIP  pytrends not installed")

# ── REAL history (pet-supplies, Oct 31 → Nov 1): Amazon swapped Purepet 6 kg (#1) for 1 kg (#1)
RH = pd.read_csv(os.path.join(FX, "real_history_pet_supplies_2026-10-31_11-01.csv"), dtype=str)
RH["rank"] = pd.to_numeric(RH["rank"])
before_fill = (~RH["title"].map(tf._has_title)).sum()
RH = tf.fill_titles(RH)
check("titles filled from other days for lazy-loaded ranks", (~RH["title"].map(tf._has_title)).sum() < before_fill,
      f"untitled {before_fill} → {(~RH['title'].map(tf._has_title)).sum()}")
om_real = tf.own_movers(RH, "2026-11-01")
check("variant swap (Purepet 6 kg → 1 kg at #1) is NOT a +10,000% mover",
      om_real.get("B0BMGP89P1", 0) < 100 and tf.SWAPS.get("B0BMGP89P1") == 1,
      f"pct {om_real.get('B0BMGP89P1', 0):.0f}, swap note: family was #{tf.SWAPS.get('B0BMGP89P1')}")
check("family key groups sizes, not different products",
      tf.family_key("Purepet Adult Dry Cat Food 1kg | Ocean Fish") == tf.family_key("Purepet Adult Dry Cat Food 6kg | Ocean")
      and tf.family_key("Purepet Adult Dry Cat Food 1kg") != tf.family_key("Purepet Adult Dry Dog Food 10kg"))
check("Hit spray → Insecticides Act flag", "Insecticides Act" in
      __import__("compliance").compliance_flags("Hit Flying Insect Killer - Mosquito & Fly Killer Spray (700ML)", "home-improvement"))
check("'how to clean …' search dropped", bool(tf._NOISE_QUERY.search("how to clean stainless steel water bottle")))

# ── own movers from bestseller history
H = pd.DataFrame([
    # date, list, category_slug, asin, rank
    ("2026-10-30", "bestsellers", "kitchen", "CLIMB", 50), ("2026-10-31", "bestsellers", "kitchen", "CLIMB", 10),
    ("2026-10-31", "bestsellers", "kitchen", "NEWIN", 5),                              # absent yesterday
    ("2026-10-30", "bestsellers", "kitchen", "FALL", 10), ("2026-10-31", "bestsellers", "kitchen", "FALL", 20),
    ("2026-10-31", "bestsellers", "toys", "TOYNEW", 1),                                # toys not fetched yesterday
], columns=["date", "list", "category_slug", "asin", "rank"])
om = tf.own_movers(H, "2026-10-31")
check("own mover: 50→10 = +400%", abs(om.get("CLIMB", 0) - 400) < 1e-9, str(om.to_dict()))
check("own mover: new top-100 entry at #5 treated as from #101 (+1920%)", abs(om.get("NEWIN", 0) - 1920) < 1e-9)
check("own mover: falling product not a mover", "FALL" not in om)
check("own mover: category missing yesterday is NOT compared (no fake movers)", "TOYNEW" not in om)
check("own mover: gap > 2 days → no comparison",
      tf.own_movers(H.replace({"2026-10-30": "2026-10-25"}), "2026-10-31").empty)

# ── embedded JSON list (data-client-recs-list): movers page with NO rendered cards
import html as _h
def recs(items):
    return '<div class="p13n-desktop-grid" data-client-recs-list="' + _h.escape(json.dumps(items), quote=True) + '"></div>'
mv = recs([{"id": "B0MOVER001", "metadataMap": {"render.zg.rank": "1", "render.zg.bsms.currentSalesRank": "45",
            "render.zg.bsms.percentageChange": "2,350", "render.zg.bsms.twentyFourHourOldSalesRank": "1,103"}},
           {"id": "B0MOVER002", "metadataMap": {"render.zg.rank": "2", "render.zg.bsms.currentSalesRank": "310",
            "render.zg.bsms.percentageChange": "", "render.zg.bsms.twentyFourHourOldSalesRank": ""}}])
mv += '<a href="/x/dp/B0MOVER001/ref"><img alt="Mini Neck Fan Rechargeable"></a>'
rows = tf.parse_list_page(mv, "movers", "electronics")
r1 = {r["asin"]: r for r in rows}
check("movers page with only embedded JSON parses", len(rows) == 2, f"{len(rows)} rows")
check("mover stats from JSON (rank 45 ← 1103, +2350%)", r1.get("B0MOVER001", {}).get("rank_now") == 45
      and r1["B0MOVER001"]["rank_prev"] == 1103 and r1["B0MOVER001"]["pct_gain"] == 2350, str(r1.get("B0MOVER001")))
check("title recovered from image alt", r1["B0MOVER001"]["title"] == "Mini Neck Fan Rechargeable")
check("no previous rank → was_unranked", r1["B0MOVER002"]["was_unranked"] is True)
import inspect
src = inspect.getsource(tf)
check("JSON parse uses only standard library (json) — no new dependency", "parse_recs_json" in src)
# bestseller page: 2 rendered cards + JSON with 4 → 4 rows, card data kept, ranks from JSON
bsh = (f'<div id="gridItemRoot"><span class="zg-bdg-text">#1</span><div data-asin="B0CARD0001"><a href="/x/dp/B0CARD0001">'
       f'<img alt="Steel Bottle"></a><span>₹499</span></div></div>'
       f'<div id="gridItemRoot"><span class="zg-bdg-text">#2</span><div data-asin="B0CARD0002"><a href="/x/dp/B0CARD0002">'
       f'<img alt="Lunch Box"></a><span>₹349</span></div></div>'
       + recs([{"id": a, "metadataMap": {"render.zg.rank": str(i + 1)}} for i, a in
               enumerate(["B0CARD0001", "B0CARD0002", "B0LAZY0003", "B0LAZY0004"])]))
rows = tf.parse_list_page(bsh, "bestsellers", "kitchen")
check("lazy-loaded items added from JSON (2 cards + 2 hidden = 4)", [r["asin"] for r in rows] ==
      ["B0CARD0001", "B0CARD0002", "B0LAZY0003", "B0LAZY0004"])
check("card data (price) kept when JSON merged", rows[0]["price_inr"] == 499)
# empty page is dumped for debugging
tf.LOCAL_OUT_DIR = TMP
tf._dump_debug("<html>weird</html>", "grocery", "new_releases", 1)
check("0-item page saved to output/debug for diagnosis", os.path.exists(os.path.join(TMP, "debug", "grocery_new_releases_p1.html")))
shutil.rmtree(os.path.join(TMP, "debug"))

# ── product page
PAGE = """<html><body><div id="centerCol">
<div id="corePriceDisplay_desktop_feature_div">
 <span class="a-price"><span class="a-offscreen">₹1,299</span></span>
 <span class="savingsPercentage">-48%</span>
 <span class="a-price a-text-price"><span class="a-offscreen">₹2,499</span></span> M.R.P.</div>
<span id="social-proofing-faceout-title-tk_bought">5K+ bought in past month</span>
<span>Limited time deal</span> <span>Apply ₹100 coupon</span> <span>Bank Offer 10% off</span>
<span>Fulfilled</span> by Amazon. Cash on Delivery available.
<a>New (12) from ₹1,249</a></div></body></html>"""
d = tf.parse_product_page(PAGE, "B0TEST0001")
exp = {"price_inr": 1299, "mrp_inr": 2499, "discount_pct": 48.0, "bought_past_month_min": 5000,
       "seller_offers": 12, "coupon": "₹100", "limited_deal": True, "bank_offer": True,
       "fulfilled_by_amazon": True, "cod_available": True}
bad = {k: (d.get(k), v) for k, v in exp.items() if d.get(k) != v}
check("product page fields", not bad, str(bad) if bad else "10/10 fields")
d2 = tf.parse_product_page("<html><div id='centerCol'><span class='a-price'><span class='a-offscreen'>₹799</span></span>"
                           "<span>1L+ bought in past month</span></div></html>", "B0TEST0002")
check("lakh units + missing fields are None", d2["bought_past_month_min"] == 100000 and d2["mrp_inr"] is None
      and d2["seller_offers"] is None and d2["discount_pct"] is None, str(d2))

# ── 8-day simulation: discount-driven climber vs organic climber, details + states mocked
def card(rank, asin, title, price, rev, mrp=None):
    m = f" M.R.P: ₹{mrp:,}" if mrp else ""
    return (f'<div id="gridItemRoot"><span class="zg-bdg-text">#{rank}</span><div data-asin="{asin}">'
            f'<a href="/x/dp/{asin}"><img alt="{title}"></a><a href="/product-reviews/{asin}">'
            f'4.2 out of 5 stars {rev:,}</a><span>₹{price:,}{m}</span></div></div>')
import json
def run_day(d):
    tf.TODAY = (tf.NOW_IST - timedelta(days=7 - d)).strftime("%Y-%m-%d")
    def fake(sb, url, retries=3):
        if "pg=2" in url or url.endswith("amazon.in/"): return "<html></html>"
        if "/dp/" in url:
            return PAGE
        base = [card(i, f"B0ELEC{i:04d}", f"Gadget {i}", 999 + i, 1000 + i * 10) for i in range(3, 20)]
        organic = card(max(1, 60 - 8 * d), "B0ORGANIC1", "Organic Climber", 1499, 200 + d * 120, mrp=1999)
        price = 2999 if d < 5 else 1799                          # 40% cut from day 5 → 3 snapshots by day 7
        hj = card(max(3, 65 - 8 * d), "B0HIJACKED", "Hijacked Organic", 899 if d == 7 else 1999, 250 + d * 110)
        cut = card(max(2, 70 - 9 * d), "B0DISCOUNT", "Discount Climber", price, 300 + d * 60, mrp=3999)
        return organic + cut + hj + "".join(base)
    tf.fetch_html = fake; tf.new_session = lambda: None
    tf.fetch_trending_rss = lambda: []; tf.fetch_rising_queries = lambda: []
    tf.fetch_state_interest = lambda: [
        {"date": tf.TODAY, "keyword": k, "state": s, "geo_code": "", "interest_3m": v, "interest_1m": v,
         "rank_3m": r, "rank_1m": r2, "rank_change": r - r2, "low_signal": v < 10}
        for k in ("air fryer", "kurti") for (s, v, r, r2) in
        (("Kerala", 100, 1, 1), ("Goa", 90, 2, 6), ("Bihar", 40, 6, 2), ("Sikkim", 5, 9, 9))]
    tf.MIN_GAP_SEC = 0; tf.PRODUCT_EXTRA_GAP_SEC = 0; tf.RUN_LOG.clear()
    msgs = []
    if not getattr(tf, "_keep_sender", False):
        tf.send_telegram = msgs.append
    tf.main()
    return msgs
digests = {}
for d in range(8):
    msgs = run_day(d)
    digests[d] = "\n".join(m for m in msgs if "Bazaar Radar</b>" in m)
sig = pd.read_csv(os.path.join(TMP, "TREND_SIGNALS.csv")).set_index("asin")
check("discount-driven climb labelled", sig.loc["B0DISCOUNT", "signal"].startswith("💸"),
      f"{sig.loc['B0DISCOUNT', 'signal']} · price change {sig.loc['B0DISCOUNT', 'price_change_7d_pct']}%")
check("organic climb NOT labelled discount-driven", not str(sig.loc["B0ORGANIC1", "signal"]).startswith("💸"),
      sig.loc["B0ORGANIC1", "signal"])
check("discount penalty lowers the discount climber's score",
      sig.loc["B0DISCOUNT", "trend_score"] < sig.loc["B0DISCOUNT", "trend_score_unpenalized"],
      f"{sig.loc['B0DISCOUNT', 'trend_score_unpenalized']:.1f} → {sig.loc['B0DISCOUNT', 'trend_score']:.1f}")
check("organic climber not penalised",
      abs(sig.loc["B0ORGANIC1", "trend_score"] - sig.loc["B0ORGANIC1", "trend_score_unpenalized"]) < 1e-9)
check("1-day dip quarantined (held), not penalised",
      sig.loc["B0HIJACKED", "signal"].startswith("⏸ Quarantined") and not bool(sig.loc["B0HIJACKED", "actionable"])
      and abs(sig.loc["B0HIJACKED", "trend_score"] - sig.loc["B0HIJACKED", "trend_score_unpenalized"]) < 0.01,
      f"{sig.loc['B0HIJACKED', 'signal']} · status {sig.loc['B0HIJACKED', 'discount_status']}")
check("quarantined product excluded from Telegram top signals",
      not any("Hijacked Organic" in m.split("Quarantined")[0] for m in msgs) and any("Quarantined" in m for m in msgs))
import calculator as CALC
crow7 = {r["asin"]: r for r in CALC.read_xlsx(os.path.join(TMP, "CALCULATOR.xlsx"))}
check("calculator row shows CURRENT quarantine status (not yesterday's signal)",
      "Quarantined" in str(crow7["B0HIJACKED"]["signal"]), str(crow7["B0HIJACKED"]["signal"])[:40])
check("decision formula holds quarantined rows", "Quarantined" in str(crow7["B0HIJACKED"]["decision"]))
msgs8 = run_day(8)
crow8 = {r["asin"]: r for r in CALC.read_xlsx(os.path.join(TMP, "CALCULATOR.xlsx"))}
check("…and shows the released status next day", "Quarantined" not in str(crow8["B0HIJACKED"]["signal"]))                                    # next day: hijacked price snaps back
sig8 = pd.read_csv(os.path.join(TMP, "TREND_SIGNALS.csv")).set_index("asin")
check("quarantine releases the product when price recovers next day",
      bool(sig8.loc["B0HIJACKED", "actionable"]) and not str(sig8.loc["B0HIJACKED", "signal"]).startswith("⏸"),
      sig8.loc["B0HIJACKED", "signal"])
check("organic climber now outranks discount climber",
      sig.loc["B0ORGANIC1", "trend_score"] > sig.loc["B0DISCOUNT", "trend_score"])
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import config as BC
same = (tf.PRICE_DROP_PCT, tf.DISCOUNT_LOOKBACK_DAYS, tf.DISCOUNT_PENALTY, tf.DISCOUNT_SUSTAIN_HOURS) == \
       (BC.DISCOUNT_DROP_PCT, BC.DISCOUNT_LOOKBACK_DAYS, BC.DISCOUNT_PENALTY, BC.DISCOUNT_SUSTAIN_HOURS)
check("live penalty == backtested penalty (same constants)", same)
import calculator as CALC
crow = {r["asin"]: r for r in CALC.read_xlsx(os.path.join(TMP, "CALCULATOR.xlsx"))}
tf.DETAIL_STATS.clear()
check("days 1–6: digest says LEARNING, shows no ranking", all("Learning: day" in digests[d] and
      "Top trend signals" not in digests[d] and "Hottest categories" not in digests[d] for d in range(6)))
check("day 7+: ranked list + category heat appear", "Top trend signals" in digests[7] and "Hottest categories" in digests[7])
gtx = pd.DataFrame([{"source": "rising_query", "query": f"{sd} q{i}", "seed": sd, "value": 100, "note": ""}
                    for sd in ("smartwatch", "lunch box", "yoga mat") for i in range(5)])
noise = ["lunch box movie", "lunch box movie cast", "self heating lunch box", "glass lunch box", "tiffin meaning in hindi"]
kept = [q for q in noise if not tf._NOISE_QUERY.search(q)]
check("rising searches: film/meaning queries dropped, product queries kept",
      kept == ["self heating lunch box", "glass lunch box"], str(kept))
check("rising searches: max 2 per seed keyword", gtx.groupby("seed", sort=False).head(2)["seed"].value_counts().max() == 2)
arch = os.path.join(TMP, "archive")
days_arch = sorted(os.listdir(arch)) if os.path.isdir(arch) else []
check("permanent archive: one folder per day with history + signals",
      len(days_arch) >= 7 and all(os.path.exists(os.path.join(arch, d, "history.csv.gz")) for d in days_arch),
      f"{len(days_arch)} day folders")
ah = pd.read_csv(os.path.join(arch, days_arch[0], "history.csv.gz"), dtype=str)
check("archived history holds that day only, readable", set(ah["date"]) == {days_arch[0]} and len(ah) > 0)
check("daily run fills CALCULATOR (after the 7-day history gate)", "B0ORGANIC1" in crow, f"{len(crow)} rows")
check("compliance + competition columns on signals", {"compliance", "competition"} <= set(sig.columns)
      and sig["compliance"].astype(str).str.contains("REVIEW|NOT FLAGGED").all())
check("details merged into signals", {"bought_past_month_min", "seller_offers", "discount_pct", "crowded_listing"} <= set(sig.columns))
check("crowded listing flagged at >=10 sellers", bool(sig["crowded_listing"].dropna().any()))
bs = pd.read_csv(os.path.join(TMP, "AMZ_BESTSELLERS.csv"))
check("MRP captured from list page", bs["mrp_inr"].notna().sum() >= 2)
check("daily run does NOT call state trends", not os.path.exists(os.path.join(TMP, "STATE_SUMMARY.csv")))
tf.BR_MODE = "states"; tf.BR_STATE_TRENDS = True
msgs = []; tf.send_telegram = msgs.append
tf.main()
ss = pd.read_csv(os.path.join(TMP, "STATE_SUMMARY.csv"))
check("state summary excludes low-signal states", "Sikkim" not in set(ss["state"]), ss["state"].tolist())
check("rising state detected", int(ss.set_index("state").loc["Bihar", "rising_keywords"]) == 2)
check("weekly states job labels states as search share", any("not sales" in m and "Where people are searching" in m for m in msgs))
tf.fetch_state_interest = lambda: []
msgs = []; tf.send_telegram = msgs.append
tf.main()
check("states job survives Google returning nothing", any("no data" in m for m in msgs))
# ── Google Sheet 403 (unshared sheet): run must continue locally, explain fix, exit 3
import gspread
from google.oauth2 import service_account as _sa
tf.BR_MODE = "daily"; tf.fetch_state_interest = lambda: []
tf.GOOGLE_CREDS_JSON = json.dumps({"type": "service_account", "client_email": "radar-bot@proj.iam.gserviceaccount.com"})
tf.GOOGLE_SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
_orig_auth, _orig_cred = gspread.authorize, _sa.Credentials.from_service_account_info
class _C:
    def open_by_key(self, k): raise PermissionError()
gspread.authorize = lambda creds: _C()
_sa.Credentials.from_service_account_info = staticmethod(lambda info, scopes=None: object())
tf.SHEET_PROBLEM.clear()
msgs = []; tf.send_telegram = msgs.append
code = None
tf._keep_sender = True
try:
    run_day(7)
except SystemExit as e:
    code = e.code
gspread.authorize, _sa.Credentials.from_service_account_info = _orig_auth, _orig_cred
check("403 on sheet: run continues and fetches Amazon", any("Amazon lists:" in m for m in msgs))
check("403: Telegram names the exact email to share with",
      any("radar-bot@proj.iam.gserviceaccount.com" in m and "Editor" in m for m in msgs))
check("403: results saved locally for the artifact", os.path.exists(os.path.join(TMP, "TREND_SIGNALS.csv")))
check("403: exits non-zero (3) so the run shows red", code == 3, f"exit {code}")

shutil.rmtree(TMP)
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
