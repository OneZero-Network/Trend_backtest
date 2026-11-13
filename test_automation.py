"""v1.35 automation-opportunity engine: grouping, supply, gap types, shortlist, waitlist gate, offline run."""
import os, sys, shutil, tempfile
from datetime import datetime, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import automation as A

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

check("titles map to problem groups",
      A.bucket_of("How to auto-generate GST invoice PDF from Google Sheets") == ["gst_invoice", "pdf_docs"]
      and A.bucket_of("Send WhatsApp message when a row is added") == ["whatsapp_msg"]
      and A.bucket_of("VBA loop through cells") == [], A.bucket_of("How to auto-generate GST invoice PDF from Google Sheets"))
check("trend seeds rotate weekly", A.trend_seeds("2026-11-09") != A.trend_seeds("2026-11-16"))

# Stack Overflow API item shape
so = A.so_demand([
    {"title": "Generate invoice PDF for each row in Google Sheets", "view_count": 900, "is_answered": False},
    {"title": "Create GST invoice numbers automatically in Excel", "view_count": 300, "is_answered": True},
    {"title": "Invoice template VBA copy totals", "view_count": 120, "is_answered": False},
    {"title": "Merge multiple workbooks into one sheet", "view_count": 50, "is_answered": True}])
check("demand counts questions, views, unanswered per group",
      so["gst_invoice"]["so_questions"] == 3 and so["gst_invoice"]["so_views"] == 1320 and so["gst_invoice"]["so_unanswered"] == 2, so)

# GitHub search item shape
now = datetime(2026, 11, 10, tzinfo=timezone.utc)
repos = [
    {"full_name": "a/gst-invoice-lib", "stargazers_count": 400, "pushed_at": "2026-09-01T00:00:00Z",
     "license": {"spdx_id": "MIT"}, "description": "Python library for GST invoices", "topics": [], "html_url": "u1"},
    {"full_name": "b/old-invoice", "stargazers_count": 900, "pushed_at": "2021-01-01T00:00:00Z",
     "license": {"spdx_id": "GPL-3.0"}, "description": "Invoice web app", "topics": [], "html_url": "u2"},
    {"full_name": "c/nolicence", "stargazers_count": 50, "pushed_at": "2026-10-01T00:00:00Z",
     "license": None, "description": "scripts", "topics": [], "html_url": "u3"}]
sp = A.gh_supply(57, repos, now=now)
check("maintained = updated ≤12 months AND ≥20 stars; stale 900★ repo doesn't count", sp["gh_maintained"] == 2, sp)
check("'easy to use' only for app/add-on/web-app style projects", sp["gh_easy"] == 0, sp)
check("licences recorded; only permissive marked reusable",
      [(t["repo"], t["reusable"]) for t in sp["gh_top"]] == [("a/gst-invoice-lib", True), ("b/old-invoice", False), ("c/nolicence", False)])

k1, s1 = A.gap(so["gst_invoice"], sp)
k2, _ = A.gap(so["gst_invoice"], {"gh_maintained": 0, "gh_easy": 0})
k3, s3 = A.gap(so["gst_invoice"], {"gh_maintained": 6, "gh_easy": 5})
check("gap types: PACKAGE (dev-only code exists) · BUILD (nothing maintained) · CROWDED (many easy tools, low score)",
      (k1, k2, k3) == ("PACKAGE", "BUILD", "CROWDED") and s3 < s1, (k1, k2, k3, s1, s3))

gt = A.trends_demand([("gst invoice generator free", 450), ("gst invoice format excel", 900)])
rows = A.week_rows(so, gt, {"gst_invoice": sp}, "2026-11-09")
r0 = rows[0]
check("row: ranked, both sources, reusable vs do-not-reuse split",
      r0["bucket"] == "gst_invoice" and r0["sources"] == "stackoverflow,gtrends"
      and "a/gst-invoice-lib (mit" in r0["reusable_code"] and "c/nolicence (none)" in r0["do_not_reuse"], r0)
