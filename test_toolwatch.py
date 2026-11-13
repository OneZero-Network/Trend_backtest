"""v1.46–1.47 Tool Watch: arithmetic cross-checked against the real calculator workbook (LibreOffice), calendar, feeds,
scoring, ticket text limits, tracker (live → keep/kill), portfolio rules and a full offline weekly run."""
import glob, html, json, os, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import toolwatch as T
import calculator as C
import compliance as K
import trend_fetcher as tf

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

TMP = tempfile.mkdtemp()

# ───────────────────────── 1. arithmetic = the real calculator formulas (recalculated in LibreOffice)
def recalc(p):
    skill = glob.glob("/root/.claude/skills/**/xlsx/scripts/recalc.py", recursive=True) + glob.glob("/mnt/skills/**/xlsx/scripts/recalc.py", recursive=True)
    if skill:
        out = subprocess.run([sys.executable, skill[0], p, "90"], capture_output=True, text=True).stdout
        return json.loads(out[out.index("{"):])
    if shutil.which("soffice"):
        od = os.path.join(os.path.dirname(p), "rc"); os.makedirs(od, exist_ok=True)
        subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", od, p], capture_output=True)
        shutil.copy(os.path.join(od, os.path.basename(p)), p); return {"status": "success", "total_errors": 0}
    return None

def sheet_result(tag, price, cost, itc=1, referral=0.10, rto=0.10, ship=60):
    import openpyxl
    path = os.path.join(TMP, f"CALC_{tag}.xlsx")
    sig = {"asin": "X1", "title": "Product X1", "category_slug": "electronics", "trend_score": 80, "signal": "📈",
           "price_inr": price, "seller_offers": 4, "bought_past_month_min": 1000, "compliance": K.NOT_FLAGGED,
           "competition": "Moderate", "url": "https://amazon.in/dp/X1"}
    C.update_xlsx(path, [sig], today="2026-11-01")
    wb = openpyxl.load_workbook(path); ws = wb[C.CALC_TAB]
    r = next(i for i in range(2, ws.max_row + 1) if ws[f"A{i}"].value == "X1")
    for k, v in (("sourcing", cost), ("moq", 100), ("defect", 0.03), ("referral", referral), ("closing", 30), ("ship", ship),
                 ("rto", rto), ("ads", price * 0.08), ("sale_gst", 0.18), ("purchase_gst", 0.18)):
        ws[f"{C.L[k]}{r}"] = v
    wb[C.ASSUMP_TAB]["B5"] = itc
    wb.save(path)
    if recalc(path) is None:
        return None
    v = openpyxl.load_workbook(path, data_only=True)[C.CALC_TAB]
    return {k: v[f"{C.L[k]}{r}"].value for k in ("usable", "fees", "gst_pay", "rto_cost", "total", "profit", "margin", "roi", "cash", "decision")}

scen = {"A": dict(price=499, cost=150), "B": dict(price=299, cost=180),
        "C": dict(price=999, cost=400, itc=0, referral=0.15, rto=0.25, ship=80)}
for tag, kw in scen.items():
    sh = sheet_result(tag, **kw)
    if sh is None:
        print("SKIP  LibreOffice not available — tool arithmetic not cross-checked"); break
    py = T.unit_econ(kw["price"], kw["cost"], **{k: v for k, v in kw.items() if k not in ("price", "cost")})
    same = all(abs(float(sh[k]) - py[k]) < 1e-6 for k in ("usable", "fees", "gst_pay", "rto_cost", "total", "profit", "margin", "roi", "cash"))
    check(f"scenario {tag}: tool arithmetic equals the real CALCULATOR workbook (all 9 outputs)", same, f"{sh} vs {py}")
    check(f"scenario {tag}: same decision as the workbook", sh["decision"].split(" —")[0].split(" (")[0] == py["decision"].split(" —")[0].split(" (")[0]
          and sh["decision"][:2] == py["decision"][:2], f"{sh['decision']} vs {py['decision']}")

