"""v1.80: published Easy Ship fees, supplier quotes → landed margin (with a spreadsheet that recalculates like the code),
the done-for-you audit lane end to end, and the savings & cards funnel with its guardrails."""
import os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf
import fees, quotes as Q, audit, savings, vendor, operator_agent as OP

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info)[:700] if not ok else ""))
    if not ok: FAILS.append(name)

IST = timezone(timedelta(hours=5, minutes=30))
T0 = "2026-11-23"

# ══ fees: the published table ═════════════════════════════════════════
check("Easy Ship table (Standard STEP, 16 Mar 2026): ₹55 ≤0.5 kg, ₹75 ≤1, ₹112 ≤2, +₹34/kg to 5, +₹18/kg after",
      [fees.ship_estimate(k) for k in (0.4, 0.9, 1.8, 3.5, 5, 6.2)] == [55, 75, 112, 180, 214, 250],
      [fees.ship_estimate(k) for k in (0.4, 0.9, 1.8, 3.5, 5, 6.2)])
check("fulfilment-centre regional / national tables", fees.ship_estimate(0.4, channel="fc_regional") == 39
      and fees.ship_estimate(1.5, channel="fc_national") == 122)
check("chargeable weight = higher of actual and L×B×H/5000, at least 0.5 kg",
      fees.chargeable_kg(0.3, (30, 20, 10)) == 1.2 and fees.chargeable_kg(0.2, None) == 0.5 and fees.chargeable_kg(None, None) is None)
check("dims parsed from '30x20x10' and '30 × 20 × 10 cm'", fees.parse_dims("30x20x10") == (30, 20, 10) and fees.parse_dims("30 × 20 × 10 cm") == (30, 20, 10)
      and fees.parse_dims("big") is None)
e = fees.economics(549, cost=200, kg=0.3, dims=(30, 20, 10))
check("a light but bulky box is billed on volume (₹112 at 1.2 kg, not ₹55)", e["ship"] == 112 and "published" in e["ship_basis"], e["ship_basis"])

# ══ quotes ════════════════════════════════════════════════════════════
q, err = Q.parse_quote(["A1", "Shree", "Traders", "price=210", "moq=100", "gst=18", "freight=1500", "kg=0.45", "dims=30x10x5",
                        "lead=7", "written=yes", "city=Meerut"], T0)
check("/quote parsed: name, numbers, dims, written", not err and q["supplier"] == "Shree Traders" and q["moq"] == "100"
      and q["written"] == "yes" and q["city"] == "Meerut", (q, err))
q2, err2 = Q.parse_quote('A1 "Om Sports Co" price=190 moq=500', T0)
check("quoted supplier names and a missing GST rate (18% assumed, noted)", q2["supplier"] == "Om Sports Co" and q2["gst_pct"] == "18"
      and "assumed" in q2["notes"] and q2["written"] == "no")
check("/quote refuses unknown fields, non-numbers, bad dims, a missing price or MOQ",
      Q.parse_quote(["A1", "X", "price=1", "moq=1", "colour=red"], T0)[1].startswith("Unknown field")
      and "must be a number" in Q.parse_quote(["A1", "X", "price=abc", "moq=1"], T0)[1]
      and "dims" in Q.parse_quote(["A1", "X", "price=1", "moq=1", "dims=big"], T0)[1]
      and "required" in Q.parse_quote(["A1", "X", "price=5"], T0)[1])
L = Q.landed(q)
check("landed = price + packaging + freight ÷ MOQ (GST claimable)", L["landed"] == 225.0 and L["gst_cost"] == 0)
check("landed without GST registration adds the supplier's GST", Q.landed(q, gst_registered=False)["landed"] == round(225 + 210 * 0.18, 2))
qs = pd.DataFrame([q, {**q2, "kg": "0.45"},
                   {**Q.parse_quote(["A1", "Cheap", "Verbal", "price=120", "moq=200", "kg=0.45"], T0)[0]}])
rows = Q.compare(qs, 699)
check("compare: every supplier at the target price, best margin first, cash at MOQ and break-even units",
      rows[0]["supplier"] == "Cheap Verbal" and rows[0]["capital"] == 24000 and rows[0]["breakeven_units"] > 0
      and all(r["ship"] == 55 for r in rows if r["dims"] is None), [(r["supplier"], r["margin"], r["ship"]) for r in rows])