check("week 1 → watch (needs 3 weeks)", r0["status"] == "watch" and r0["weeks_in_top"] == 1)
hist = [{"week": "2026-10-26", "bucket": "gst_invoice", "in_top": "True"},
        {"week": "2026-11-02", "bucket": "gst_invoice", "in_top": "True"}]
r3 = A.week_rows(so, gt, {"gst_invoice": sp}, "2026-11-09", hist)[0]
check("3rd week in top 5 with both sources → SHORTLIST", r3["status"].startswith("SHORTLIST") and r3["weeks_in_top"] == 3, r3)
r_one = A.week_rows(so, {}, {"gst_invoice": sp}, "2026-11-09", hist)[0]
check("one source only → never shortlisted", r_one["status"] == "watch")

wv = A.waitlist_verdict([{"bucket": "(example — use a bucket key)", "signups": "99"},
                         {"bucket": "gst_invoice", "signups": "34", "sellers_yes": "1"},
                         {"bucket": "reconcile", "signups": "5", "sellers_yes": "2"}])
check("waitlist gate: 30+ sign-ups OR 3+ sellers yes; example row ignored",
      [(b, ok) for b, ok, _ in wv] == [("gst_invoice", True), ("reconcile", False)], wv)

# ── v1.36 open discovery
check("need extracted from any phrasing",
      A.need_tokens("Ask HN: Is there a tool that converts Figma designs to Flutter?") == ["convert", "figma", "design", "flutter"]
      and A.need_tokens("Feature request: export dashboards to PDF") == ["export", "dashboard", "pdf"])
items = [{"source": "hn", "text": "Ask HN: Is there a tool that converts Figma designs to Flutter code?", "weight": 120, "url": "h1"},
         {"source": "github_issue", "text": "Feature request: convert Figma design to Flutter widgets", "weight": 85, "url": "g1"},
         {"source": "stackoverflow", "text": "How to export Notion database to Google Sheets automatically", "weight": 3000, "url": "s1"},
         {"source": "gtrends", "text": "notion to google sheets", "weight": 400},
         {"source": "hn", "text": "Ask HN: what are you working on?", "weight": 50}]
G = A.cluster_needs(items)
cores = {tuple(sorted(g["core"])): sorted({it["source"] for it in g["items"]}) for g in G}
check("same need from different sources grouped; vague asks dropped",
      cores.get(("convert", "design", "figma", "flutter")) == ["github_issue", "hn"]
      and any(set(k) >= {"notion", "google"} and v == ["gtrends", "stackoverflow"] for k, v in cores.items())
      and len(G) == 2, cores)
sup = {" ".join(sorted(g["core"][:3])): {"gh_total": 3, "gh_maintained": 0, "gh_easy": 0, "gh_top": []} for g in G}
R = A.open_rows(G, sup, "2026-11-09")
check("open rows: both needs BUILD, 2 sources each, examples + links kept",
      all(r["gap"] == "BUILD" and "," in r["sources"] and r["links"] for r in R), R)
hist = [{"week": w, "bucket": R[0]["bucket"], "in_top": "True"} for w in ("2026-10-26", "2026-11-02")]
check("3 weeks in top 10 + 2 sources → SHORTLIST", A.open_rows(G, sup, "2026-11-09", hist)[0]["status"].startswith("SHORTLIST"))
check("small-business needs still tagged", A.open_rows(A.cluster_needs([
      {"source": "hn", "text": "Is there a tool to generate GST invoice from Shopify orders", "weight": 30},
      {"source": "stackoverflow", "text": "Generate GST invoice from Shopify orders automatically", "weight": 500}]), {}, "2026-11-09")[0]["smb_group"]
      == "ecommerce_ops,gst_invoice" and "gst_invoice" in "ecommerce_ops,gst_invoice")

# ── v1.37 stronger judgement
gst = A.cluster_needs([{"source": "gtrends", "text": "gst invoice generator free", "weight": 900},
                       {"source": "stackoverflow", "text": "Generate GST invoice from Excel automatically", "weight": 400}])