# minimum price: closed form is exact
for kw in (dict(cost=150), dict(cost=400, margin=0.25, roi=0.5, referral=0.15, rto=0.25, ship=80), dict(cost=90, margin=0.3)):
    m = T.min_price(**kw)
    other = {k: v for k, v in kw.items() if k not in ("cost", "margin", "roi")}
    e = T.unit_econ(m["price"], kw["cost"], **other)
    tm, tr = kw.get("margin", 0.15), kw.get("roi", 0.30)
    check(f"minimum price {kw}: clears both targets and the binding one is exact",
          e["margin"] >= tm - 1e-9 and e["roi"] >= tr - 1e-9 and (abs(e["margin"] - tm) < 1e-9 or abs(e["roi"] - tr) < 1e-9), (m, e["margin"], e["roi"]))
check("minimum price: impossible when fees eat the whole price", T.min_price(100, referral=0.6, ads_pct=0.2)["price"] is None
      or T.min_price(100, referral=0.9, ads_pct=0.2)["price"] is None)
rl = T.rto_loss(1000, 0.15, 60, 2, damaged=0.10, cost=150)
check("RTO loss: 150 failed orders, ₹18,000 shipping + ₹2,250 unsellable = ₹20,250, ₹23.82 per delivered order",
      abs(rl["rto_orders"] - 150) < 1e-9 and abs(rl["ship_lost"] - 18000) < 1e-6 and abs(rl["damaged_lost"] - 2250) < 1e-6
      and abs(rl["total"] - 20250) < 1e-6 and abs(rl["per_delivered"] - 23.8235294) < 1e-5, rl)

# ───────────────────────── 2. calendar
fp = T.festival_plan("Makar Sankranti/Lohri/Pongal", "2026-11-13")
check("festival plan: stock 120→45 days before, promote 30→3, peak 4 days before",
      (fp["stock_from"], fp["stock_until"], fp["promote_from"], fp["promote_until"], fp["peak"], fp["days_to"])
      == ("2026-09-16", "2026-11-30", "2026-12-15", "2027-01-11", "2027-01-10", 62), fp)
ev = [p["event"] for p in T.upcoming_events("2026-11-13")]
check("upcoming events on 13 Nov: Christmas, Makar Sankranti, Republic Day, Valentine's — not Chhath (too late), not Holi/Eid (too early), "
      "one page per date", ev == ["Christmas/New Year", "Makar Sankranti/Lohri/Pongal", "Republic/Independence Day", "Valentine's"], ev)
check("no event listed whose promotion window is over", all(T.festival_plan(e, "2027-01-20")["event"] != "Makar Sankranti/Lohri/Pongal"
      for e in [p["event"] for p in T.upcoming_events("2027-01-20")]), [p["event"] for p in T.upcoming_events("2027-01-20")])
check("slugs have no stray apostrophe letters", T.slugify("Valentine's") == "valentines")

# ───────────────────────── 3. feeds
check("tool-shaped: accepted", all(T.tool_shaped(q) for q in ("amazon seller profit calculator", "instagram bio generator", "image compressor online free")))
check("tool-shaped: rejected (regulated, banned, one word, not a tool, login, app)",
      not any(T.tool_shaped(q) for q in ("gst calculator", "income tax calculator", "rummy score calculator", "calculator", "calculator app download",
                                         "sbi login", "loan emi calculator", "cricket score tracker")))
# v1.47 regressions from the live run of 13 Nov (TW-2646-05 "Murasu Cyclone Tracker +193,500%")
check("live regression: a cyclone tracker is a news spike / live-data tool, never a ticket",
      T.classify("murasu cyclone tracker") == "news/weather event — spikes die in days", T.classify("murasu cyclone tracker"))
check("live-data tools (train, flight, courier, gold price, weather) are not buildable without a feed",
      all(T.classify(q) == "needs a live data feed" for q in ("train running tracker", "courier parcel tracker", "weather radar tracker")))
check("sports / election spikes filtered", all(T.classify(q) for q in ("t20 world cup score tracker", "election exit poll calculator")))
check("evergreen look-alikes still pass (budget planner, rainwater harvesting calculator)",
      T.classify("monthly budget planner") == "" and T.classify("rainwater harvesting calculator") == "")
check("canonical key drops noise words", T.canon_key("Free Online EMI Calculator India 2026") == "emi-calculator")
raw = [{"query": "amazon seller fee calculator", "rise": "+900%", "seed": "amazon seller"},
       {"query": "Amazon Seller Fee Calculator India", "rise": 400, "seed": "calculator"},
       {"query": "instagram bio generator", "rise": "Breakout", "seed": "generator"},
       {"query": "age calculator", "rise": 3000, "seed": "calculator"},
       {"query": "gst calculator", "rise": 9000, "seed": "calculator"},
       {"query": "weak thing maker", "rise": 120, "seed": "maker"}]
