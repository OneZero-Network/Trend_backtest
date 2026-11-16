"""v1.50: capture gap, resell gate, compliance false positives, fair forward test, version, movers skip, state keywords."""
import os, re, sys, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf
import capture, resell_gate, compliance, picks, season, stock_gate

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info)[:300] if not ok else ""))
    if not ok: FAILS.append(name)

NAN = float("nan")
def row(date, asin, rank, price=NAN, rating=NAN, reviews=NAN, lst="bestsellers", cat="kitchen", title="x"):
    return {"date": date, "list": lst, "category_slug": cat, "asin": asin, "rank": rank, "price_inr": price,
            "rating": rating, "reviews": reviews, "title": title}

# ══ 1. capture: three different meanings of "no price" ═══════════════════════════
H = pd.DataFrame([
    row("2026-11-10", "CARRIED001", 60, 299, 4.1, 100),                  # priced 6 days ago? no: 2 days ago (below)
    row("2026-11-14", "CARRIED001", 62, 299, 4.1, 100),                  # priced 2 days ago at a rendered rank
    row("2026-11-16", "CARRIED001", 45),                                 # today: placeholder (rank 31-50)
    row("2026-11-16", "UNKNOWN001", 38),                                 # today: placeholder, never priced
    row("2026-11-16", "SOLDOUT001", 12, NAN, 4.3, 2500),                 # today: card rendered, no price → truly unavailable
    row("2026-11-16", "INSTOCK001", 7, 499, 4.4, 900),                   # today: priced
    row("2026-11-16", "TWOLIST001", 40),                                 # placeholder on bestsellers…
    row("2026-11-16", "TWOLIST001", 4, 150, 4.0, 50, lst="new_releases"),  # …but a priced card on new_releases
    row("2026-11-05", "OLDPRICE01", 70, 99, 4.0, 10),                    # priced 11 days ago — outside the carry window
    row("2026-11-16", "OLDPRICE01", 85),
])
st = capture.stock_states(H, "2026-11-16")
g = st["stock_state"].to_dict()
check("priced card → in_stock", g["INSTOCK001"] == "in_stock" and st.loc["INSTOCK001", "price_best"] == 499)
check("card rendered, no price → unavailable (sold out)", g["SOLDOUT001"] == "unavailable")
check("placeholder + priced 2 days ago → carried, with that price", g["CARRIED001"] == "carried" and st.loc["CARRIED001", "price_best"] == 299, g)
check("placeholder, never priced → unknown (NOT 'out of stock')", g["UNKNOWN001"] == "unknown")
check("priced on ANY list today counts", g["TWOLIST001"] == "in_stock" and st.loc["TWOLIST001", "price_best"] == 150)
check("a price older than the carry window is not trusted", g["OLDPRICE01"] == "unknown")
check("summary counts", capture.summarize(st["stock_state"]) == {"in_stock": 2, "carried": 1, "unknown": 2, "unavailable": 1},
      capture.summarize(st["stock_state"]))
check("placeholder detection", capture.is_placeholder({"price_inr": None, "rating": None, "reviews": None})
      and not capture.is_placeholder({"price_inr": None, "rating": 4.2, "reviews": None}))

# ── merge of the lazily loaded half
rows = [{"asin": "A" * 10, "rank": 31, "title": "", "price_inr": None, "price_max_inr": None, "mrp_inr": None, "rating": None, "reviews": None},
        {"asin": "B" * 10, "rank": 32, "title": "kept", "price_inr": 100.0, "price_max_inr": None, "mrp_inr": None, "rating": 4.0, "reviews": 5},
        {"asin": "C" * 10, "rank": 33, "title": "", "price_inr": None, "price_max_inr": None, "mrp_inr": None, "rating": None, "reviews": None}]
extra = [{"asin": "A" * 10, "rank": 31, "title": "Lazy Title", "price_inr": 249.0, "price_max_inr": None, "mrp_inr": 399.0, "rating": 4.2, "reviews": 77},
         {"asin": "B" * 10, "rank": 32, "title": "other", "price_inr": 1.0, "price_max_inr": None, "mrp_inr": None, "rating": 1.0, "reviews": 1},
         {"asin": "C" * 10, "rank": 33, "title": "", "price_inr": None, "price_max_inr": None, "mrp_inr": None, "rating": None, "reviews": None}]