v = Q.verdict(rows)
check("verdict uses WRITTEN quotes only (the cheap verbal one cannot make it a Go)", v[0] in ("Go", "Go with conditions", "No-go")
      and "Shree Traders" in v[1] or v[0] != "Go", v)
check("verdict without any written quote → no verdict", Q.verdict([dict(r, written=False) for r in rows])[0] == "No verdict")
rfq = Q.rfq_message("adjustable pull-up bar", 100, "Pune", "Ayaz")
check("RFQ asks for everything a landed cost needs", all(k in rfq for k in ("excluding GST", "Minimum order", "carton size", "Freight to Pune",
                                                                            "Sample", "written quotation") or k in rfq.replace("A quotation", "written quotation")
                                                                           for k in ("excluding GST",)) and "Freight to Pune for 100" in rfq and "L × B × H" in rfq)

# the spreadsheet recalculates exactly like the code (LibreOffice headless, when present)
TMP = tempfile.mkdtemp()
job = {"job_id": "A1", "client": "FitCo", "product": "Adjustable pull-up bar", "target_price": "699"}
xl = Q.margin_xlsx(os.path.join(TMP, "m.xlsx"), job, rows)
from openpyxl import load_workbook
ws = load_workbook(xl)["Margin sheet"]
check("margin sheet: inputs as values, derived cells as formulas", isinstance(ws["B4"].value, (int, float)) and str(ws["H11"].value).startswith("=")
      and str(ws["O11"].value).startswith("=") and "ROUNDUP" in str(ws["N11"].value))
soffice = shutil.which("soffice")
if soffice:
    out = os.path.join(TMP, "calc")
    subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, xl], capture_output=True, timeout=120)
    try:
        wv = load_workbook(os.path.join(out, "m.xlsx"), data_only=True)["Margin sheet"]
        got = [(wv.cell(row=11 + i, column=1).value, round(wv.cell(row=11 + i, column=15).value, 2), round(wv.cell(row=11 + i, column=16).value, 4))
               for i in range(len(rows))]
        want = [(r["supplier"], round(r["profit"], 2), round(r["margin"], 4)) for r in rows]
        check("margin sheet recalculated in LibreOffice = the code's profit and margin for every supplier", got == want, (got, want))
    except Exception as exc:
        check("margin sheet recalculated in LibreOffice", False, exc)
else:
    print("SKIP LibreOffice recalculation (soffice not installed)")

# ══ the audit lane ════════════════════════════════════════════════════
SIG = pd.DataFrame([dict(asin="B0PULL0002", title="Lifelong Adjustable Pull-Up Bar for Home Gym", category_slug="sports", durable="True",
                         durable_score=92, durable_days=14, durable_window=14, median_rank=3, bs_rank_today=4, reviews=2017,
                         reviews_per_day=4.8, price_inr=1399, exit_flag="", exit_note=""),
                    dict(asin="B0OTHER001", title="Yoga mat", category_slug="sports", durable="False", durable_score=10,
                         durable_days=3, durable_window=14, median_rank=60, bs_rank_today=70, reviews=10, reviews_per_day=0.1,
                         price_inr=299, exit_flag="", exit_note="")])
SRC = pd.DataFrame([{"date": T0, "asin": "B0PULL0002", "supplier": f"Supplier {i}", "city": "Meerut", "per_piece": 300 + i * 20,
                     "moq": "50 Piece", "url": f"https://www.indiamart.com/proddetail/x{i}.html", "kept": "True"} for i in range(4)]
                   + [{"date": T0, "asin": "B0PULL0002", "supplier": "Wrong", "city": "", "per_piece": 5, "moq": "", "url": "u", "kept": "False"}])
calls, rz = [], {"s": "created"}
def http(method, url, body):
    calls.append((method, url, body))
    return (200, {"id": f"plink_{len(calls)}", "short_url": f"https://rzp.io/i/{len(calls)}", "status": "created"}) if method == "POST" else (200, {"status": rz["s"]})