rej = []
cs = T.trend_candidates(raw + [{"query": "murasu cyclone tracker", "rise": 193500, "seed": "tracker"}], {}, "2026-11-16", rejected=rej)
by = {c["key"]: c for c in cs}
check("trend candidates: duplicates merged (max rise), regulated and weak dropped",
      sorted(by) == ["q:age-calculator", "q:amazon-seller-fee-calculator", "q:instagram-bio-generator"] and by["q:amazon-seller-fee-calculator"]["rise"] == 900, sorted(by))
check("rejected list keeps the reason (shown in the weekly digest)",
      any(r["query"] == "gst calculator" and "regulated" in r["reason"] for r in rej) and any(r["query"] == "murasu cyclone tracker" and "news" in r["reason"] for r in rej), rej)
check("seller fit and trivial flags set", by["q:amazon-seller-fee-calculator"]["seller"] and by["q:age-calculator"]["trivial"] and not by["q:instagram-bio-generator"]["trivial"])
seen1 = T.update_seen([], cs, "2026-11-16")
seen1b = T.update_seen(seen1, cs, "2026-11-17")
seen2 = T.update_seen(seen1b, cs, "2026-11-23")
wk = {r["key"]: r["weeks"] for r in seen2}
check("persistence counts distinct WEEKS (not runs)", wk["q:instagram-bio-generator"] == "2", wk)
c2 = T.trend_candidates(raw, {r["key"]: r for r in seen1}, "2026-11-23")
check("2nd week seen → weeks = 2", {c["key"]: c["weeks"] for c in c2}["q:instagram-bio-generator"] == 2)
opp = [{"week": "2026-11-16", "name": "Split PDF by chapter tool", "gap": "BUILD", "effort": "S", "regulated": "False", "requests": "3",
        "votes": "4", "links": "https://a https://b", "score": "40", "sources": "softwarerecs", "gh_easy": "0", "web_tool_pages": "-1"},
       {"week": "2026-11-16", "name": "Claude Desktop usage panel", "gap": "VENDOR-ONLY", "effort": "M", "regulated": "False", "requests": "3", "score": "60"},
       {"week": "2026-11-16", "name": "Login to platform X API", "gap": "HARD", "effort": "M", "regulated": "False", "requests": "5", "score": "70"},
       {"week": "2026-11-16", "name": "GST invoice generator", "gap": "BUILD", "effort": "S", "regulated": "True", "requests": "9", "score": "90"},
       {"week": "2026-11-16", "name": "Full ERP", "gap": "BUILD", "effort": "L", "regulated": "False", "requests": "9", "score": "90"},
       {"week": "2026-11-09", "name": "Old week thing", "gap": "BUILD", "effort": "S", "regulated": "False", "requests": "9", "score": "99"}]
ac = T.ask_candidates(opp, "2026-11-16")
check("asks: only latest week, no vendor-only / platform-locked / regulated / large builds", [c["title"] for c in ac] == ["Split Pdf By Chapter Tool"] or [c["query"] for c in ac] == ["split pdf by chapter tool"], [c["query"] for c in ac])

# ───────────────────────── 4. scoring
eng = T.engine_candidates("2026-11-13")
check("engine tools: 4 tools, each with test cases and an exact formula or rule", len(eng) == 4 and all(c["tests"] and c["formula"] for c in eng))
check("festival planner gets calendar urgency when a window opens within 35 days", eng[3]["base_score"] == 92 and T.score(eng[3])[0] == 92)
ok_shape = {"label": "ok", "rel": 0.2, "recent_share": 0.15, "avg": 20, "active": 0.9}
base = {"type": "search", "rise": 900, "weeks": 1, "seller": False, "trivial": False, "shape": ok_shape}
s0 = T.score(base, {"web_tool_pages": 2})[0]
check("crowded page 1 lowers the score", T.score(base, {"web_tool_pages": 6})[0] < s0 * 0.5)
check("not checked = ×0.7, never free", abs(T.score(base, None)[0] - s0 * 0.7) < 0.2, (T.score(base, None), s0))
check("v1.47: ZERO tool sites on page 1 is not a free opportunity (×0.5, 1–2 sites prove tool intent)",
      abs(T.score(base, {"web_tool_pages": 0})[0] - s0 * 0.5) < 0.2 and T.score(base, {"web_tool_pages": 1})[0] == s0, (T.score(base, {"web_tool_pages": 0}), s0))