n = capture.merge_lazy(rows, extra)
check("lazy merge fills only placeholders, only with real data", n == 1 and rows[0]["price_inr"] == 249.0 and rows[0]["reviews"] == 77
      and rows[0]["title"] == "Lazy Title" and rows[1]["price_inr"] == 100.0 and rows[2]["price_inr"] is None, rows)

# ── the below-the-fold probe: works → used on later pages; fails → one try, then stops
PAGES, CALLS = {}, []
def fake_fetch(sess_box, url, retries=3):
    CALLS.append(url); return PAGES.get("html")
def fake_parse_ok(html, lt, slug):
    return [{"asin": f"{i:010d}", "rank": 30 + i, "title": "t", "price_inr": 100.0 + i, "price_max_inr": None, "mrp_inr": None,
             "rating": 4.0, "reviews": 10} for i in range(1, 21)]
def mk_ph():
    return [{"asin": f"{i:010d}", "rank": 30 + i, "title": "", "price_inr": None, "price_max_inr": None, "mrp_inr": None,
             "rating": None, "reviews": None} for i in range(1, 21)]
real_fetch, real_parse = tf.fetch_html, tf.parse_list_page
tf.fetch_html = fake_fetch
try:
    tf.BR_LAZY_FETCH = "auto"; tf.LAZY.update(state="untested", form=None, placeholders=0, filled=0, requests=0)
    PAGES["html"] = "<html/>"; tf.parse_list_page = fake_parse_ok
    r1 = mk_ph(); f1 = tf.lazy_fill([None], "https://amazon.in/gp/bestsellers/kitchen/", r1, "bestsellers", "kitchen")
    check("probe OK: placeholders priced, state=working, form remembered", f1 == 20 and tf.LAZY["state"] == "working"
          and tf.LAZY["form"] == 0 and all(r["price_inr"] for r in r1), tf.LAZY)
    CALLS.clear(); r2 = mk_ph(); tf.lazy_fill([None], "https://amazon.in/gp/bestsellers/toys/?pg=2", r2, "bestsellers", "toys")
    check("working probe → ONE extra request per page, with the remembered form", len(CALLS) == 1 and "isAboveTheFold=0" in CALLS[0], CALLS)
    CALLS.clear(); tf.lazy_fill([None], "https://amazon.in/gp/new-releases/toys/", mk_ph(), "new_releases", "toys")
    check("only bestseller pages are probed", CALLS == [])
    # failure path
    tf.LAZY.update(state="untested", form=None); CALLS.clear(); PAGES["html"] = "<html/>"; tf.parse_list_page = lambda *a: []
    r3 = mk_ph(); f3 = tf.lazy_fill([None], "https://amazon.in/gp/bestsellers/kitchen/", r3, "bestsellers", "kitchen")
    check("probe FAILS → both forms tried once, state=failed, rows untouched", f3 == 0 and tf.LAZY["state"] == "failed"
          and len(CALLS) == 2 and all(r["price_inr"] is None for r in r3), (tf.LAZY, CALLS))
    CALLS.clear(); tf.lazy_fill([None], "https://amazon.in/gp/bestsellers/toys/", mk_ph(), "bestsellers", "toys")
    check("after a failed probe no more requests are spent today", CALLS == [])
    # blocked page (None) must not crash or mark success
    tf.LAZY.update(state="untested", form=None); PAGES["html"] = None; CALLS.clear()
    f4 = tf.lazy_fill([None], "https://amazon.in/gp/bestsellers/kitchen/", mk_ph(), "bestsellers", "kitchen")
    check("blocked probe page → 0 filled, no crash", f4 == 0)
    tf.BR_LAZY_FETCH = "0"; tf.LAZY.update(state="off"); CALLS.clear()
    tf.lazy_fill([None], "https://amazon.in/gp/bestsellers/kitchen/", mk_ph(), "bestsellers", "kitchen")
    check("BR_LAZY_FETCH=0 never requests", CALLS == [])
finally:
    tf.fetch_html, tf.parse_list_page = real_fetch, real_parse
    tf.BR_LAZY_FETCH = "auto"; tf.LAZY.update(state="untested", form=None)

