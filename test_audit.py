"""v1.45 audit fixes: storage safety, fair forward test, churn filter, lead-test honesty, alerts, festivals."""
import os, sys, shutil, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf, picks as P, discovery as D, compliance, season

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

# ── 1. storage: a failed write must never empty a tab ────────────────
import gspread
class WS:
    def __init__(s, rows): s.rows, s.calls, s.fail = [list(r) for r in rows], [], 0
    row_count = property(lambda s: max(len(s.rows), 1)); col_count = property(lambda s: max((len(r) for r in s.rows), default=1))
    def get_all_values(s): return [list(r) for r in s.rows]
    def col_values(s, i): return [r[0] for r in s.rows]
    def resize(s, rows=None, cols=None): s.calls.append("resize")
    def clear(s): s.calls.append("clear"); s.rows = []
    def batch_clear(s, rng): s.calls.append("trim")
    def update(s, range_name=None, values=None, value_input_option=None):
        s.calls.append("update")
        if s.fail > 0:
            s.fail -= 1; raise RuntimeError("503 backend error")
        s.rows = [list(r) for r in values]
class WB:
    def __init__(s, tabs): s.tabs = tabs
    def worksheet(s, t):
        if t not in s.tabs: raise gspread.WorksheetNotFound(t)
        return s.tabs[t]
    def add_worksheet(s, title, rows, cols): s.tabs[title] = WS([]); return s.tabs[title]
tf.time.sleep = lambda x: None
sent = []; tf.send_telegram = lambda m: sent.append(m)
hist_rows = [["date", "asin"]] + [[f"2026-11-{d:02d}", f"A{i}"] for d in range(1, 11) for i in range(20)]
st = tf.Store.__new__(tf.Store); st.wb = WB({"HISTORY": WS(hist_rows)}); st._rows_seen = {}; st.local_dir = "x"
ws = st.wb.tabs["HISTORY"]
ws.fail = 99
try:
    st.write("HISTORY", pd.DataFrame(hist_rows[1:], columns=hist_rows[0]))
    crashed = False
except Exception:
    crashed = True
check("write keeps failing → run stops, tab NOT emptied (no clear-before-write)",
      crashed and len(ws.rows) == 201 and "clear" not in ws.calls, (crashed, len(ws.rows), ws.calls[:3]))
ws.fail, ws.calls = 2, []
n = st.write("HISTORY", pd.DataFrame(hist_rows[1:], columns=hist_rows[0]))
check("transient failures are retried, then succeed", n == 200 and ws.calls.count("update") == 3, ws.calls)
ws.calls = []
n = st.write("HISTORY", pd.DataFrame(hist_rows[1:21], columns=hist_rows[0]))
check("shrink protection: 200 rows → 20 rows refused, nothing written, admin alerted",
      n == 0 and len(ws.rows) == 201 and "update" not in ws.calls and any("Refused to overwrite HISTORY" in m for m in sent), (n, len(ws.rows)))
n = st.write("HISTORY", pd.DataFrame(hist_rows[21:], columns=hist_rows[0]))
check("normal trimming (200 → 180 rows) still allowed", n == 180 and len(ws.rows) == 181)
st.wb.tabs["TREND_SIGNALS"] = WS(hist_rows)
check("unprotected tabs (rewritten daily) may shrink freely",
      st.write("TREND_SIGNALS", pd.DataFrame(hist_rows[1:6], columns=hist_rows[0])) == 5)

# ── 2. picks: churn filter + fair baselines + day-level verdict ──────
sig = pd.DataFrame([
    {"asin": "OLD", "title": "Steady climber bottle", "category_slug": "kitchen", "trend_score": 50, "rank_change": 20,
     "price_inr": "199", "days_on_list_14d": 6, "bs_rank_today": 60, "seasonal": "", "compliance": ""},
    {"asin": "BLIP", "title": "One day tail entrant", "category_slug": "kitchen", "trend_score": 70, "rank_change": 12,
     "price_inr": "199", "days_on_list_14d": 1, "bs_rank_today": 89, "seasonal": "", "compliance": ""},
    {"asin": "BIG", "title": "New entrant straight to top", "category_slug": "sports", "trend_score": 65, "rank_change": 90,
     "price_inr": "499", "days_on_list_14d": 1, "bs_rank_today": 11, "seasonal": "", "compliance": ""}])
ch = P.select_picks(sig, n=8, today="2026-11-20")
check("one-day tail entrant dropped; steady climber and a top-30 debut kept", list(ch["asin"]) == ["BIG", "OLD"], list(ch["asin"]))
H = pd.DataFrame([{"date": "2026-11-10", "list": "bestsellers", "category_slug": "kitchen", "asin": f"K{i}", "rank": i,
                   "price_inr": "" if i % 2 else "100", "title": "t"} for i in range(1, 41)])