check("persistence ×1.3, seller ×1.25, trivial ×0.6",
      abs(T.score({**base, "weeks": 2}, {"web_tool_pages": 2})[0] - min(100, s0 * 1.3)) < 0.2
      and abs(T.score({**base, "seller": True}, {"web_tool_pages": 2})[0] - min(100, s0 * 1.25)) < 0.2
      and abs(T.score({**base, "trivial": True}, {"web_tool_pages": 2})[0] - s0 * 0.6) < 0.2)
check("v1.47: a % rise beyond 5,000% adds nothing (it only measures how tiny the base was)",
      T.score({**base, "rise": 193500}, {"web_tool_pages": 2})[0] == T.score({**base, "rise": 5000}, {"web_tool_pages": 2})[0]
      and "capped" in T.score({**base, "rise": 193500}, {"web_tool_pages": 2})[1])
sp = {"label": "spike", "rel": 0.1, "recent_share": 0.9, "avg": 5, "active": 0.2}
tn = {"label": "tiny", "rel": 0.001, "recent_share": 0.1, "avg": 0.3, "active": 0.1}
check("v1.47: a news-style spike scores ×0.2, a tiny base ×0.5, an unverified volume ×0.6",
      abs(T.score({**base, "shape": sp}, {"web_tool_pages": 2})[0] - s0 * 0.2) < 0.2 and abs(T.score({**base, "shape": tn}, {"web_tool_pages": 2})[0] - s0 * 0.5) < 0.2
      and abs(T.score({**base, "shape": None}, {"web_tool_pages": 2})[0] - s0 * 0.6) < 0.2)
cyc = {**base, "rise": 193500, "shape": None, "seller": False}
check("live regression: the cyclone ticket would have scored under the quality bar even if the filter missed it",
      T.score(cyc, {"web_tool_pages": 0})[0] < 25, T.score(cyc, {"web_tool_pages": 0}))
flat = [10] * 90
check("demand curve: steady searches vs an anchor → ok", T.shape_from_series(flat, [50] * 90)["label"] == "ok" and abs(T.shape_from_series(flat, [50] * 90)["rel"] - 0.2) < 1e-9)
check("demand curve: 90% of interest in the last 14 days → spike", T.shape_from_series([0] * 76 + [20] * 14, [50] * 90)["label"] == "spike")
check("demand curve: a trickle next to the anchor → tiny; all zeros → tiny", T.shape_from_series([1, 0, 0] * 30, [50] * 90)["label"] == "tiny" and T.shape_from_series([0] * 90)["label"] == "tiny")
check("demand curve: too few points → unknown (never guessed)", T.shape_from_series([5] * 10)["label"] == "unknown")
check("shape note names the verdict in words", "SPIKE" in T.shape_note(T.shape_from_series([0] * 76 + [20] * 14, [50] * 90), "image compressor"))
eng_sc = sorted(((*T.score(c), c) for c in eng), key=lambda x: -x[0])
good = [{"key": f"q:good{i}", "type": "search", "title": f"Good {i}", "query": f"good {i} calculator", "slug": f"good-{i}", "noun": "calculator",
         "group": "calculator", "rise": 900, "weeks": 1, "seller": True, "trivial": False, "shape": ok_shape} for i in range(3)]
junk = {"key": "q:junk", "type": "search", "title": "Junk", "query": "junk tracker", "slug": "junk", "noun": "tracker", "group": "planner",
        "rise": 100000, "weeks": 1, "seller": False, "trivial": False, "shape": None}