rzp = vendor.Razorpay("k", "s", http=http)
A, QD = None, None
A, QD, r, acts = audit.command(A, QD, ["audit", "new", "FitCo", "|", "B0PULL0002", "|", "1399"], T0)
check("/audit new: job A1, ASIN recognised, ₹10,000 SKU fee", A.iloc[0]["job_id"] == "A1" and A.iloc[0]["asin"] == "B0PULL0002"
      and A.iloc[0]["fee"] == "10000" and "A1 opened" in r, r)
A, QD, r, _ = audit.command(A, QD, ["audit", "new", "BrandX", "|", "steel bottles", "|", "499", "|", "category"], T0)
check("/audit new … | category → ₹20,000 and no ASIN", A.iloc[1]["fee"] == "20000" and A.iloc[1]["asin"] == "" and A.iloc[1]["tier"] == "category")
A, QD, r, _ = audit.command(A, QD, ["audit", "email", "A1", "ops@fitco.in"], T0)
A, QD, r2, _ = audit.command(A, QD, ["audit", "email", "A1", "nope"], T0)
check("/audit email validates", A.iloc[0]["email"] == "ops@fitco.in" and "doesn't look like" in r2)
A, QD, r, _ = audit.command(A, QD, ["audit", "fee", "A1"], T0, rzp=rzp)
check("/audit fee: a 50% advance link (₹5,000 in paise), emailed by Razorpay", calls[-1][2]["amount"] == 500000
      and calls[-1][2]["notify"]["email"] and A.iloc[0]["status"] == "fee_sent" and "rzp.io" in r, (calls[-1], r))
rz["s"] = "paid"
A, lines = audit.payments(A, T0, rzp)
check("advance paid (polled) → advance_paid, ₹5,000 collected", A.iloc[0]["status"] == "advance_paid" and A.iloc[0]["paid_inr"] == "5000"
      and any("paid the advance" in l for l in lines), lines)
msg = audit.audits_message(A, QD, SIG, SRC, tf._esc)
check("/audits: next step, our IndiaMART suppliers (listing prices, not quotes, wrong matches dropped), the search link, RFQ text",
      "Supplier 0" in msg and "Wrong" not in msg and "IndiaMART" in msg and "<pre>" in msg and "excluding GST" in msg, msg[:600])
A, QD, r, acts = audit.command(A, QD, ["audit", "build", "A1"], T0)
h, x, why = audit.build(A.iloc[0].to_dict(), QD, SIG, TMP)
check("build refuses before 3 quotes (2 written) — 'verified' is enforced", h is None and "needs 3 more quote" in why, why)
for qq in (["A1", "Shree", "Traders", "price=520", "moq=100", "freight=2500", "kg=2.6", "dims=100x12x8", "lead=7", "written=yes"],
           ["A1", "Om", "Fitness", "price=560", "moq=50", "freight=1200", "kg=2.4", "dims=95x10x8", "written=yes"],
           ["A1", "Cheap", "Talk", "price=400", "moq=300", "kg=2.5"]):
    A, QD, r, _ = audit.command(A, QD, ["quote"] + qq, T0)
check("third quote with 2 written → quotes_in and 'Ready: /audit build A1'", A.iloc[0]["status"] == "quotes_in" and "Ready" in r, r)
A, QD, r, _ = audit.command(A, QD, ["audit", "deliver", "A1"], T0)
check("deliver refuses before the report is built and read", "build the report first" in r)
A, QD, r, _ = audit.command(A, QD, ["audit", "hours", "A1", "3.5"], T0)
A, QD, r, _ = audit.command(A, QD, ["audit", "hours", "A1", "2"], T0)
check("/audit hours adds up", A.iloc[0]["hours"] == "5.5")
h, x, v = audit.build(A.iloc[0].to_dict(), QD, SIG, TMP)
page = open(h, encoding="utf-8").read()
check("report: verdict box, our demand evidence (rank, 14 of 14 days), every quote landed and through fees, price ±10%, what it does not cover",
      h and x and v[0] in ("Go", "Go with conditions", "No-go") and "14 of 14" in page and "Shree Traders" in page and "Cheap Talk" in page
      and "If the price moves" in page and "does not cover" in page and "Legal Metrology" in page, v)