k = " ".join(sorted(gst[0]["core"][:3]))
tiny = {k: {"gh_total": 12, "gh_maintained": 0, "gh_easy": 0, "gh_top": [
        {"repo": "s/inv", "stars": 3, "updated": "2026-10-01", "licence": "mit", "reusable": True, "url": "u"}]}}
r = A.open_rows_v2(gst, tiny, {}, "2026-11-09")[0]
check("10 Nov case: GST invoices is NOT 'BUILD' — known apps (Zoho, Vyapar…) count as competition",
      r["gap"] == "CROWDED" and "Vyapar" in r["commercial"], (r["gap"], r["commercial"]))
check("★3 practice repos are never offered as reusable", r["reusable_code"] == "")
wa = A.cluster_needs([{"source": "hn", "text": "Is there a tool to auto reply WhatsApp messages for my shop", "weight": 40},
                      {"source": "gtrends", "text": "whatsapp auto reply shop", "weight": 600}])
check("platform-dependent needs → HARD", A.open_rows_v2(wa, {}, {}, "2026-11-09")[0]["gap"] == "HARD")
dv = A.cluster_needs([{"source": "hn", "text": "Is there a CLI to convert JSON schema to TypeScript types", "weight": 80},
                      {"source": "github_issue", "text": "Feature request: convert JSON schema to TypeScript types", "weight": 60}])
kd = " ".join(sorted(dv[0]["core"][:3]))
rd = A.open_rows_v2(dv, {}, {kd: {"show_hn": 0, "npm": 40}}, "2026-11-09")[0]
check("developer need: audience Developer, effort S, 40 npm packages → not BUILD",
      rd["audience"] == "Developer" and rd["effort"] == "S" and rd["gap"] == "PACKAGE", (rd["audience"], rd["effort"], rd["gap"]))
sel = A.cluster_needs([{"source": "hn", "text": "Is there a tool to track Amazon seller competitor prices daily", "weight": 50},
                       {"source": "gtrends", "text": "amazon seller competitor price tracker", "weight": 300}])
rs = A.open_rows_v2(sel, {}, {}, "2026-11-09")[0]
check("seller need marked 📊 data edge and scored higher than the same need without it",
      rs["data_edge"] and rs["score"] > 0)
check("focus=developer hides business needs", A.open_rows_v2(sel, {}, {}, "2026-11-09", focus="developer") == []
      or all(x["audience"] != "Business" for x in A.open_rows_v2(sel, {}, {}, "2026-11-09", focus="developer")))
check("streak survives re-wording (similar key from last week counts)",
      A.match_history({"convert", "json", "schema"}, ["convert json schema typescript", "pdf merge"]) == "convert json schema typescript")
wl = A.waitlist_verdict_v2([{"bucket": "a", "shown": "25", "positive": "9"}, {"bucket": "b", "shown": "10", "positive": "8"},
                            {"bucket": "c", "signups": "31"}, {"bucket": "(example — x)", "shown": "99", "positive": "99"}])
check("gate = rate among people shown directly (9/25 = 36% ✅, 8/10 too few shown ⏳), or 30+ organic",
      [(b, ok) for b, ok, _ in wl] == [("a", True), ("b", False), ("c", True)], wl)

# ── v1.38 — the critique's cases
g1 = A.cluster_needs([{"source": "gtrends", "text": f"gst invoice generator {w}", "weight": 500}
                      for w in ("free", "online", "excel", "pdf", "format", "app", "india", "word", "download", "maker")])
k1 = " ".join(sorted(g1[0]["core"][:3]))
r1 = A.finalize_v38(A.open_rows_v2(g1, {}, {}, "2026-11-09"), {}, {})[0]
check("score 80 from 10 searches of ONE source → confidence Low, ⚠️ regulated, CROWDED (known apps incl. ClearTax)",
      r1["confidence"] == "Low" and r1["regulated"] and r1["gap"] == "CROWDED" and "ClearTax" in r1["commercial"]
      and "low evidence" in r1["score_parts"], (r1["confidence"], r1["regulated"], r1["gap"], r1["score_parts"]))