scored = sorted(((*T.score(c, {"web_tool_pages": 2}), c) for c in eng + good + [junk]), key=lambda x: -x[0])
pk, st = T.pick(scored, [], "2026-11-16", n=5, max_open=8)
kinds = sorted(c["type"] for _, _, c in pk)
check("pick (week 1): Wave 1 is staged — 3 engine tools + 2 reserved slots for what the market shows", kinds == ["engine"] * 3 + ["search"] * 2, kinds)
check("pick: a rising search with NO demand-curve check is never ticketed blind", all(c["key"] != "q:junk" for _, _, c in pk) and st["unverified"] == 1, st)
junk2 = {**junk, "key": "q:junk2", "shape": sp}
pkj, stj = T.pick(sorted(((*T.score(c, {"web_tool_pages": 2}), c) for c in eng + good + [junk2]), key=lambda x: -x[0]), [], "2026-11-16", n=5, max_open=8)
check("pick: a verified news-style spike is under the quality bar and never ticketed", all(c["key"] != "q:junk2" for _, _, c in pkj) and stj["below_bar"] == 1, stj)
pk0, st0 = T.pick([x for x in scored if x[2]["type"] == "engine"] + [(10.0, "x", junk)], [], "2026-11-16", n=5, max_open=8)
check("pick: nothing else clears the bar → engine tools use every slot", [c["type"] for _, _, c in pk0].count("engine") == 4 and len(pk0) == 4, [c["key"] for _, _, c in pk0])
rows0 = [{"key": "eng:rto", "status": "NEW", "type": "engine", "created": "2026-11-02"}]
pk, st = T.pick(scored, rows0, "2026-11-16", n=5, max_open=8)
check("pick: skips tools already ticketed, respects the weekly allowance", all(c["key"] != "eng:rto" for _, _, c in pk) and len(pk) == 5, [c["key"] for _, _, c in pk])
rows_full = [{"key": f"k{i}", "status": "NEW", "type": "search", "created": "2026-10-01"} for i in range(8)]
pk2, st2 = T.pick(scored, rows_full, "2026-11-16", n=5, max_open=8)
check("pick: team backlog full (8 open) → no new tickets", pk2 == [] and st2["room"] == 0)
killed = [{"key": "q:good0", "status": "KILL", "type": "search", "decision": "2026-10-20: below the bar", "created": "2026-08-01"}]
pk3, _ = T.pick(scored, killed, "2026-11-16", n=20, max_open=99)
check("pick: a tool killed in the last 90 days is not re-proposed", all(c["key"] != "q:good0" for _, _, c in pk3))

# ── festival planner: no day count without an answer (live regression: Christmas is 42 days away on 13 Nov)
import season as SE
check("season.phase: every day count has an answer, 31–44 days = prepare (not 'stock window opens <past date>')",
      [SE.phase(d)[0] for d in (130, 121, 120, 45, 44, 31, 30, 3, 2, 0)] == ["early", "early", "stock", "stock", "prepare", "prepare", "promote", "promote", "too_late", "too_late"])
check("Christmas on 13 Nov (42 days) → prepare, not a past 'opens' date", T.festival_plan("Christmas/New Year", "2026-11-13")["phase"] == "prepare"
      and "opens 2026-08" not in SE.window("SEASONAL: Christmas/New Year", "2026-11-13")["note"] and "prepare" in SE.window("SEASONAL: Christmas/New Year", "2026-11-13")["note"],
      SE.window("SEASONAL: Christmas/New Year", "2026-11-13")["note"])
fest = [c for c in eng if c["key"] == "eng:festival"][0]
check("festival ticket spells out the full verdict table incl. the 31–44 day state and tests its boundaries",
      any("31–44" in l and "do NOT order new stock" in l for l in fest["formula"]) and sum("prepare creatives" in t for t in fest["tests"]) >= 1
      and any("promote only" in t for t in fest["tests"]) and any("too late" in t for t in fest["tests"]) and any("(1 day)" in t for t in fest["tests"]), fest["tests"])
check("event pages carry today's verdict", all("today:" in p for p in fest["pages"]) and any("prepare creatives" in p for p in fest["pages"]))
tkf = T.make_ticket(fest, "TW-2646-01", "2026-11-13", neighbours=slugs[:3] if False else [], score_val=92, parts="p")
check("festival ticket still fits one message and keeps WHY NOW (with its demand check)", len(html.escape(tkf["box"])) <= T.BOX_LIMIT and "WHY NOW" in tkf["box"], len(html.escape(tkf["box"])))

# ───────────────────────── 5. tickets
slugs = [c["slug"] for c in eng]
boxes = []
for i, c in enumerate(eng):
    s, parts = T.score(c)
    tk = T.make_ticket(c, f"TW-2646-0{i + 1}", "2026-11-13", site="https://bizopstool.example", neighbours=T.neighbours_for(c, slugs, slugs), score_val=s, parts=parts)
    boxes.append(tk)