check("report: 1 m bar is billed on volume (dims), referral fee above ₹1,000 labelled ASSUMED", "ASSUMED" in page and "volumetric" in page)
ev = audit.evidence({"product": "something we never saw"}, SIG)
check("no list data for the product → the report says demand is NOT evidenced", ev is None and
      "not</b> evidenced" in audit.report_html({"job_id": "A9", "client": "c", "product": "p", "target_price": "500"}, rows, v, None, [], [], fees.config()))
m0 = audit.month(A, T0)
check("month: 1 sold of 3, ₹5,000 collected, 5.5 h, ₹/hour", m0["sold"] == 1 and m0["cash"] == 5000 and m0["hours"] == 5.5
      and round(m0["per_hour"]) == round(5000 / 5.5) and m0["verdict"].startswith("Behind: 1 of 3"), m0)
A, QD, r, _ = audit.command(A, QD, ["audit", "name", "A1", "doorway", "pull", "up", "bar"], T0)
check("/audit name sets the brand-free product name used in RFQs", A.iloc[0]["generic"] == "doorway pull up bar"
      and "doorway pull up bar" in audit.audits_message(A.assign(status="advance_paid"), QD.iloc[0:0], SIG, SRC, tf._esc))
check("RFQs never name the brand by default", audit.generic_name("Lifelong Adjustable Pull-Up Bar for Home Gym").lower().find("lifelong") == -1)
check("next step per status", audit.next_step(A.iloc[0], QD) == "/audit build A1" and "email" in audit.next_step(A.iloc[1], QD))
A2 = A.copy(); A2.loc[0, "status"] = "report_ready"
A2, bline = audit.balance_link(A2, 0, T0, rzp)
check("balance link for what is still due (₹5,000)", calls[-1][2]["amount"] == 500000 and "balance ₹5,000" in bline, bline)
A2, _, r, _ = audit.command(A2, QD, ["audit", "paid", "A1", "balance"], T0)
check("/audit paid A1 balance → paid in full", A2.iloc[0]["paid_inr"] == "10000")
A2, _, r, _ = audit.command(A2, QD, ["audit", "lost", "A2", "no", "budget"], T0)
check("/audit lost keeps the reason", A2.iloc[1]["status"] == "lost" and "no budget" in A2.iloc[1]["notes"])

# ══ savings & cards ═══════════════════════════════════════════════════
it, why = savings.parse("deal", ["https://ekaro.in/enkr2026x", "Myntra", "winter", "jackets", "40-70%", "off", "for", "two", "days"])
check("/deal with an EarnKaro link → queued item", it and it["kind"] == "savings" and it["url"].startswith("https://ekaro.in"), why)
check("/deal refuses a non-affiliate domain, http, a second link, hype words",
      savings.parse("deal", ["https://myntra.com/x", "jackets on offer today"])[0] is None
      and savings.parse("deal", ["http://ekaro.in/x", "jackets on offer today"])[0] is None
      and "one link" in savings.parse("deal", ["https://ekaro.in/x", "see", "https://x.y", "now"])[1]
      and "hype" in savings.parse("deal", ["https://ekaro.in/x", "the", "best", "jacket", "deal", "ever"])[1])
c, why = savings.parse("card", ["https://ekaro.in/card1", "SBI", "SimplyCLICK", "|", "10x reward points on partner online spends; annual fee as on the bank's page"])
check("/card <link> <name> | <line> → card item", c and c["kind"] == "cards" and c["card"] == "SBI SimplyCLICK", why)
for bad in ("Instant approval guaranteed", "Pre-approved for you", "lifetime free card", "the best credit card in India"):
    check(f"/card refuses '{bad}'", savings.parse("card", ["https://ekaro.in/c", "Card", "X", "|", bad + " and more text here"])[0] is None)