g2 = A.cluster_needs([{"source": "stackoverflow", "text": "Convert markdown tables to Notion database blocks", "weight": 900}])
r2 = A.finalize_v38(A.open_rows_v2(g2, {}, {}, "2026-11-09"), {}, {})[0]
check("a single question can never be BUILD → UNPROVEN", r2["gap"] == "UNPROVEN" and r2["confidence"] == "Low", (r2["gap"], r2["confidence"]))
wc = A.web_crowding([{"url": "https://www.reddit.com/r/x/tool", "title": "Best tool?"},
                     {"url": "https://pdfmerge.example/tool", "title": "Free PDF merge tool online"},
                     {"url": "https://www.mergepdf.example/", "title": "Merge PDF — free online"},
                     {"url": "https://ilovepdf.example/merge", "title": "Merge PDF files online"},
                     {"url": "https://github.com/a/b", "title": "merge tool"}])
check("web crowding counts dedicated tool sites, not forums/GitHub", wc["web_tool_pages"] == 3, wc)
g3 = A.cluster_needs([{"source": "hn", "text": "Is there a tool to merge PDF files offline", "weight": 30},
                      {"source": "stackoverflow", "text": "Merge PDF files offline batch", "weight": 800},
                      {"source": "gtrends", "text": "merge pdf offline tool", "weight": 300},
                      {"source": "hn", "text": "Looking for an offline PDF merge tool", "weight": 20}])
k3 = " ".join(sorted(g3[0]["core"][:3]))
r3 = A.finalize_v38(A.open_rows_v2(g3, {}, {}, "2026-11-09"), {}, {k3: wc})[0]
check("BUILD on GitHub but 3 tool sites on page 1 → CROWDED", r3["gap"] == "CROWDED" and r3["web_tool_pages"] == 3, (r3["gap"], r3["web_tool_pages"]))
r4 = A.finalize_v38(A.open_rows_v2(g3, {}, {}, "2026-11-09"), {}, {}, focus_words=["llm", "token", "openai"])[0]
r4b = A.finalize_v38(A.open_rows_v2(g3, {}, {}, "2026-11-09"), {}, {})[0]
check("v1.46: off-focus need is NOT parked — it keeps its place at ×0.8 (focus is a boost, not a filter)",
      r4["status"] != "PARKED (off focus)" and not r4["fits_focus"] and abs(float(r4["score"]) - round(float(r4b["score"]) * 0.8, 1)) < 0.15
      and "×0.8 outside focus words" in r4["score_parts"], (r4["status"], r4["score"], r4b["score"]))
g5 = A.cluster_needs([{"source": "hn", "text": "Is there a tool to track LLM token cost per request", "weight": 90},
                      {"source": "github_issue", "text": "Feature request: track LLM token cost per request", "weight": 70},
                      {"source": "stackoverflow", "text": "How to track LLM token cost per API request", "weight": 2000},
                      {"source": "gtrends", "text": "llm token cost tracker", "weight": 400}])
r5 = A.finalize_v38(A.open_rows_v2(g5, {}, {}, "2026-11-09"), {}, {}, focus_words=["llm", "token"])[0]
check("in-focus need with 4 asks from 4 sources → fits focus, confidence Medium+",
      r5["fits_focus"] and r5["confidence"] in ("Medium", "High"), (r5["fits_focus"], r5["confidence"]))
sp2 = A.gh_supply(5, [{"full_name": "z/old", "stargazers_count": 300, "pushed_at": "2023-01-01T00:00:00Z", "license": {"spdx_id": "MIT"},
                       "description": "", "topics": [], "html_url": "u"}], now=now)
# v1.46: a repo only counts as supply when it is about the need (live report: "desktop" matched Teamwork/clippy.desktop)
rep = [{"full_name": "Teamwork/clippy.desktop", "stargazers_count": 85, "pushed_at": "2016-07-18T00:00:00Z", "license": {"spdx_id": "MIT"},
        "description": "Clippy for desktop", "topics": [], "html_url": "u1"},
       {"full_name": "ryoppippi/ccusage", "stargazers_count": 9000, "pushed_at": "2026-11-01T00:00:00Z", "license": {"spdx_id": "MIT"},
        "description": "CLI tool for analyzing Claude Code usage statistics", "topics": ["usage", "cli"], "html_url": "u2"}]