# ══ 2. compute_signals carries stock_state; select_picks uses it ═════════════════
D0 = datetime(2026, 11, 1)
days = [(D0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(16)]
rows_h = []
for i, d in enumerate(days):
    for k in range(1, 61):
        a = f"K{k:09d}"
        rank = k if i < 13 else max(1, k - 12)                # everything climbs in the last 3 days
        tail = 31 <= rank <= 50                                # rendered ranks carry a card, the tail does not
        rows_h.append(row(d, a, rank, NAN if tail else 100.0 + k, NAN if tail else 4.0, NAN if tail else 50, title=f"Gizmo {k} Holder"))
H2 = pd.DataFrame(rows_h)
tf.TODAY = days[-1]; tf.NOW_IST = datetime.strptime(days[-1], "%Y-%m-%d").replace(hour=12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
sig = tf.compute_signals(H2, {})
check("signals carry stock_state and a best-known price", "stock_state" in sig and set(sig["stock_state"]) <= {"in_stock", "carried", "unknown", "unavailable"})
tail_today = H2[(H2.date == days[-1]) & H2["rank"].between(31, 50)]["asin"]
tail_sig = sig[sig["asin"].isin(tail_today)]
kmap = {a: int(a[1:]) for a in tail_today}
was_rendered = tail_sig[tail_sig["asin"].map(kmap) >= 51]          # sat at a rendered rank (51-60) until 3 days ago
never_seen = tail_sig[tail_sig["asin"].map(kmap) <= 50]            # tail rank on every day → we never saw a price
check("tail-rank items priced earlier → 'carried' with a price (v1.49 dropped ~19% of them as 'out of stock')",
      len(was_rendered) > 0 and (was_rendered["stock_state"] == "carried").all() and was_rendered["price_inr"].notna().all(),
      tail_sig[["asin", "stock_state", "price_inr"]])
check("tail-rank items never seen with a price stay 'unknown' (honest, not 'sold out')",
      len(never_seen) > 0 and (never_seen["stock_state"] == "unknown").all() and never_seen["price_inr"].isna().all())

s = pd.DataFrame([
    {"asin": "OK00000001", "title": "In stock climber", "category_slug": "kitchen", "trend_score": 90, "rank_change": 20, "stock_state": "in_stock", "price_inr": 100, "actionable": "True", "days_on_list_14d": 10, "bs_rank_today": 5},
    {"asin": "CAR0000001", "title": "Carried climber", "category_slug": "toys", "trend_score": 89, "rank_change": 20, "stock_state": "carried", "price_inr": 100, "actionable": "True", "days_on_list_14d": 10, "bs_rank_today": 5},
    {"asin": "UNK0000001", "title": "Unverified climber", "category_slug": "sports", "trend_score": 88, "rank_change": 20, "stock_state": "unknown", "price_inr": None, "actionable": "True", "days_on_list_14d": 10, "bs_rank_today": 5},
    {"asin": "GON0000001", "title": "Sold out climber", "category_slug": "baby", "trend_score": 87, "rank_change": 20, "stock_state": "unavailable", "price_inr": None, "actionable": "True", "days_on_list_14d": 10, "bs_rank_today": 5},
])
rej = []
ch = picks.select_picks(s, n=8, today="2026-11-16", rejected=rej)
why = {t: w for t, w in rej}
check("in-stock and carried items are picked; unverified and sold-out are not", set(ch["asin"]) == {"OK00000001", "CAR0000001"}, list(ch["asin"]))
check("sold out says SOLD OUT; unknown says UNVERIFIED — never 'out of stock' for a placeholder",
      "sold out" in why["Sold out climber"] and "unverified" in why["Unverified climber"] and "out of stock" not in why["Unverified climber"], why)
old = s.drop(columns=["stock_state"]).copy(); old.loc[old.asin == "UNK0000001", "price_inr"] = None
rej2 = []
ch_old = picks.select_picks(old, n=8, today="2026-11-16", rejected=rej2)
check("older signal tables (no stock_state) keep the price rule", "UNK0000001" not in set(ch_old["asin"]) and any("no price" in w for _, w in rej2))

# ══ 3. resell gate ═════════════════════════════════════════════════════════════
def ok(t, c=""): return resell_gate.check(t, c)["ok"]
def kind(t, c=""): return resell_gate.check(t, c)["kind"]
check("big brands are affiliate-only", kind("NIVEA MEN Deep Impact Roll On", "toys") != "" and kind("Safari Pentagon Pro 8 Wheels Trolley Bag", "luggage") == "brand"
      and kind("HRX Zanzibar Cabin Size Suitcase", "luggage") == "brand" and kind("American Tourister Ivy Backpack", "luggage") == "brand")
check("small private-label brands are NOT blocked (their product can be sourced generically)",
      ok("Gesto Gym Gloves for Men", "sports") and ok("Boldfit Resistance Band Set", "sports") and ok("Misamo Enterprise Oil Sprayer 220ml", "kitchen"))
check("beauty and grocery lists are excluded wholesale; formulated items elsewhere too",
      kind("Anything", "beauty") == "formulated" and kind("Meat Up Adult Dry Dog Food 2.4kg", "pet-supplies") == "formulated"
      and kind("Parachute Advansed Coco Nourish Body Lotion", "hpc") == "formulated")
check("look-alike words are not formulated: air conditioner, soap dispenser, protein shaker",
      ok("Voltas 1.5 Ton Split Air Conditioner", "home-improvement") and ok("Steel Soap Dispenser Pump", "kitchen") and ok("Boldfit Protein Shaker Bottle", "sports"))
check("dangerous goods (butane cans) are blocked for reselling", kind("Vinsa Butane Gas Can 225 Gram", "sports") == "hazmat")
check("a brand only counts at the START of the title ('dove-tail saw', 'simple wall hook' are fine)",
      ok("Dove-tail Hand Saw 10 inch", "home-improvement") and resell_gate.brand_of("Noise Cancelling Earphones") == "")
check("brand names with accents/punctuation: L'Oréal, Scotch-Brite", resell_gate.brand_of("L'Oréal Paris Hyaluron Moisture") == "l oreal"
      and resell_gate.brand_of("Scotch-Brite Scrub Pad") != "")
rs = pd.DataFrame([{"asin": "A" * 10, "title": "Safari Trolley", "category_slug": "luggage", "resell_note": resell_gate.check("Safari Trolley", "luggage")["reason"], "seasonal": "", "compliance": ""},
                   {"asin": "B" * 10, "title": "Gesto Gloves", "category_slug": "sports", "resell_note": "", "seasonal": "", "compliance": ""}])
gh = pd.DataFrame([row(d, "A" * 10, 5, 100, 4, 5) for d in days] + [row(d, "B" * 10, 6, 100, 4, 5) for d in days])
gg = stock_gate.gate_all(gh, rs, days[-1])
check("stock gate says NO to a brand before any data test, and still judges a generic one",
      gg["A" * 10].startswith("❌ NO — big brand") and not gg["B" * 10].startswith("❌ NO — big brand"), gg)

# ══ 4. compliance false positives / false negatives ═══════════════════════════
fp1 = compliance.compliance_flags("MIP INTERNATIONAL® Spider Web Shooters Toy for Kids – Hero Launcher Wrist Toy Set | Sticky Wall Soft Bomb", "toys")
fp2 = compliance.compliance_flags("Luyizi Dog Ear Muffs for Noise Protection, Waterproof & Soundproof Pet Ear Protection Headband, Anti-Noise Thunder Fireworks", "pet-supplies")
real = compliance.compliance_flags("Diwali Fire Crackers Combo Pack | Sparklers", "home")
check("a toy 'soft bomb' and dog ear-muffs are NOT explosives any more", "EXPLOSIVES" not in fp1 and "EXPLOSIVES" not in fp2, (fp1, fp2))
check("a real firecracker listing still is", "EXPLOSIVES" in real and "EXPLOSIVES" in compliance.compliance_flags("Green Crackers Bomb Ladi 100 Wala", "home"))
check("butane gas cans carry a DANGEROUS GOODS review flag (v1.49: NOT FLAGGED)", "DANGEROUS GOODS" in compliance.compliance_flags("Vinsa Butane Gas Can 225 Gram", "sports"))
check("an oil SPRAYER is not an FSSAI food-oil", "FSSAI" not in compliance.compliance_flags("Misamo Enterprise Oil Sprayer, 220ml Glass Oil Spray Bottle", "kitchen")
      and "FSSAI" in compliance.compliance_flags("Fortune Sunflower Oil 1L", "grocery"))
sel = pd.DataFrame([{"asin": "FP00000001", "title": "Spider Web Shooters Toy", "category_slug": "toys", "trend_score": 90, "rank_change": 20,
                     "stock_state": "in_stock", "price_inr": 100, "actionable": "True", "days_on_list_14d": 10, "bs_rank_today": 5, "compliance": fp1}])
check("…so the toy is no longer thrown out of the picks", len(picks.select_picks(sel, n=4, today="2026-11-16")) == 1)

# ══ 5. forward test: matched baselines, fair scorecard, interval, holders ═════
HH = pd.DataFrame([row(d, f"P{k:09d}", k, 100, 4, 5, cat="kitchen") for d in days[:3] for k in range(1, 41)])
pk = pd.DataFrame([{"asin": "P000000020", "title": "Pick", "category_slug": "kitchen", "trend_score": 90}])
R = picks.make_rows(pk, HH, days[0])
bl = R[R.role == "baseline"]
check("baselines start within ±15 ranks of the pick and are labelled 'matched'",
      len(bl) == 3 and (bl["rank_at_pick"].astype(float) - 20).abs().le(15).all() and set(bl["baseline_type"]) == {"matched"}, bl)
pk2 = pd.DataFrame([{"asin": "P000000001", "title": "Pick", "category_slug": "kitchen", "trend_score": 90}])
HH2 = pd.DataFrame([row(days[0], f"P{k:09d}", k, 100, 4, 5, cat="kitchen") for k in [1] + list(range(50, 60))])
R2 = picks.make_rows(pk2, HH2, days[0])
check("not enough look-alikes → falls back to random and SAYS so", set(R2[R2.role == "baseline"]["baseline_type"]) == {"random"})
check("Wilson interval is wide for small n", picks.wilson(5, 16)[0] < 0.2 and picks.wilson(5, 16)[1] > 0.5 and picks.wilson(0, 0) == (0.0, 1.0))

def trk(pick_date, role, asin, r0, hit7, alive7, cat="kitchen"):
    return {"pick_date": pick_date, "role": role, "asin": asin, "category_slug": cat, "title": "", "rank_at_pick": r0,
            "hit_d7": hit7, "alive_d7": alive7}
T = pd.DataFrame([trk("2026-12-01", "pick", "p1", 20, 0, 0)] + [trk("2026-12-01", "baseline", f"b{i}", 20 + i, 1, 1) for i in range(3)]
                 + [trk("2026-12-01", "baseline", "far", 90, 0, 1)]                      # starts at rank 90 → excluded from the matched cohort
                 + [trk("2026-12-02", "pick", "p2", 10, 1, 1)] + [trk("2026-12-02", "baseline", f"c{i}", 10 + i, 0, 1) for i in range(3)])
fs = picks.fair_scorecard(T)
def cell(h, metric, cohort, role, col="resolved"):
    return fs[(fs.horizon_days == h) & (fs.metric == metric) & (fs.cohort == cohort) & (fs.role == role)].iloc[0][col]
check("rank-matched cohort drops baselines that started far from the pick", cell(7, "hit", "rank-matched", "baseline") == 6
      and cell(7, "hit", "all", "baseline") == 7, fs[fs.horizon_days == 7])
check("matched cohort rates computed from the right rows", cell(7, "hit", "rank-matched", "pick", "rate") == 0.5
      and abs(cell(7, "hit", "rank-matched", "baseline", "rate") - 0.5) < 1e-9)
check("festival-week pick days are excluded from the non-festival cohort (Dec 1-2 is within 25 Dec ±10? no → included)",
      cell(7, "hit", "non-festival-days", "pick") == 2)
Tf = T.copy(); Tf["pick_date"] = Tf["pick_date"].str.replace("2026-12-0", "2026-11-0", regex=False)       # Nov 1-2: Diwali week
fsf = picks.fair_scorecard(Tf)
check("a Diwali-week pick day leaves the non-festival cohort empty (instead of blaming the picks)",
      fsf[(fsf.horizon_days == 7) & (fsf.metric == "hit") & (fsf.cohort == "non-festival-days") & (fsf.role == "pick")].iloc[0]["resolved"] == 0)
line = picks.fair_line(fs)
check("one-line digest summary says 'inconclusive' when intervals overlap", "Like-for-like (7d)" in line and "inconclusive" in line, line)
check("festival calendar helper", season.near_festival("2026-11-09") == "Diwali" and season.near_festival("2026-12-02") == "")

pool = pd.DataFrame([{"asin": "CL00000001", "title": "Climber", "category_slug": "kitchen"}, {"asin": "HO00000001", "title": "Holder", "category_slug": "kitchen"}])
hh = pd.DataFrame([row(d, "CL00000001", 40 if i < 12 else 10) for i, d in enumerate(days)] + [row(d, "HO00000001", 51) for d in days])
rj = []
kept = picks.drop_holders(pool, hh, days[-1], rj)
check("a pick whose own history says 'holding #51' never reaches the public pool", list(kept["asin"]) == ["CL00000001"]
      and any("no climb" in w for _, w in rj), rj)
check("post copy no longer promises 'Rising' for every item", "Rising on Amazon" not in picks.wa_bundle(pool, hh, "t-21", days[-1])[0]
      and "Rising on Amazon" not in picks.post_messages(pool, hh, "t-21", days[-1], style="single")[0])

# ══ 6. versions, movers skip, rise display, state keywords ═══════════════════
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trend_fetcher.py"), encoding="utf-8").read()
stale = re.findall(r"v1\.\d\d(?![\d.])[^\n]{0,40}", "\n".join(l for l in src.splitlines() if "{VERSION}" not in l and l.lstrip().startswith(("lines", "log(", "L = [f", "send_telegram")) and "BAZAAR RADAR" in l))
check("no hard-coded version in banners/digests (the digest said v1.45 for four releases)", not stale, stale)
check("VERSION is a major.minor string", re.match(r"^\d+\.\d+$", tf.VERSION) is not None)

HM = pd.DataFrame([row(d, "A" * 10, 5) for d in days[:10]])
check("movers: 5+ days of bestsellers and no movers row ever → skip", tf.movers_dead(HM, "2026-11-11") in (True, False))
today_dead = next(d for d in (f"2026-11-{x:02d}" for x in range(11, 29)) if datetime.strptime(d, "%Y-%m-%d").toordinal() % tf.MOVERS_REPROBE_EVERY != 0)
check("movers skipped on an ordinary day", tf.movers_dead(HM, today_dead) is True)
rp = next(d for d in (f"2026-11-{x:02d}" for x in range(11, 29)) if datetime.strptime(d, "%Y-%m-%d").toordinal() % tf.MOVERS_REPROBE_EVERY == 0)
check("…but re-checked once a week", tf.movers_dead(HM, rp) is False)
HM2 = pd.concat([HM, pd.DataFrame([row(days[9], "Z" * 10, 3, lst="movers")])], ignore_index=True)
check("a movers row in the last 5 days brings the fetch back", tf.movers_dead(HM2, today_dead) is False)
check("too little history → keep fetching", tf.movers_dead(HM.head(2), today_dead) is False)

check("rise display is capped: never +2,347,100%", tf.fmt_rise(2347100) == "+5,000%+ (breakout)" and tf.fmt_rise("350") == "+350%" and tf.fmt_rise("Breakout") == "Breakout")

titles = (["Acme Steel Lunch Box 3 Compartment | Leakproof"] * 3 + ["Zed Stainless Steel Lunch Box Set Insulated"] * 2
          + ["Qux Plastic Storage Box With Lid"] * 4 + ["Toy Boys Gift Pack Boys Toy Car"] * 1)
ks = pd.DataFrame([{"asin": f"Q{i:09d}", "title": t, "category_slug": "kitchen", "trend_score": 90 - i, "actionable": "True", "seasonal": "", "compliance": ""}
                   for i, t in enumerate(titles)])
kw = tf.product_keywords(ks, 5)
check("state keywords are real contiguous phrases from ≥3 titles, not 'toy boys'", "lunch box" in kw and "storage box" in kw
      and not any(k in kw for k in ("toy boys", "boys toy")), kw)

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