check("every engine ticket fits one Telegram message (escaped ≤ 3,600 chars)", all(len(html.escape(t["box"])) <= T.BOX_LIMIT for t in boxes), [len(html.escape(t["box"])) for t in boxes])
check("engine tickets keep formula + test cases even when trimmed", all("TEST CASES" in t["box"] and "EXACT FORMULA" in t["box"] for t in boxes))
check("full text (file / sheet) always has everything", all("Link TO" in t["md"] and "KEEP if" in t["md"] and "Events:" in t["md"] for t in boxes))
check("ticket states ID, build path, target search, ship-by date", all(x in boxes[0]["box"] for x in ("TW-2646-01", "/tools/amazon-india-seller-profit-calculator", "Target search:", "Ship by: 2026-11-27")))
check("negative margin/ROI use the same proper minus as the rupee figures", "margin −18.7%" in boxes[0]["md"] + boxes[1]["md"] + boxes[2]["md"] + boxes[3]["md"]
      and "margin -" not in "".join(t["md"] for t in boxes), [t["md"][:0] for t in boxes])
check("ticket never presents defaults as Amazon's fees", "never present them as Amazon's fees" in boxes[0]["md"])
check("tracker row is complete and starts as NEW", set(boxes[0]["row"]) == set(T.TICKET_COLS) and boxes[0]["row"]["status"] == "NEW")
check("SEO title ≤ 60 chars and never cut mid-word",
      all(len(T.seo_title(c["title"])) <= 60 for c in eng) and not T.seo_title("Festival Stock & Promotion Planner for Indian Sellers").endswith("Sig"))
sc = {**by["q:amazon-seller-fee-calculator"], "web_pages": 4, "web_examples": "a.com, b.com"}
tk = T.make_ticket(sc, "TW-2647-01", "2026-11-23", neighbours=slugs[:3], score_val=30, parts="x")
check("rising-search ticket: evidence, competition, spec pattern, formula-source rule",
      "+900%" in tk["box"] and "4 dedicated tool sites" in tk["box"] and "authoritative" in tk["box"] and len(html.escape(tk["box"])) <= T.BOX_LIMIT, tk["box"][:400])
tk2 = T.make_ticket({**ac[0], "web_pages": -1}, "TW-2647-02", "2026-11-23", neighbours=slugs[:3], score_val=20, parts="y")
check("ask ticket: links to the asks and says page 1 was not checked", "Ask: https://a" in tk2["box"] and "not checked automatically" in tk2["box"])
check("standing rules without a site have no '<site>' placeholder", "<site>" not in T.standing_rules() and "bizopstool site" in T.standing_rules())
check("standing rules fit one message and forbid regulated tools", len(html.escape(T.standing_rules())) < 3900 and "tax, legal, medical, loan" in T.standing_rules())

# ───────────────────────── 6. tracker
rows = [{"ticket_id": "A", "key": "eng:profit", "type": "engine", "title": "Profit calc", "slug": "profit", "status": "NEW", "ship_by": "2026-11-20",
         "live_since": "", "impressions_28d": "", "clicks_28d": "", "decision": ""},
        {"ticket_id": "B", "key": "q:x", "type": "search", "title": "Other", "slug": "other", "status": "NEW", "ship_by": "2026-10-01",
         "live_since": "", "impressions_28d": "", "clicks_28d": "", "decision": ""},
        {"ticket_id": "RULES", "key": "rules:v1", "type": "rules", "title": "Rules", "slug": "", "status": "SENT"}]
rows, evs = T.update_tracker(rows, "2026-11-30", live_fn=lambda s: s == "profit", stats=None)
check("page detected live → LIVE, judged 56 days later", rows[0]["status"] == "LIVE" and rows[0]["live_since"] == "2026-11-30" and rows[0]["judge_on"] == "2027-01-25" and rows[1]["status"] == "NEW" and evs)
rows, evs = T.update_tracker(rows, "2026-12-30", live_fn=lambda s: False, stats={"profit": (0, 0)})
check("28 days live with zero impressions → warning, not a verdict", rows[0]["status"] == "LIVE" and "no impressions" in rows[0]["decision"] and any("no impressions" in e for e in evs))
rows, evs = T.update_tracker(rows, "2027-01-30", live_fn=lambda s: False, stats={"profit": (900, 30)})
check("week 8: meets the bar → KEEP", rows[0]["status"] == "KEEP" and any("KEEP" in e for e in evs), rows[0])
rows2 = [{"ticket_id": "C", "key": "k", "type": "search", "title": "Weak", "slug": "weak", "status": "LIVE", "ship_by": "x", "live_since": "2026-11-01",
          "impressions_28d": "", "clicks_28d": "", "decision": ""}]