sp3 = A.gh_supply(40, rep, now=now, words=["desktop", "usage", "panel"])
check("v1.46: keyword-collision repo dropped from supply; the real usage tool stays", [t["repo"] for t in sp3["gh_top"]] == ["ryoppippi/ccusage"], sp3["gh_top"])
check("no words given → old behaviour (all repos)", len(A.gh_supply(40, rep, now=now)["gh_top"]) == 2)
check("repo quality note + last commit date", sp2["gh_top"][0]["note"].startswith("stale") and sp2["gh_top"][0]["updated"] == "2023-01-01")

# ── v1.39 — only real tool requests are demand (from the 10 Nov live run)
import trend_fetcher as tf
asks = [("hn", "Show HN: Point your camera at a landscape to learn about its geology", False),
        ("hn", "Ask HN: What in the world is taking Ubuntu so long?", False),
        ("stackoverflow", "Try/except failing to catch exceptions", False),
        ("stackoverflow", "Is there a library to convert JSON schema to TypeScript types?", True),
        ("hn", "Ask HN: Is there a tool that tracks LLM API cost per user?", True),
        ("hn", "Ask HN: Looking for an alternative to Zapier for self-hosting", True),
        ("softwarerecs", "Offline PDF merger for Linux", True),
        ("github_issue", "Feature request: export to CSV", True)]
bad = [t for src, t, want in asks if A.is_tool_ask({"source": src, "text": t}) != want]
check("launches, rants and debugging questions dropped; real tool requests kept", not bad, bad)
check("readable need names", A.display_name({"items": [{"text": "Ask HN: Is there a tool that tracks LLM API cost per user?", "weight": 5}]})
      == "tracks LLM API cost per user")
long = "\n".join(f'line {i} <a href="https://example.com/{i}">[{i}]</a>' for i in range(400))
parts = tf.split_message(long)
check("long Telegram messages split at line breaks — no link cut in half",
      len(parts) > 1 and all(p.count("<a ") == p.count("</a>") for p in parts) and all(len(p) <= 3900 for p in parts))

# ── v1.40 — votes count, 8-week memory, focus searches
ev = A.evidence([{"source": "github_issue", "text": "x", "weight": 300}])
check("one issue with 300 👍 counts as ~11 asks and is a strong single signal",
      ev["eff_asks"] == 11.0 and ev["strong_single"] and ev["votes"] == 300, ev)
check("strong single signal → Medium confidence on its own", A.confidence_v2(11, 1, True) == "Medium")
check("weak single ask stays Low", A.confidence_v2(1.2, 1, False) == "Low")
check("4+ weighted asks from 2 sources → Medium; 8+ from 3 → High",
      A.confidence_v2(4.5, 2) == "Medium" and A.confidence_v2(9, 3) == "High")
w = A.merge_window([{"source": "hn", "text": "new ask", "url": "u3", "weight": 5}],
                   [{"source": "hn", "text": "old ask", "url": "u1", "weight": 4, "seen": "2026-10-20"},
                    {"source": "hn", "text": "too old", "url": "u2", "weight": 4, "seen": "2026-08-01"},
                    {"source": "hn", "text": "new ask", "url": "u3", "weight": 1, "seen": "2026-11-02"}], "2026-11-10")
check("8-week memory keeps recent asks, drops old ones, de-duplicates by link (newest kept)",
      sorted(x["text"] for x in w) == ["new ask", "old ask"] and [x for x in w if x["url"] == "u3"][0]["weight"] == 5, w)