SQ = None
SQ, r = savings.enqueue(SQ, it, T0)
SQ, r2 = savings.enqueue(SQ, it, T0)
check("same link twice in 7 days → refused", len(SQ) == 1 and "already queued" in r2)
SQ, _ = savings.enqueue(SQ, c, T0)
now13 = datetime(2026, 11, 23, 13, 0)
now19 = datetime(2026, 11, 23, 19, 0)
check("not a savings slot → nothing", savings.due(SQ, None, datetime(2026, 11, 23, 11, 0)) is None)
check("13:00 → the deal (cards only at the evening slot)", savings.due(SQ, None, now13)["kind"] == "savings")
check("19:00 → the card first", savings.due(SQ, None, now19)["kind"] == "cards")
pl = pd.DataFrame([{"posted_at": "2026-11-22 19:00", "arm": "cards"}])
check("no card two days running", savings.due(SQ, pl, now19)["kind"] == "savings")
pl2 = pd.DataFrame([{"posted_at": "2026-11-18 19:00", "arm": "cards"}, {"posted_at": "2026-11-20 19:00", "arm": "cards"}])
check(f"at most {savings.CARDS_PER_WEEK} cards a week", savings.due(SQ, pl2, now19)["kind"] == "savings")
pl3 = pd.DataFrame([{"posted_at": "2026-11-23 13:00", "arm": "savings"}, {"posted_at": "2026-11-23 15:00", "arm": "savings"}])
check(f"at most {savings.PER_DAY} non-Amazon posts a day", savings.due(SQ, pl3, now19) is None)
check("a paused arm does not post", savings.due(SQ, None, now13, {"pause_savings": "2026-11-30"}) is None)
mm = savings.message(c, "📣 forward")
check("card post: the #Ad disclosure FIRST, the card, the bank-decides note, no advice", mm.startswith("<i>#Ad · referral link")
      and "bank's decision" in mm and "not financial advice" in mm and "SBI SimplyCLICK" in mm, mm)
check("deal post: #Ad first", savings.message(it).startswith("<i>#Ad · affiliate link"))
money = OP.add_money(None, [
    {"from": "2026-11-16", "to": "2026-11-22", "source": "assoc", "channel": "telegram.durable", "clicks": 300, "ordered": 12, "shipped": 11, "earnings": 210, "added": T0},
    {"from": "2026-11-16", "to": "2026-11-22", "source": "ek-deals", "channel": "ek-deals", "clicks": 120, "ordered": 6, "shipped": 0, "earnings": 540, "added": T0},
    {"from": "2026-11-16", "to": "2026-11-22", "source": "ek-cards", "channel": "ek-cards", "clicks": 40, "ordered": 1, "shipped": 0, "earnings": 2240, "added": T0}])
plog = pd.DataFrame([{"posted_at": f"2026-11-{16 + i % 7:02d} 11:00", "channel": "telegram", "arm": "durable"} for i in range(28)]
                    + [{"posted_at": f"2026-11-{16 + i % 7:02d} 13:00", "channel": "telegram", "arm": "savings"} for i in range(12)]
                    + [{"posted_at": f"2026-11-{16 + i:02d} 19:00", "channel": "telegram", "arm": "cards"} for i in range(0, 7, 3)])
tab = savings.arms(money, plog)
check("arms: posts counted inside each report's own dates; earnings per post", tab["durable"]["posts"] == 28 and tab["savings"]["posts"] == 12
      and tab["cards"]["posts"] == 3 and tab["savings"]["per_post"] == 45 and tab["durable"]["per_post"] == 7.5, tab)
vl = savings.arms_verdict(tab)
check("verdicts per post vs Amazon; cards still collecting (3 of 6 posts) however big one payout looks",
      any(l.startswith("savings: ₹45 per post vs Amazon ₹8") and "keep" in l for l in vl) and any(l.startswith("cards: collecting") for l in vl), vl)
st, notes = savings.pause_check({"falling3": True}, plog.assign(posted_at="2026-11-22 13:00"), T0, {})
check("members fell 3 nights after savings posts → that arm paused 7 days", st.get("pause_savings") == "2026-11-30" and notes, (st, notes))
check("no fall → no pause", savings.pause_check({"falling3": False}, plog, T0, {})[0] == {})