rows2, evs = T.update_tracker(rows2, "2027-01-05", stats={"weak": (120, 2)})
check("week 8: below the bar → KILL", rows2[0]["status"] == "KILL" and any("KILL" in e for e in evs))
pf = T.portfolio(rows, "2027-01-30", have_site=False, have_gsc=False)
check("portfolio: tells you what to set up and flags late tickets", any("BIZOPS_SITE" in l for l in pf) and any("GSC_SITE" in l for l in pf) and any("past their ship-by" in l for l in pf), pf)
live = [{"ticket_id": str(i), "type": "search", "slug": f"s{i}", "status": "LIVE", "live_since": "2026-08-01", "impressions_28d": "40", "clicks_28d": "2", "ship_by": "2026-08-01"} for i in range(4)]
pf2 = T.portfolio(live, "2026-12-15", have_site=True, have_gsc=True)
check("week-8 rule: fewer than 3 tools meet the bar → change the mix", any("Week-8 rule" in l for l in pf2), pf2)
check("week-16 rule: under 1,000 clicks → park the lane", any("Week-16 rule" in l for l in pf2), pf2)
check("nudge text lists late tickets for you to copy", "Status check" in T.nudge_text(rows, "2027-01-30") and "Other" in T.nudge_text(rows, "2027-01-30"))
gs = T.stats_by_slug([{"keys": ["https://x.test/tools/abc/diwali-2026?utm=1"], "impressions": 10, "clicks": 1},
                      {"keys": ["https://x.test/tools/abc"], "impressions": 5, "clicks": 0}, {"keys": ["https://x.test/tools/abcd"], "impressions": 99, "clicks": 9}], ["abc", "abcd"])
check("Search Console rows map to tools (event pages count, similar slugs don't mix)", gs == {"abc": (15, 1), "abcd": (99, 9)}, gs)

# ───────────────────────── 7. full offline weekly run
tf.LOCAL_OUT_DIR = TMP
sent, docs = [], []
tf.send_telegram = lambda m: sent.append(m)
tf.send_telegram_document = lambda path, caption="", ctype=None: docs.append(path)
raw_live = raw + [{"query": "murasu cyclone tracker", "rise": 193500, "seed": "tracker"},
                  {"query": "business card maker", "rise": 700, "seed": "maker"}]
tf.tw_trends = lambda seeds: (raw_live, 0)
shape_calls = []
def fake_shapes(queries, anchor=None):
    shape_calls.append(list(queries))
    return {q[:90]: ({"label": "tiny", "rel": 0.004, "recent_share": 0.1, "avg": 0.5, "active": 0.2} if q.startswith("age calc") else
                     {"label": "ok", "rel": 0.2, "recent_share": 0.15, "avg": 20.0, "active": 0.9}) for q in queries}