check("focus searches built from focus words", A.focus_queries(["llm", "api cost"]) == ["llm tool", "api cost"])
grp = A.cluster_needs([{"source": "github_issue", "text": "Feature request: show token cost per request in the dashboard", "weight": 120}])
rr = A.finalize_v38(A.open_rows_v2(grp, {}, {}, "2026-11-09"), {}, {}, focus_words=["token", "llm"])[0]
check("a single 120-👍 in-focus feature request now counts as Medium evidence (not discarded)",
      rr["confidence"] == "Medium" and rr["fits_focus"], (rr["confidence"], rr.get("eff_asks")))

# ── v1.41 — vendor-only (from the 10 Nov v1.40 run)
vg = lambda *its: {"items": list(its)}
gi = lambda t, u, w=50: {"source": "github_issue", "text": t, "url": u, "weight": w}
cases = [(vg(gi("[FEATURE] Bring back the usage stats panel on the Desktop new session screen",
               "https://github.com/anthropics/claude-code/issues/102264")), True),
         (vg(gi("Mods - make Claude 10x more extensible", "https://github.com/anthropics/claude-code/issues/91870")), True),
         (vg(gi("Support custom LLM proxy / base URL for context", "https://github.com/anthropics/claude-code/issues/1")), False),
         (vg(gi("Add dark mode toggle", "https://github.com/someone/smalltool/issues/2")), False),
         (vg(gi("Bring back usage panel", "https://github.com/anthropics/x/issues/3"),
             {"source": "hn", "text": "Is there a tool that tracks Claude usage per session?", "url": "h", "weight": 30}), False)]
got = [A.vendor_only(g) for g, _ in cases]
check("vendor-only: in-app asks on vendor repos ✓; proxy/API angle, small repos, outside asks ✗",
      got == [w for _, w in cases], got)
check("LLM / AI requests are Developer audience, not General",
      A.judge("Support custom LLM proxy / base URL for claude", [])["audience"] == "Developer")

# offline end-to-end run (all four sources simulated)
import trend_fetcher as tf
TMP = tempfile.mkdtemp(); tf.LOCAL_OUT_DIR = TMP; tf.TODAY = "2026-11-10"
tf.auto_gh_issues = lambda days=90: [items[1]]
tf.auto_hn = lambda days=30: [items[0], items[4]]
tf.auto_stackoverflow = lambda days=7: [items[2]]
tf.auto_trends = lambda today: [items[3], {"source": "gtrends", "text": "rummy app download", "weight": 900}]
calls = []
tf.auto_github = lambda q: (calls.append(q), {k: {"gh_total": 2, "gh_maintained": 0, "gh_easy": 0, "gh_top": [
    {"repo": "x/y", "stars": 40, "updated": "2026-10-01", "licence": "mit", "reusable": True, "url": "u"}]} for k in q})[1]
tf.auto_competition = lambda q, a: {k: {"show_hn": 0} for k in q}
tf.auto_focus = lambda fw: []
sent = []; tf.send_telegram = sent.append
tf.main_automation()
op = pd.read_csv(os.path.join(TMP, "OPPORTUNITIES.csv"))
check("OPPORTUNITIES: discovered needs for the week", set(op["week"]) == {"2026-11-09"} and len(op) == 2, op[["bucket", "sources"]])
check("banned topics never reach the report (rummy)", not op["examples"].astype(str).str.contains("rummy").any())
check("GitHub supply checked per need", calls and len(calls[0]) == 2)
check("ASKS memory tab written", os.path.exists(os.path.join(TMP, "ASKS.csv")))
check("admin message v1.48: real-request count, 8-week memory, evidence verdict, focus warning, licence rule",
      sent and "v1.48" in sent[0] and "8-week memory" in sent[0]
      and ("enough evidence" in sent[0]) and "no focus words set" in sent[0] and "do NOT reuse" in sent[0], sent[:1])
check("WAITLIST tab created", os.path.exists(os.path.join(TMP, "WAITLIST.csv")))
tf.main_automation()
check("re-run in the same week replaces it (no duplicates)", len(pd.read_csv(os.path.join(TMP, "OPPORTUNITIES.csv"))) == len(op))
shutil.rmtree(TMP)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