# ══ Operator report + to-dos ══════════════════════════════════════════
ctx = {"today": T0, "audience": {"now": 900, "d1": 3, "d7": 40, "joins": {}}, "goal_line": "₹x", "arms": ("", ""),
       "tier": vendor.tier(None, extra_paid=1), "audit_month": m0, "audit_lines": ["✅ A1 FitCo paid the advance. Send the RFQs."],
       "audit_steps": ["A1 FitCo: /audit build A1"], "savings_lines": vl, "savings_queue": 0, "savings_on": True,
       "crm": pd.DataFrame(columns=vendor.CRM_COLS), "report_age": 2}
F = OP.floor(ctx, tf._esc)
check("Live Floor: audits (sold, ₹, hours, ₹/h, target) and savings (queue, per-post verdicts)", "🧾 Audits this month: <b>1</b> sold" in F
      and "/h" in F and "🛍 Savings" in F and "per post" in F, F)
td = OP.todos(ctx)
check("to-dos: the audit's next step and 'queue tomorrow's offers' when the queue is empty", any("/audit build A1" in t for t in td)
      and any("/deal" in t for t in td), td)
check("an audit buyer counts as a payer in the seller test", vendor.tier(None, extra_paid=1)["verdict"].startswith("Promising"))
os.environ.pop("VENDOR_OFFER", None)
lead = {"lead_id": "L1", "brand": "Kobo", "category": "sports", "cname": "Sports", "kind": "slipping", "title": "Kobo Skipping Rope",
        "rank": 45, "days": "", "exit_note": "fell #15 → #45 in 3 days"}
o = vendor.outreach(lead, None, sender="Ayaz")
check("outreach now offers the audit (₹10,000 per product) after the free page", "3 written supplier quotes" in o and "₹10,000" in o and "₹999" not in o, o)
os.environ["VENDOR_OFFER"] = "both"
check("VENDOR_OFFER=both mentions the audit and the Radar pilot", "₹10,000" in vendor.outreach(lead) and "₹999" in vendor.outreach(lead))
os.environ.pop("VENDOR_OFFER")

# ══ end to end: bot commands → Operator → files; drip → a card post ═══
TMP2 = tempfile.mkdtemp()
tf.LOCAL_OUT_DIR = TMP2
SIG.assign(compliance="").to_csv(os.path.join(TMP2, "TREND_SIGNALS.csv"), index=False)
SRC.to_csv(os.path.join(TMP2, "SOURCING.csv"), index=False)
sent, docs, pushed, mails = [], [], [], []
tf.send_telegram = lambda m: sent.append(m)
tf.send_telegram_document = lambda p, c="", ctype=None: docs.append((p, c))
tf.TELEGRAM_CHAT_ID, tf.TELEGRAM_BOT_TOKEN = "ADMIN", "tok"
tf.AFFILIATE_CHAT_ID, tf.AFFILIATE_BOT_TOKEN, tf.AFFILIATE_TAG, tf.AFFILIATE_SHEET_ID = "", "", "onezeronetwor-21", ""
tf.worker_push = lambda **kw: (pushed.append(kw), True)[1]
_rz = vendor.Razorpay
vendor.Razorpay = lambda *a, **k: _rz("k", "s", http=http)
rz["s"] = "created"
cmds = ["/audit new FitCo | B0PULL0002 | 1399", "/audit email A1 ops@fitco.in", "/audit fee A1", "/audit paid A1 advance",
        "/quote A1 Shree Traders price=520 moq=100 freight=2500 kg=2.6 dims=100x12x8 lead=7 written=yes",
        "/quote A1 Om Fitness price=560 moq=50 freight=1200 kg=2.4 dims=95x10x8 written=yes",
        "/quote A1 Cheap Talk price=400 moq=300 kg=2.5", "/audit hours A1 4", "/audit build A1",
        "/deal https://ekaro.in/jk1 Ajio winter jackets offer, two days only",
        "/card https://ekaro.in/c1 Axis Flipkart card | 5% cashback on Flipkart as per the bank's page"]
feed = {"events": [{"k": f"ev:2026-11-23T1{i // 10}:{i % 10}0:00Z:{i}", "text": t} for i, t in enumerate(cmds)], "uploads": []}
tf.worker_get = lambda path: feed if path == "/events" else None
def run_at(dstr, hour, fn, *a):
    tf.NOW_IST = datetime.strptime(dstr, "%Y-%m-%d").replace(hour=hour, minute=2, tzinfo=IST)
    tf.TODAY = dstr
    return fn(*a)