rows = P.make_rows(pd.DataFrame([{"asin": "K2", "title": "x", "category_slug": "kitchen", "trend_score": 1}]), H, "2026-11-10")
base = rows[rows["role"] == "baseline"]["asin"].tolist()
check("baselines are in-stock products only (same rule as picks)", base and all(int(a[1:]) % 2 == 0 for a in base), base)
trk = []
for d in range(1, 13):
    day = f"2026-10-{d:02d}"
    for i in range(4):
        trk.append({"pick_date": day, "role": "pick", "hit_d14": 1 if d <= 9 else 0, "alive_d14": 1})
        trk.append({"pick_date": day, "role": "baseline", "hit_d14": 0 if d <= 9 else 1, "alive_d14": 0})
T = pd.DataFrame(trk)
check("days, not picks, are counted: picks won 9 of 12 days", P.daily_wins(T, 14, "hit") == (9, 12))
v = P.verdict(P.scorecard(T), tracker=T)
check("verdict reports days won and the survival measure", "9 of 12 days" in v and "still in the top 100" in v, v)
T5 = T[T["pick_date"] <= "2026-10-05"]
v5 = P.verdict(P.scorecard(T5), tracker=T5)
check("48 resolved picks from only 5 days → no verdict yet (needs 10 different days)", "needs 10 different days" in v5, v5)
tr = P.update_tracking(pd.DataFrame([{"pick_date": "2026-11-10", "role": "pick", "asin": "K2", "category_slug": "kitchen", "rank_at_pick": 2}]),
                       pd.concat([H, H.assign(date="2026-11-17").query("asin != 'K2'")]))
check("survival column filled (dropped out by day 7 → alive 0)", list(tr["alive_d7"]) == [0], tr.filter(like="d7").to_dict("records"))

# ── 3. discovery: lead test can't be passed by chance ────────────────
bs = [{"date": f"2026-11-{d:02d}", "asin": "X", "title": "Milton Hot Lunch Box Steel"} for d in range(1, 30)]
bs += [{"date": "2026-11-20", "asin": "N", "title": "Portable Neck Fan Rechargeable"}]
disc = [{"date": "2026-11-12", "item_key": "q:hot lunch box", "kind": "phrase", "name": "hot lunch box", "product_id": "", "sources": "gtrends"},
        {"date": "2026-11-12", "item_key": "q:toys", "kind": "phrase", "name": "toys", "product_id": "", "sources": "gtrends"},
        {"date": "2026-11-12", "item_key": "q:neck fan", "kind": "phrase", "name": "neck fan", "product_id": "", "sources": "gtrends"},
        {"date": "2026-11-12", "item_key": "q:sand timer", "kind": "phrase", "name": "sand timer", "product_id": "", "sources": "gtrends"}]
lt = D.lead_test(disc, bs)
check("lead test: already-listed product and one-word phrase excluded; only true leads count (1 of 2)",
      lt["eligible"] == 2 and lt["appeared"] == 1 and lt["median_lead_days"] == 8, lt)
c = D.combine([{"date": "2026-11-12", "phrase": "toys", "seed": "toys", "rise": 900, "lane": "products"},
               {"date": "2026-11-12", "phrase": "desk lamp", "seed": "table lamp", "rise": 900, "lane": "products"}],
              [], ["Soft Toys for Kids", "LED Desk Lamp Rechargeable"], [], "2026-11-12",
              yt_phrase={("products", "desk lamp"): (4, 5000)})
by = {r["name"]: r for r in c}
check("one-word search can't claim Amazon agreement", by["toys"]["sources"] == "gtrends", by["toys"]["sources"])
check("product phrase confirmed by YouTube reviews from 4 channels → youtube source",
      "youtube" in by["desk lamp"]["sources"] and by["desk lamp"]["status"] == "2+ sources", by["desk lamp"])

# ── 4. festivals ─────────────────────────────────────────────────────
check("kites flagged for Makar Sankranti; 'kitchen' is not",
      "Sankranti" in compliance.seasonal_flag("Paper Kite Patang pack of 20") and compliance.seasonal_flag("Kitchen rack") == "")
check("kite promoted in early Jan, not in March",
      season.window("SEASONAL: Makar Sankranti/Lohri/Pongal", "2027-01-05")["affiliate_ok"]
      and not season.window("SEASONAL: Makar Sankranti/Lohri/Pongal", "2027-03-01")["affiliate_ok"])
check("school bags are not blocked as seasonal", compliance.seasonal_flag("School bag for kids") == "")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
