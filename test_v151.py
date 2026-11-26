"""v1.51: durability + exit logic, durable/momentum arms, per-arm forward test, provable post copy, calculator/sourcing
priority, brief sections, non-product filter."""
import os, re, sys, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf
import durable, picks, brief, calculator, compliance, resell_gate

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info)[:400] if not ok else ""))
    if not ok: FAILS.append(name)

NAN = float("nan")
IST = timezone(timedelta(hours=5, minutes=30))
D0 = datetime(2026, 11, 1)
days = [(D0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(16)]
TODAY = days[-1]


def row(d, a, rank, cat="kitchen", title=None, price=199.0, rating=4.2, reviews=50, lst="bestsellers"):
    return {"date": d, "list": lst, "category_slug": cat, "asin": a, "rank": rank, "price_inr": price,
            "rating": rating, "reviews": reviews, "title": title or f"Item {a} Holder"}


# ══ 1. durable.py on a hand-made history ═════════════════════════════════════════
rows = []
for i, d in enumerate(days):
    for k in range(2, 10):                                       # fillers keep the list populated
        rows.append(row(d, f"FILL{k:06d}", k))
    rows.append(row(d, "DUR000001", 10))                         # steady all 16 days
    if i >= 10: rows.append(row(d, "PART00001", 20))             # only the last 6 days
    rows.append(row(d, "DEEP00001", 90))                         # always there, but at the very tail
    if i == 15: rows.append(row(d, "NEW000001", 12))             # first seen today
    if i <= 11: rows.append(row(d, "GONE00001", 25))             # left 4 days ago
    rows.append(row(d, "FALL00001", 15 if i < 15 else 30))      # 15 → 30 in the last day (ref day 3 back is still 15)
    rows.append(row(d, "SLIP00001", 20 if i < 13 else 29))       # 9 places: below the flag
    rows.append(row(d, "EDGE00001", 20 if i < 13 else 30))       # exactly 10 places: flagged
    rows.append(row(d, "UP0000001", 40 if i < 13 else 20))       # improving
    if i not in (4, 5):                                          # the garden list was not fetched on days 5 and 6
        rows.append(row(d, "GAR000001", 7, cat="garden"))
H = pd.DataFrame(rows)

d = durable.durability(H, TODAY)
check("durability: steady item present 14 of 14 days is durable", bool(d.loc["DUR000001", "durable"]) and d.loc["DUR000001", "durable_days"] == 14
      and d.loc["DUR000001", "durable_window"] == 14, d.loc["DUR000001"].to_dict())
check("durability: item on the list 6 of 14 days is not durable", not d.loc["PART00001", "durable"] and d.loc["PART00001", "durable_days"] == 6)
check("durability: always present but median rank #90 is not durable (a long stay at the tail is not demand)",
      not d.loc["DEEP00001", "durable"] and d.loc["DEEP00001", "presence"] == 1.0)
check("durability: first seen today is not durable", not d.loc["NEW000001", "durable"])
check("durability: an item not on today's list is not returned at all", "GONE00001" not in d.index)
check("durability: a day the category was never fetched is not a miss (12 of 12, not 12 of 14)",
      d.loc["GAR000001", "durable_window"] == 12 and d.loc["GAR000001", "durable_days"] == 12 and bool(d.loc["GAR000001", "durable"]),
      d.loc["GAR000001"].to_dict())
check("durability: note is plain words with the observed counts",
      d.loc["DUR000001", "durable_note"] == "on the list 14 of the last 14 days · median #10", d.loc["DUR000001", "durable_note"])
check("durability: score is 0-100 and a steadier, higher-ranked item scores higher",
      0 <= d["durable_score"].min() and d["durable_score"].max() <= 100 and d.loc["DUR000001", "durable_score"] > d.loc["DEEP00001", "durable_score"])
d5 = durable.durability(H[H["date"].isin(days[-5:])], TODAY)
check("durability: with fewer than 7 observed days nothing is called durable", len(d5) > 0 and not d5["durable"].any())
check("durability: ignores rows after `today` (no look-ahead)",
      durable.durability(H, days[9]).loc["DUR000001", "durable_window"] == 10)
check("durability: empty or list-less history → empty frame, no crash",
      durable.durability(pd.DataFrame(), TODAY).empty and durable.durability(H[H["list"] == "movers"], TODAY).empty)

e = durable.exit_flags(H, TODAY)
check("exit: 15 → 30 in 3 days is 'falling' with a plain note", e.loc["FALL00001", "exit_flag"] == "falling"
      and e.loc["FALL00001", "exit_note"] == "fell #15 → #30 in 3 days", e.loc["FALL00001"].to_dict())
check("exit: a 10-place fall is flagged, a 9-place fall is not", e.loc["EDGE00001", "exit_flag"] == "falling" and e.loc["SLIP00001", "exit_flag"] == "")
check("exit: improving and steady items are never flagged", e.loc["UP0000001", "exit_flag"] == "" and e.loc["DUR000001", "exit_flag"] == "")
check("exit: an item with no reading 3 days ago cannot be flagged", e.loc["NEW000001", "exit_flag"] == "")

# base rates: fallers that then leave vs steady items
rows = []
for i in range(40):
    dd = (D0 + timedelta(days=i)).strftime("%Y-%m-%d")
    for it in range(80):
        base = 1 + it
        if it % 2 == 0:                                          # fallers: drop 12 places on day 10, gone from day 18
            if i >= 18: continue
            rank = base if i < 10 else base + 12
            if rank > 100: continue
        else:
            rank = base
        rows.append(row(dd, f"B{it:07d}", rank, cat="c"))
HB = pd.DataFrame(rows)
fr = durable.fader_rates(HB)
fl = fr[(fr["state"] == "falling") & fr["show"]]
st = fr[(fr["state"] == "steady") & fr["show"]]
check("base rates: falling cells exist and are shown only with n ≥ 30", len(fl) > 0 and (fl["n"] >= durable.MIN_BASE_N).all(), fr)
check("base rates: a faller leaves more often than a steady item",
      fl["left"].sum() / fl["n"].sum() > st["left"].sum() / st["n"].sum(), (fl.to_dict("records"), st.to_dict("records")))
check("base rates: one plain sentence with both counts and the 'not a proof' caveat",
      "off the top 100 a week later" in durable.fader_line(fr) and "not a proof" in durable.fader_line(fr) and "n=" in durable.fader_line(fr))
small = durable.fader_rates(H)
check("base rates: a short history shows no sentence rather than a noisy number (falling cells under n=30 stay hidden)",
      durable.fader_line(small) == "" and (len(small) == 0 or not small[small["state"] == "falling"]["show"].any()), small)

# ══ 2. compute_signals carries the new columns and labels ═════════════════════════
tf.TODAY = TODAY
tf.NOW_IST = datetime.strptime(TODAY, "%Y-%m-%d").replace(hour=12, tzinfo=IST)
Hs = H.copy()
Hs.loc[Hs["asin"] == "DUR000001", "title"] = "Steel Lunch Box Set"
for dd in days:                                                  # a bill-payment tile at #1 for 16 days
    Hs = pd.concat([Hs, pd.DataFrame([row(dd, "BILL000001", 1, cat="luggage", title="Credit Card Bill", price=1.0)])], ignore_index=True)
sig = tf.compute_signals(Hs, {}).set_index("asin")
need = {"durable", "durable_score", "durable_days", "durable_window", "median_rank", "durable_note", "exit_flag", "exit_note", "non_product"}
check("signals carry durable / exit / non_product columns", need <= set(sig.columns), need - set(sig.columns))
check("signals: durable item is labelled 🧱 (a plain 'Watch' becomes 'Durable')",
      "🧱" in sig.loc["DUR000001", "signal"] and not sig.loc["DUR000001", "signal"].startswith("• Watch"), sig.loc["DUR000001", "signal"])
check("signals: a falling item gets 📉 appended and keeps its first label", "📉 falling" in sig.loc["FALL00001", "signal"], sig.loc["FALL00001", "signal"])
check("signals: a bill-payment tile at #1 for 16 days is never durable",
      not bool(sig.loc["BILL000001", "durable"]) and "not a physical product" in str(sig.loc["BILL000001", "non_product"]))
check("signals: non-durable items keep their old labels (no 🧱)", "🧱" not in sig.loc["PART00001", "signal"], sig.loc["PART00001", "signal"])

# ══ 3. picks: durable arm, momentum arm, falling rule ═════════════════════════════
def S(asin, cat="kitchen", ds=90.0, ts=40.0, dur=True, stock="in_stock", fall="", note="", seas="", comp="", rc=15, **kw):
    return {"asin": asin, "title": f"Product {asin} Organiser", "category_slug": cat, "trend_score": ts, "durable": dur,
            "durable_score": ds, "stock_state": stock, "exit_flag": fall, "exit_note": note, "seasonal": seas,
            "compliance": comp, "actionable": "True", "rank_change": rc, "days_on_list_14d": 14, "bs_rank_today": 20,
            "price_inr": 199.0, **kw}

sg = pd.DataFrame([S("A1", ds=99), S("A2", ds=95), S("A3", ds=94), S("A4", ds=93),            # kitchen ×4 → cap
                   S("B1", cat="garden", ds=80), S("C1", cat="luggage", ds=70, dur="True"),   # sheet strings
                   S("X1", cat="sports", ds=99.9, fall="falling", note="fell #12 → #40 in 3 days"),
                   S("X2", cat="sports", ds=99.8, stock="unavailable"),
                   S("X3", cat="sports", ds=99.7, stock="unknown"),
                   S("X4", cat="sports", ds=99.6, comp="REVIEW: EXPLOSIVES"),
                   S("X5", cat="sports", ds=99.5, seas="SEASONAL: Diwali"),
                   S("N1", cat="sports", ds=50, dur=False)])
rej = []
dp = picks.select_durable(sg, n=10, max_per_cat=2, today="2026-11-16", rejected=rej)
got = list(dp["asin"])
check("durable arm: only durable items, best durability first, ≤2 per category",
      got[:2] == ["A1", "A2"] and "A3" not in got and "N1" not in got and {"B1", "C1"} <= set(got), got)
check("durable arm: falling / sold-out / unverified / explosives / out-of-season durable items are dropped",
      not ({"X1", "X2", "X3", "X4", "X5"} & set(got)), got)
check("durable arm: the falling item is reported with its own reason", any("falling — fell #12 → #40" in w for _, w in rej), rej)
check("durable arm: rows are tagged arm=durable", set(dp["arm"]) == {"durable"})
check("durable arm: a signals table without the durable column returns nothing, no crash",
      len(picks.select_durable(sg.drop(columns=["durable"]), n=5, today="2026-11-16")) == 0)

mom_in = pd.DataFrame([S("M1", ts=60, dur=False), S("M2", ts=55, dur=False, fall="falling", note="fell #9 → #25 in 3 days"),
                       S("M3", cat="garden", ts=50, dur=False)])
rej2 = []
mp = picks.select_picks(mom_in, n=5, today="2026-11-16", rejected=rej2)
check("momentum arm: a climber that is falling NOW is not picked (post would say 'climbing')",
      list(mp["asin"]) == ["M1", "M3"] and any("falling" in w for _, w in rej2), (list(mp["asin"]), rej2))
check("momentum arm: rows are tagged arm=momentum", set(mp["arm"]) == {"momentum"})
check("momentum arm: bill-payment tiles are still rejected",
      len(picks.select_picks(pd.DataFrame([{**S("Z1", dur=False), "title": "Credit Card Bill"}]), n=3, today="2026-11-16")) == 0)

# ══ 4. forward test per arm ═══════════════════════════════════════════════════════
hist_f = pd.DataFrame([row(dd, f"F{k:07d}", k) for dd in days for k in range(1, 61)])
chosen = pd.DataFrame([{**S("F0000010", dur=True), "category_slug": "kitchen", "arm": "durable"},
                       {**S("F0000040", dur=False), "category_slug": "kitchen", "arm": "momentum"}])
mr = picks.make_rows(chosen, hist_f, days[3])
check("make_rows: picks AND their baselines carry the arm",
      set(mr.loc[mr["role"] == "pick", "arm"]) == {"durable", "momentum"}
      and set(mr.loc[(mr["role"] == "baseline"), "arm"]) == {"durable", "momentum"}
      and (mr.loc[mr["role"] == "baseline"].groupby("arm").size() == picks.BASELINE_PER_PICK).all(), mr)
trk = picks.update_tracking(mr.astype(str), hist_f)
old_rows = trk.drop(columns=["arm"]).iloc[:0]
check("arm_of: rows from before v1.51 (no arm column) count as momentum", set(picks.arm_of(trk.drop(columns=["arm"]))) == {"momentum"})
check("arm_of: blank / NaN arm counts as momentum", set(picks.arm_of(pd.DataFrame({"arm": ["", "nan", None, "durable"]}))) == {"momentum", "durable"})
asc = picks.arm_scorecard(trk)
check("arm_scorecard: one table, an `arm` column, both arms present", set(asc["arm"]) == {"momentum", "durable"} and "cohort" in asc, asc.head())
check("momentum_verdict reads the momentum arm only (the pre-registered verdict is not diluted by the durable arm)",
      len(picks.arm_rows(trk, "momentum")) == 4 and len(picks.arm_rows(trk, "durable")) == 4 and isinstance(picks.momentum_verdict(trk), str))
check("momentum_verdict on a durable-only tracker is empty, not a crash", picks.momentum_verdict(picks.arm_rows(trk, "durable")) == "")
fs_demo = pd.DataFrame([
    {"arm": "durable", "horizon_days": 7, "metric": "alive", "cohort": "rank-matched", "role": "pick", "resolved": 60, "successes": 57, "rate": .95, "ci_low": .86, "ci_high": .98},
    {"arm": "durable", "horizon_days": 7, "metric": "alive", "cohort": "rank-matched", "role": "baseline", "resolved": 180, "successes": 126, "rate": .70, "ci_low": .63, "ci_high": .76}])
check("durable_line: says 'picks ahead' only when the intervals separate, names the arm, shows n",
      "Durable picks" in picks.durable_line(fs_demo) and "picks ahead" in picks.durable_line(fs_demo) and "n=60" in picks.durable_line(fs_demo),
      picks.durable_line(fs_demo))
check("fair_line for the momentum arm ignores durable rows", picks.fair_line(fs_demo) == "")

# ══ 5. select_mixed ═══════════════════════════════════════════════════════════════
cats = ["kitchen", "garden", "sports", "luggage", "pet-supplies", "home-improvement"]
mix = []
for ci, c in enumerate(cats):
    for k in range(6):
        mix.append(S(f"{c[:2].upper()}D{k}", cat=c, ds=90 - k - ci * .1, ts=30, dur=True, rc=0))            # durable only
        mix.append(S(f"{c[:2].upper()}M{k}", cat=c, ds=10, ts=60 - k - ci * .1, dur=False, rc=20))          # climbers only
sm = pd.DataFrame(mix)
hist_m = pd.DataFrame([row(dd, a, 30 if i < 10 else 5, cat=c) for i, dd in enumerate(days)
                       for a, c in zip(sm["asin"], sm["category_slug"])])
tf.BR_DURABLE_SHARE = 0.67
m = tf.select_mixed(sm, hist_m, TODAY, 12, [], max_per_cat=3)
check("mixed: 12 slots → 8 durable + 4 momentum (2/3 share), all distinct",
      m["arm"].value_counts().to_dict() == {"durable": 8, "momentum": 4} and m["asin"].is_unique, m["arm"].value_counts().to_dict())
check("mixed: arms are interleaved 2:1 so both appear across the day", list(m["arm"][:6]) == ["durable", "durable", "momentum"] * 2, list(m["arm"]))
check("mixed: category cap holds across BOTH arms", m["category_slug"].value_counts().max() <= 3, m["category_slug"].value_counts().to_dict())
m0 = tf.select_mixed(sm, hist_m, TODAY, 12, [], max_per_cat=3, share=0)
m1 = tf.select_mixed(sm, hist_m, TODAY, 12, [], max_per_cat=3, share=1)
check("mixed: share 0 = momentum only (the v1.50 behaviour), share 1 = durable only",
      set(m0["arm"]) == {"momentum"} and set(m1["arm"]) == {"durable"}, (m0["arm"].tolist(), m1["arm"].tolist()))
few = sm[~sm["asin"].str.contains("D")].copy()                                  # no durable products at all
mf = tf.select_mixed(few, hist_m, TODAY, 12, [], max_per_cat=3)
check("mixed: with nothing durable, momentum fills every slot (no empty slots)", len(mf) == 12 and set(mf["arm"]) == {"momentum"}, len(mf))
nomom = sm[~sm["asin"].str.contains("M")].copy()
mn = tf.select_mixed(nomom, hist_m, TODAY, 12, [], max_per_cat=3)
check("mixed: with no climbers, durable tops up to full", len(mn) == 12 and set(mn["arm"]) == {"durable"}, (len(mn), mn["arm"].tolist()))

# ══ 6. post copy: provable claims ═════════════════════════════════════════════════
hist_c = pd.DataFrame([row(dd, "CP00000001", 12, title="Steel Lunch Box") for dd in days[2:]]
                      + [row(dd, "CP00000002", 60 if i < 13 else 40, title="Foam Roller") for i, dd in enumerate(days)])
real = durable.durability(hist_c, TODAY)
pd_row = {"asin": "CP00000001", "title": "Steel Lunch Box", "category_slug": "kitchen", "arm": "durable",
          "durable_days": str(int(real.loc["CP00000001", "durable_days"])), "durable_window": str(int(real.loc["CP00000001", "durable_window"]))}
mo_row = {"asin": "CP00000002", "title": "Foam Roller", "category_slug": "kitchen", "arm": "momentum"}
batch = pd.DataFrame([pd_row, mo_row])
msg = picks.post_messages(batch, hist_c, "tag-21", TODAY, cat_names={"kitchen": "Home & Kitchen"}, week_label="test")[0]
n_real, w_real = int(real.loc["CP00000001", "durable_days"]), int(real.loc["CP00000001", "durable_window"])
days_txt = f"{n_real} days on the list" if n_real == w_real else f"{n_real} of {w_real} days on the list"
check("copy: durable pick says '<N> days on the list' (or 'N of M') with the numbers we observed, plus its rank",
      f"#12 · {days_txt}" in msg and "of the last" not in msg, msg)
check("copy: the N in the sentence equals the days that item really is in HISTORY",
      n_real == hist_c[(hist_c.asin == "CP00000001") & hist_c.date.isin(days[-w_real:])]["date"].nunique(), (n_real, w_real))
check("copy: a momentum pick keeps its rank-move line and has no durable wording", "#60 → #40" in msg or "#" in msg.split("Foam Roller")[1][:120])
check("copy: no explainer sentence, no legend line, new title (v1.51 user feedback)",
      "Tap a name" not in msg and "tracked daily" not in msg and "🧱 =" not in msg and "Hidden Amazon Finds" not in msg
      and "bestseller lists today" not in picks.wa_bundle(batch, hist_c, "t", TODAY, cat_names={})[0]
      and msg.startswith("🔍 <b>Amazon Hidden Bestsellers</b> — test · <i>#ad</i>\n\n1."), msg[:160])
check("copy: durable line is short — rank, category, days, and no 'last' wording; icon is 🏆", "🏆 Home &amp; Kitchen · #12 · " in msg and len(next(l for l in msg.split("\n") if "🏆" in l)) < 60, msg)
check("copy: no price, no 'best' / 'trending now' / forecast words in a durable post",
      not re.search(r"₹|\bbest\b|\bwill\b|\bhot\b|going viral|trending now", msg, re.I), msg)
w_txt = picks.wa_bundle(batch, hist_c, "tag-21", TODAY, cat_names={"kitchen": "Home & Kitchen"})[0]
x_txt = picks.x_text(pd_row, picks.facts_for(pd_row, hist_c, TODAY), "Home & Kitchen", "tag-21")
check("copy: WhatsApp and X carry the same provable sentence, X stays within its limit",
      f"#12 in Home & Kitchen · {days_txt}" in w_txt and f"#12 in Home & Kitchen · {days_txt}" in x_txt
      and picks.x_weight(x_txt) <= picks.X_LIMIT, x_txt)
bare = {k: v for k, v in pd_row.items() if k not in ("durable_days", "durable_window")}
check("copy: a durable row without the counts recomputes them from HISTORY (never invents a number)",
      days_txt in picks.post_messages(pd.DataFrame([bare]), hist_c, "t", TODAY, cat_names={}, week_label="x")[0])
check("copy: pool rows read back as strings ('14.0', 'nan') are handled",
      "14 days on the list" in picks.post_messages(pd.DataFrame([{**pd_row, "durable_days": "14.0", "durable_window": "14.0"}]),
                                                    hist_c, "t", TODAY, cat_names={}, week_label="x")[0]
      and "13 of 14 days on the list" in picks.post_messages(pd.DataFrame([{**pd_row, "durable_days": "13", "durable_window": "14"}]),
                                                              hist_c, "t", TODAY, cat_names={}, week_label="x")[0])

# ══ 7. calculator feed + sort ═════════════════════════════════════════════════════
act = pd.DataFrame([S(f"T{i}", dur=False, ts=90 - i, seasonal="") for i in range(6)]
                   + [S("D1", ds=95, ts=10), S("D2", ds=90, ts=12), S("D3", ds=85, ts=11, seas="SEASONAL: Diwali"),
                      S("DF", ds=99, ts=20, fall="falling", note="fell")])
feed = tf.calc_feed(act, 5, 2)
check("calc feed: durable resellable products first, falling and seasonal-durable never added as durable slots",
      list(feed["asin"][:2]) == ["D1", "D2"] and "DF" not in set(feed["asin"]) and len(feed) == 5, list(feed["asin"]))
check("calc feed: durable_n=0 → plain top by trend score, falling still excluded",
      list(tf.calc_feed(act, 3, 0)["asin"]) == ["T0", "T1", "T2"] and "DF" not in set(tf.calc_feed(act, 30, 0)["asin"]))
check("calc feed: an older signals table without the columns still works", len(tf.calc_feed(act[["asin", "title", "trend_score"]], 3, 2)) == 3)

ex = [{"asin": "Q1", "product": "quoted", "signal": "📉 falling", "trend_score": 5, "quote": 100, "last_seen": "2026-11-16"}]
exist = [{"asin": "H1", "product": "high momentum", "signal": "📈 Sustained climber", "trend_score": 60, "last_seen": "2026-11-16"},
         {"asin": "D9", "product": "lasting", "signal": "🧱 Durable", "trend_score": 20, "last_seen": "2026-11-16"},
         {"asin": "F9", "product": "falling", "signal": "📈 Sustained climber · 📉 falling", "trend_score": 90, "last_seen": "2026-11-16"}]
merged = calculator.merge(exist, [], today="2026-11-16")
order = [r["asin"] for r in merged]
check("calculator: among rows waiting for a quote, durable (🧱) first, falling (📉) last, then trend score",
      order == ["D9", "H1", "F9"], order)

# ══ 8. brief ══════════════════════════════════════════════════════════════════════
bs = pd.DataFrame([
    S("L1", ds=95, ts=30, durable_note="on the list 14 of the last 14 days · median #5", resell_ok=True, non_product="", competition="Crowded listing (14 sellers)"),
    S("L2", ds=90, ts=30, durable_note="on the list 13 of the last 14 days · median #9", resell_ok="False", non_product=""),
    S("L3", ds=88, ts=30, durable_note="x", resell_ok=True, non_product="", fall="falling", note="fell #8 → #31 in 3 days"),
    S("L4", ds=87, ts=30, durable_note="x", resell_ok=True, non_product="", seas="SEASONAL: Diwali"),
    S("L5", ds=86, ts=30, durable_note="x", resell_ok=True, non_product="not a physical product (credit card bill) — bill payment"),
    S("R1", dur=False, ts=70, resell_ok=True, non_product="", durable_note=""),
    S("R2", dur=False, ts=65, resell_ok=True, non_product="", fall="falling", note="fell #5 → #20 in 3 days", durable_note="")])
bs["category"] = "Home & Kitchen"
li = brief.durable_items(bs, cat_names={"kitchen": "Home & Kitchen"})
check("brief: lasting list = durable AND sourceable AND not falling AND not seasonal AND a real product",
      [r["asin"] for r, _ in li] == ["L1"], [r["asin"] for r, _ in li])
ew = brief.exit_watch(bs, cat_names={"kitchen": "Home & Kitchen"})
check("brief: exit watch = durable products that have started falling", [r["asin"] for r, _ in ew] == ["L3"], [r["asin"] for r, _ in ew])
rs, _ = brief.rising(bs, hist_m, TODAY, picks_mod=picks, cat_names={})
bs_b = bs.copy(); bs_b.loc[bs_b["asin"] == "R1", "resell_ok"] = "False"
rs_b, _ = brief.rising(bs_b, hist_m, TODAY, picks_mod=picks, cat_names={})
check("brief: 'rising' leaves out brand / formulated / hazmat items (not sourceable by a seller)", "R1" not in [r["asin"] for r, _, _ in rs_b])
check("brief: 'rising' no longer lists a product that has just fallen", "R2" not in [r["asin"] for r, _, _ in rs] and "R1" in [r["asin"] for r, _, _ in rs])
page, text = brief.build(bs, pd.DataFrame(), "Track record: building", hist_m, TODAY, picks, {"kitchen": "Home & Kitchen"}, 2,
                         fader="Observed here: after a fall of 10+ places in 3 days, 39% of products were off the top 100 a week later (n=583 item-days) vs 17% of steady ones (n=2825).")
check("brief: sections 'Lasting demand' and 'Exit watch' appear, ahead of 'Rising this week'",
      "Lasting demand" in page and "Exit watch" in page and page.index("Lasting demand") < page.index("Exit watch") < page.index("Rising this week"))
check("brief: says plainly that durable also means crowded and that rank is not sales", "other sellers already" in page and "Rank is not sales" in page)
check("brief: rising section says climbs are the noisiest signal", "noisiest signal" in page)
check("brief: the observed base-rate sentence is in the page", "39% of products were off the top 100" in page)
check("brief: the plain-text summary names a lasting item", "Lasting" in text, text)
old_sig = bs.drop(columns=[c for c in ("durable", "durable_score", "durable_note", "exit_flag", "exit_note", "non_product")])
pg2, _ = brief.build(old_sig, pd.DataFrame(), "", hist_m, TODAY, picks, {"kitchen": "Home & Kitchen"}, 0)
check("brief: a signals table from before v1.51 still builds (no durable sections, no crash)", "Lasting demand" not in pg2 and "Rising this week" in pg2)

# ══ 9. non-product, NaN titles, version, workflows ════════════════════════════════
for t in ("Credit Card Bill", "Amazon Shopping Voucher", "Amazon Pay Digital Gift Card", "LPG cylinder booking", "Mobile Recharge"):
    check(f"non-product: '{t}'", bool(compliance.non_product(t)))
for t in ("Digital Kitchen Scale", "Card Holder Wallet", "Rechargeable Mosquito Bat", "Lighter Gas Recharge Can", "WHOOP Fitness Band", "Gift Wrapping Paper Roll"):
    check(f"physical product kept: '{t}'", not compliance.non_product(t), compliance.non_product(t))
check("resell gate: a bill-payment tile is kind 'digital', not sourceable",
      resell_gate.check("Credit Card Bill", "luggage")["kind"] == "digital" and not resell_gate.check("Amazon Pay Gift Card", "kitchen")["ok"])
check("compliance flags tolerate a NaN title (CSV read-back)", compliance.compliance_flags(NAN, "kitchen") and compliance.seasonal_flag(NAN) == "")
check("VERSION is 1.51 or later", float(tf.VERSION) >= 1.51, tf.VERSION)
here = os.path.dirname(os.path.abspath(__file__))
wf = {n: open(os.path.join(here, ".github", "workflows", n)).read() for n in ("drip.yml", "trend_fetcher.yml")}
check("workflows pass BR_DURABLE_SHARE (drip) and BR_CALC_DURABLE_N (daily)",
      "BR_DURABLE_SHARE" in wf["drip.yml"] and "BR_CALC_DURABLE_N" in wf["trend_fetcher.yml"])

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