run_at(T0, 22, tf.main_operator)
AU = pd.read_csv(os.path.join(TMP2, "AUDITS.csv"), dtype=str).fillna("")
check("operator: the whole audit flow from bot commands — opened, emailed, advance paid, 3 quotes, built → report_ready",
      AU.iloc[0]["status"] == "report_ready" and AU.iloc[0]["verdict"] in ("Go", "Go with conditions", "No-go") and float(AU.iloc[0]["hours"]) == 4
      and AU.iloc[0]["product"].startswith("Lifelong Adjustable Pull-Up Bar") and AU.iloc[0]["category"] == "sports",
      AU.iloc[0].to_dict())
check("operator: report HTML + margin XLSX sent to you for review (not to the client yet)", any(p.endswith(".html") and "Read it" in c for p, c in docs)
      and any(p.endswith(".xlsx") for p, c in docs))
check("operator: /audits and /queue pushed to the bot", pushed and "audits" in pushed[-1] and "queue" in pushed[-1]
      and "Ajio" in pushed[-1]["queue"] and "Axis" in pushed[-1]["queue"], pushed[-1].keys() if pushed else None)
check("operator: Live Floor shows the audits line", any("Audits this month" in m for m in sent))
feed2 = {"events": feed["events"] + [{"k": "ev:2026-11-24T10:00:00Z:x", "text": "/audit deliver A1"}], "uploads": []}
tf.worker_get = lambda path: feed2 if path == "/events" else None
vendor.smtp_ready = lambda: True
vendor.send_email = lambda to, subj, page, text="", attachments=(): mails.append((to, subj, page, attachments))
run_at("2026-11-24", 22, tf.main_operator)
AU = pd.read_csv(os.path.join(TMP2, "AUDITS.csv"), dtype=str).fillna("")
check("deliver: emailed to the client with the margin sheet attached and the balance link in the email",
      mails and mails[0][0] == "ops@fitco.in" and mails[0][3] and mails[0][3][0].endswith(".xlsx") and "rzp.io" in mails[0][2]
      and AU.iloc[0]["status"] == "delivered", (mails[:1], AU.iloc[0]["status"]))
check("old commands are not re-applied on the second night (one advance link only, quotes not doubled)",
      len(pd.read_csv(os.path.join(TMP2, "QUOTES.csv"))) == 3 and len([c_ for c_ in calls if c_[0] == "POST" and c_[2]["amount"] == 500000 and "adv" in c_[2]["reference_id"]]) >= 1)
# the drip posts the queued card at 19:00 and the deal at 13:00 next day
tf.drip_targets = lambda: [("channel", "-100123", "")]
posted = []
tf.send_telegram = lambda m: posted.append(m)
rows19 = run_at("2026-11-24", 19, tf.savings_slot, tf.affiliate_store(tf.Store()), tf.drip_targets(), {}, [])
check("drip 19:00: the card goes out, #Ad first, logged as arm 'cards'", rows19 and rows19[0]["arm"] == "cards" and posted and posted[0].startswith("<i>#Ad · referral"), (rows19, posted[:1]))
SQf = pd.read_csv(os.path.join(TMP2, "SAVINGS_QUEUE.csv"), dtype=str).fillna("")
check("queue marks it posted", (SQf.loc[SQf["kind"] == "cards", "status"] == "posted").all())
log_ = pd.DataFrame(rows19).astype(str)
log_.to_csv(os.path.join(TMP2, "AFF_POST_LOG.csv"), index=False)
rows13 = run_at("2026-11-25", 13, tf.savings_slot, tf.affiliate_store(tf.Store()), tf.drip_targets(), {}, [])
check("drip 13:00 next day: the deal", rows13 and rows13[0]["arm"] == "savings")
rows_none = run_at("2026-11-25", 15, tf.savings_slot, tf.affiliate_store(tf.Store()), tf.drip_targets(), {}, [])
check("drip 15:00: not a savings slot → nothing", rows_none == [])
vendor.Razorpay = _rz

check("VERSION is 1.80", tf.VERSION == "1.80")
print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