tf.tw_shapes = fake_shapes
tf.auto_web = lambda q: {k: {"web_tool_pages": 2, "web_examples": "x.com"} for k in q}
tf.tw_gsc = lambda days=28: None
tf.BIZOPS_SITE = ""
pd.DataFrame(opp).to_csv(os.path.join(TMP, "OPPORTUNITIES.csv"), index=False)
def run_on(day):
    tf.TODAY = day
    tf.NOW_IST = datetime.strptime(day, "%Y-%m-%d").replace(hour=14, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    sent.clear(); docs.clear()
    tf.main_toolwatch()
run_on("2026-11-16")
tt = pd.read_csv(os.path.join(TMP, "TOOL_TICKETS.csv"), dtype=str).fillna("")
real = tt[tt["type"] != "rules"]
check("week 1 (staged Wave 1): 3 engine tools + the 2 best verified rising searches, plus the rules row — and the cyclone is not one of them",
      set(real["key"]) == {"eng:festival", "eng:profit", "eng:minprice", "q:amazon-seller-fee-calculator", "q:instagram-bio-generator"}
      and (tt["type"] == "rules").sum() == 1 and not real["title"].str.contains("Cyclone").any(), list(real["key"]))
check("demand curves were requested for the engine tools and the strongest candidates only",
      shape_calls and any("amazon india seller profit calculator" in q for q in shape_calls[0]) and not any("cyclone" in q for q in shape_calls[0]), shape_calls[:1])
boxes_sent = [m for m in sent if m.startswith("<pre>")]
check("week 1: digest + rules box + 5 ticket boxes, each within Telegram's limit, one file attached",
      len(sent) == 7 and len(boxes_sent) == 6 and all(len(m) < 4096 for m in sent) and len(docs) == 1 and docs[0].endswith(".md"), (len(sent), len(docs)))
check("week 1: digest explains how to use it and the honest limits", "tap a grey box" in sent[0] and "Honest limits" in sent[0] and "standing rules" in sent[0])
check("week 1: digest shows what was filtered and why (cyclone = news spike, GST = regulated) and what was considered",
      "Filtered (and why)" in sent[0] and "murasu cyclone tracker" in sent[0] and "news/weather" in sent[0] and "regulated" in sent[0] and "Considered, not ticketed" in sent[0], sent[0][:900])
md = open(docs[0], encoding="utf-8").read()
check("attached file has the rules and every ticket in full", "STANDING RULES" in md and md.count("## TW-") == 5, md.count("## TW-"))
run_on("2026-11-16")
check("same week, run again: no new tickets, no rules box, no file", len(pd.read_csv(os.path.join(TMP, "TOOL_TICKETS.csv"))) == 6
      and len(sent) == 1 and not docs and "No new tickets" in sent[0], (len(sent), sent[0][:200]))
os.environ["BR_TW_RESEND"] = "1"; run_on("2026-11-16"); os.environ["BR_TW_RESEND"] = ""
check("resend flag sends this week's tickets again", len([m for m in sent if m.startswith("<pre>")]) == 5 and len(docs) == 1, len(sent))
tf.BIZOPS_SITE = "https://x.test"
tf.tw_live = lambda slug: slug == "amazon-india-seller-profit-calculator"
run_on("2026-11-23")
tt = pd.read_csv(os.path.join(TMP, "TOOL_TICKETS.csv"), dtype=str).fillna("")
pr = tt[tt["slug"] == "amazon-india-seller-profit-calculator"].iloc[0]
check("week 2: the page the team published is detected as LIVE without anyone reporting it", pr["status"] == "LIVE" and pr["live_since"] == "2026-11-23")
new2 = tt[tt["created"] == "2026-11-23"]
check("week 2: new tickets only for what is not yet ticketed; backlog cap respected", 1 <= len(new2) <= 4 and not (set(new2["key"]) & set(real["key"])), list(new2["key"]))
bc = new2[new2["key"] == "q:business-card-maker"]
check("week 2: a search seen in week 1 and ticketed now says 'seen in 2 different weeks'",
      len(bc) == 1 and "seen in 2 different weeks" in bc.iloc[0]["text"], bc["text"].tolist()[:1])
check("week 2: the 4th engine tool (held back in week 1) is ticketed now", "eng:rto" in set(new2["key"]), list(new2["key"]))
check("tickets carry the demand-curve line", "Demand check:" in bc.iloc[0]["text"] and "OK" in bc.iloc[0]["text"], bc.iloc[0]["text"][:300])
tf.tw_gsc = lambda days=28: [{"keys": ["https://x.test/tools/amazon-india-seller-profit-calculator"], "clicks": 30, "impressions": 900}]
run_on("2027-01-25")
tt = pd.read_csv(os.path.join(TMP, "TOOL_TICKETS.csv"), dtype=str).fillna("")
pr = tt[tt["slug"] == "amazon-india-seller-profit-calculator"].iloc[0]
check("week 9: Search Console numbers in → KEEP decided automatically and reported", pr["status"] == "KEEP" and any("KEEP" in m for m in sent[:1]), (pr["status"], pr["decision"]))
cand = pd.read_csv(os.path.join(TMP, "TOOL_CANDIDATES.csv"), dtype=str)
check("every scored candidate is saved with its score explanation and demand-curve numbers", len(cand) >= 5 and cand["why_score"].str.len().min() > 5
      and {"shape", "rel_volume", "recent_share"} <= set(cand.columns))

shutil.rmtree(TMP, ignore_errors=True)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
