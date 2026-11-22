"""v1.70: three users + the Operator agent — fee model, vendor lane (brief, leads, outreach, CRM, payments, email),
drop-ship lane (shortlist, pilots, kill rules), Operator (queue, reports, arms, goal, Live Floor), end-to-end
operator and vendor runs with the bot queue, Razorpay and SMTP faked."""
import json, os, re, sys, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf
import fees, vendor, dropship, operator_agent as OP, growth, tags as T

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info)[:600] if not ok else ""))
    if not ok: FAILS.append(name)

IST = timezone(timedelta(hours=5, minutes=30))
esc = tf._esc

# ══ 1. fees ═══════════════════════════════════════════════════════════
check("fees: no referral fee below ₹1,000, labelled as published", fees.referral_pct(999)[0] == 0 and "published" in fees.referral_pct(999)[1])
check("fees: referral at ₹1,000+ is labelled ASSUMED, never presented as published", "ASSUMED" in fees.referral_pct(1399)[1])
check("fees: closing fee = highest published figure per band (₹27 ≤300, ₹23 ≤500, ₹75 >1,000); ₹501–1,000 is ASSUMED",
      fees.closing_fee(299)[0] == 27 and fees.closing_fee(450)[0] == 23 and fees.closing_fee(1500)[0] == 75
      and "ASSUMED" in fees.closing_fee(700)[1])
check("fees: Easy Ship estimate matches both published examples (350 g → ₹55, 3.5 kg → ~₹180)",
      fees.ship_estimate(0.35) == 55 and abs(fees.ship_estimate(3.5) - 180) <= 5, (fees.ship_estimate(0.35), fees.ship_estimate(3.5)))
W = {"Lifelong PVC Hex Dumbbells (5 kg x 2)": 10.0, "De Jure ankle and wrist weights, 1 kg × 2": 2.0, "Pair of 2kg dumbbells": 4.0,
     "Boldfit doorway pull-up bar, supports up to 200 kg": None, "Slovic resistance tube, up to 15 kg": None,
     "Yoga mat 4mm 1.2 kg": 1.2, "Steel bottle 750 ml": None, "Kettlebell 150 kg capacity bench 12 kg": 12.0}
got = {t: fees.weight_kg(t) for t in W}
check("fees: weight read from titles; load ratings ('up to 200 kg', '150 kg capacity') are not weights", got == W, got)
e1 = fees.economics(499, cost=150, kg=0.4)
check("fees: ex-GST margin with fees and shipping; GST on fees not counted as a cost",
      e1["fees"] == 23 and e1["ship"] == 55 and 0.30 < e1["margin"] < 0.50 and e1["verdict"] == "room", e1)
e2 = fees.economics(549, kg=10)
check("fees: 10 kg at ₹549 is flagged heavy and no supplier price leaves 25%", e2["heavy"] and e2["verdict"] == "no" and "no supplier price" in fees.line(e2), fees.line(e2))
e3 = fees.economics(499, cost=150, kg=0.4, dropship=True)
check("fees: drop shipping prices in supplier stock-outs (3% × 10% of the order value)", e3["cancel_cost"] == round(0.03 * 0.10 * 499, 2) and e3["margin"] < e1["margin"])
os.environ["AMZ_FEES"] = '{"gst": 0.05, "closing_501_1000": 30, "junk": 1}'
c = fees.config()
check("fees: AMZ_FEES overrides only known keys", c["gst"] == 0.05 and c["closing_501_1000"] == 30 and "junk" not in c)
os.environ.pop("AMZ_FEES")
check("fees: break-even shipping given a supplier cost", fees.economics(899, cost=300, kg=None)["breakeven_ship"] > 0)

# ══ 2. vendor lane on a small signal set ══════════════════════════════
TODAY = "2026-11-23"
SIG = pd.DataFrame([
    # durable, sourceable, light: an opening
    dict(asin="B0PULL0001", title="Slovic No-Drill Pull Up Bar for Doorway", category_slug="sports", durable="True", durable_score=95,
         durable_days=14, durable_window=14, median_rank=1, bs_rank_today=1, reviews=6873, reviews_per_day=11.6, price_inr=549,
         exit_flag="", exit_note="", non_product="", stock_state="in_stock", first_seen="2026-10-01", days_on_list_14d=14, compliance="Not flagged"),
    dict(asin="B0DUMB0001", title="Lifelong PVC Hex Dumbbells (5 kg x 2)", category_slug="sports", durable="True", durable_score=94,
         durable_days=14, durable_window=14, median_rank=2, bs_rank_today=2, reviews=23384, reviews_per_day=17.3, price_inr=549,
         exit_flag="", exit_note="", non_product="", stock_state="in_stock", first_seen="2026-10-01", days_on_list_14d=14, compliance=""),
    dict(asin="B0ANKL0001", title="De Jure Ankle and Wrist Weights, 1 kg × 2", category_slug="sports", durable="True", durable_score=80,
         durable_days=13, durable_window=14, median_rank=16.5, bs_rank_today=17, reviews=1678, reviews_per_day=4.6, price_inr=239,
         exit_flag="", exit_note="", non_product="", stock_state="in_stock", first_seen="2026-10-01", days_on_list_14d=13, compliance=""),
    dict(asin="B0GRIP0001", title="Boldfit Hand Gripper 0.3 kg", category_slug="sports", durable="True", durable_score=85,
         durable_days=14, durable_window=14, median_rank=7, bs_rank_today=6, reviews=33130, reviews_per_day=17.6, price_inr=499,
         exit_flag="", exit_note="", non_product="", stock_state="in_stock", first_seen="2026-10-01", days_on_list_14d=14, compliance=""),
    # slipping small brand
    dict(asin="B0ROPE0001", title="Kobo Skipping Rope", category_slug="sports", durable="False", durable_score=40,
         durable_days=9, durable_window=14, median_rank=33, bs_rank_today=45, reviews=900, reviews_per_day=1, price_inr=150,
         exit_flag="falling", exit_note="fell #15 → #45 in 3 days", non_product="", stock_state="in_stock", first_seen="2026-10-20",
         days_on_list_14d=9, compliance=""),
    # new and holding
    dict(asin="B0NEW00001", title="Fitzy Foam Roller", category_slug="sports", durable="False", durable_score=50,
         durable_days=8, durable_window=14, median_rank=40, bs_rank_today=38, reviews=50, reviews_per_day=2, price_inr=399,
         exit_flag="", exit_note="", non_product="", stock_state="in_stock", first_seen="2026-11-14", days_on_list_14d=8, compliance=""),
    # big brand: never a lead, never an opening
    dict(asin="B0NIVI0001", title="Nivia Football Size 5", category_slug="sports", durable="True", durable_score=90,
         durable_days=14, durable_window=14, median_rank=3, bs_rank_today=3, reviews=9000, reviews_per_day=5, price_inr=499,
         exit_flag="", exit_note="", non_product="", stock_state="in_stock", first_seen="2026-10-01", days_on_list_14d=14, compliance=""),
    # a bill-payment tile at #1 for weeks is not demand
    dict(asin="B0BILL0001", title="Amazon Pay Electricity Bill Payment", category_slug="sports", durable="False", durable_score=99,
         durable_days=14, durable_window=14, median_rank=1, bs_rank_today=1, reviews=0, reviews_per_day=0, price_inr=0,
         exit_flag="", exit_note="", non_product="bill payment", stock_state="", first_seen="2026-10-01", days_on_list_14d=14, compliance=""),
])
CATS = {"sports": "Sports, Fitness & Outdoors"}
b = vendor.category_brief(SIG, None, "sports", TODAY, cat_names=CATS, fader="39% of fallers left the top 100 within a week.")
check("brief: counts exclude the bill-payment tile", b["on_list"] == 7 and b["durable_n"] == 5, (b["on_list"], b["durable_n"]))
check("brief: lasted is ranked by durability and says how many days", b["lasted"][0]["asin"] == "B0PULL0001" and b["lasted"][0]["days"] == "all 14 days")
check("brief: slipping carries the 3-day exit note", b["slipping"] and b["slipping"][0]["note"] == "fell #15 → #45 in 3 days")
check("brief: new entrants that held ≥7 days", [x["asin"] for x in b["new_held"]] == ["B0NEW00001"], b["new_held"])
ops = [o["asin"] for o in b["openings"]]
check("brief: openings exclude the big brand and rank heavy-for-price last", "B0NIVI0001" not in ops and ops[-1] in ("B0DUMB0001", "B0ANKL0001")
      and ops[0] in ("B0PULL0001", "B0GRIP0001"), ops)
html_ = vendor.brief_html(b, reply_to="radar@onezero.in")
check("brief HTML: email-safe (inline styles, no script), fee caveat and 'not a forecast'",
      "<script" not in html_ and "style=" in html_ and "fee preview" in html_ and "Nothing here is a forecast" in html_ and "radar@onezero.in" in html_)
wa = vendor.brief_whatsapp(b)
check("brief WhatsApp text: no HTML, no links, has the slipping line and the base rate", "<" not in wa and "http" not in wa and "fell #15" in wa and "39%" in wa)
check("brief Telegram preview is short", len(vendor.brief_text(b)) < 1500)

leads = vendor.find_leads(SIG, CATS, per_cat=5)
names = [l["brand"] for l in leads]
check("leads: small brands only (no Nivia), slipping first, no bill tile", "Nivia" not in names and leads[0]["kind"] == "slipping"
      and leads[0]["brand"] == "Kobo" and "Amazon" not in names, names)
check("leads: stable ids, the same brand+category always maps to the same id", vendor.lead_id("Kobo", "sports") == leads[0]["lead_id"])
os.environ["VENDOR_OFFER"] = "radar"                 # v1.80 defaults to the audit offer; this suite checks the Radar offer
msg = vendor.outreach(leads[0], b, sender="Ayaz, OneZero")
check("outreach: built from observed numbers, offers the free page first, price second, an opt-out, a signature",
      "fell #15 → #45 in 3 days" in msg and "free" in msg and "₹999" in msg and "STOP" in msg and msg.endswith("Ayaz, OneZero"), msg)
check("outreach: no guessed brand in the greeting; product named without a dangling '…'", msg.startswith("Hi, we track") and "…" not in msg.split("\n")[0], msg)
check("outreach: the sender comes from VENDOR_SENDER at send time", (os.environ.__setitem__("VENDOR_SENDER", "Ayaz") or True) and vendor.outreach(leads[0], b).endswith("— Ayaz"))
os.environ.pop("VENDOR_SENDER")
check("brief: slipping / new counts are the true totals, not the capped list lengths",
      b["slipping_n"] == 1 and b["new_held_n"] == 1)
check("outreach: no hype words, no forecast", not re.search(r"\b(best|guarantee|viral|will (sell|go))\b", msg, re.I))
hold = [l for l in leads if l["kind"] == "holding"][0]
check("outreach (holding): quotes days on the list and rank", "has been on it" in vendor.outreach(hold, b) and f"#{hold['rank']}" in vendor.outreach(hold, b))
check("contact links: Amazon search and Google searches, nothing scraped", all(k in vendor.contact_links(hold) for k in ("amazon", "google", "instagram")))

crm, n = vendor.crm_merge(None, leads, TODAY)
check("CRM: new leads start at 'new'", n == len(leads) and set(crm["status"]) == {"new"})
crm2, n2 = vendor.crm_merge(crm.assign(status="contacted"), leads, TODAY)
check("CRM: merging again keeps their status and adds nothing", n2 == 0 and set(crm2["status"]) == {"contacted"})
lid = leads[0]["lead_id"]
crm, r = vendor.apply_command(crm, [lid, "contacted"], TODAY)
check("/lead <id> contacted moves the lead", crm.loc[crm["lead_id"] == lid, "status"].iloc[0] == "contacted" and "contacted" in r)
crm, r = vendor.apply_command(crm, [lid, "email", "ops@kobo.in"], TODAY)
crm, r2 = vendor.apply_command(crm, [lid, "email", "not-an-email"], TODAY)
check("/lead email validates the address", crm.loc[crm["lead_id"] == lid, "email"].iloc[0] == "ops@kobo.in" and "doesn't look like" in r2)
crm, r = vendor.apply_command(crm, ["Lzzzz", "call"], TODAY)
crm, r3 = vendor.apply_command(crm, [lid, "maybe"], TODAY)
check("/lead with an unknown id or status explains itself", "No lead Lzzzz" in r and "Unknown status" in r3)
crm, r = vendor.apply_command(crm, [lid, "call"], TODAY)
check("/lead call says the pilot link comes tonight", "pilot link" in r)

# tiers (rules fixed 22 Nov)
def mk(n_contacted, n_calls, paid=0, monthly=0):
    rows = []
    for i in range(n_contacted):
        st = "call" if i < n_calls else "contacted"
        rows.append({"lead_id": f"L{i:04d}", "status": st, "plan": "", "paid_at": ""})
    for i in range(paid):
        rows[i].update(status="pilot", plan="pilot", paid_at=TODAY)
    for i in range(monthly):
        rows[paid + i].update(status="monthly", plan="monthly", paid_at=TODAY)
    return pd.DataFrame(rows)
check("tier: collecting below 15 contacts", vendor.tier(mk(10, 2))["verdict"].startswith("Collecting: 10 of 15"))
check("tier: 15 contacts and <4 calls → hook problem (no infinite 'collecting')", vendor.tier(mk(15, 2))["verdict"].startswith("Hook problem"))
check("tier: 15 contacts, 4+ calls, nobody paid → change the offer", vendor.tier(mk(15, 5))["verdict"].startswith("No payers yet"))
check("tier: dropping needs 30 contacts and 8 calls", vendor.tier(mk(30, 8))["verdict"].startswith("No payers after"))
check("tier: 1 payer → promising; 2 monthly → sellers first", vendor.tier(mk(15, 5, paid=1))["verdict"].startswith("Promising")
      and vendor.tier(mk(15, 5, monthly=2))["verdict"].startswith("Sellers first"))

# payments: Razorpay faked
calls = []
def rz_http(method, url, body):
    calls.append((method, url, body))
    if method == "POST":
        return 200, {"id": f"plink_{len(calls)}", "short_url": f"https://rzp.io/i/{len(calls)}", "status": "created"}
    return 200, {"status": rz_state["s"]}
rz_state = {"s": "created"}
rzp = vendor.Razorpay("rzp_test", "sec", http=rz_http)
crm, lines = vendor.payment_actions(crm, TODAY, rzp)
row = crm[crm["lead_id"] == lid].iloc[0]
check("payments: a lead at 'call' gets a ₹999 link in paise, emailed by Razorpay, and moves to link_sent",
      calls and calls[0][2]["amount"] == 99900 and calls[0][2]["notify"]["email"] is True and calls[0][2]["customer"]["email"] == "ops@kobo.in"
      and row["status"] == "link_sent" and row["link_url"].startswith("https://rzp.io") and any("pilot link ready" in l for l in lines), (calls, lines))
n_before = len(calls)
crm, lines = vendor.payment_actions(crm, TODAY, rzp)
check("payments: an open link is polled, not created twice", len([c for c in calls[n_before:] if c[0] == "POST"]) == 0)
rz_state["s"] = "paid"
crm, lines = vendor.payment_actions(crm, "2026-11-25", rzp)
row = crm[crm["lead_id"] == lid].iloc[0]
check("payments: paid → pilot for 30 days", row["status"] == "pilot" and row["paid_at"] == "2026-11-25" and row["expires"] == "2026-12-25", row.to_dict())
check("subscribers: the paid lead gets the Radar", lid in set(vendor.subscribers(crm, "2026-11-30")["lead_id"]))
rz_state["s"] = "created"
crm, lines = vendor.payment_actions(crm, "2026-12-21", rzp)
row = crm[crm["lead_id"] == lid].iloc[0]
check("payments: 5 days before expiry a ₹1,999 renewal link is made; access continues meanwhile",
      row["link_amount"] == "1999" and row["status"] == "link_sent" and lid in set(vendor.subscribers(crm, "2026-12-22")["lead_id"])
      and any("renewal link" in l for l in lines), (row.to_dict(), lines))
rz_state["s"] = "paid"
crm, lines = vendor.payment_actions(crm, "2026-12-23", rzp)
row = crm[crm["lead_id"] == lid].iloc[0]
check("payments: a paid renewal extends from the old end date (never cuts days)", row["status"] == "monthly" and row["expires"] == "2027-01-24", row.to_dict())
check("MRR counts monthly subscribers only", vendor.mrr(crm, "2026-12-30")[0] == 1999)
crm_nokey, lines_nokey = vendor.payment_actions(pd.DataFrame([{"lead_id": "L9", "brand": "X", "status": "call"}]), TODAY, vendor.Razorpay("", ""))
check("payments without Razorpay keys: tells you to send the link by hand, changes nothing", any("send the ₹999" in l for l in lines_nokey)
      and crm_nokey.iloc[0]["status"] == "call")

sentmail = []
ok = vendor.send_email("ops@kobo.in", "Sports Radar", html_, wa, sender="radar@onezero.in", smtp=lambda f, to, m: sentmail.append((f, to, m)))
check("email: multipart with a plain-text part and the HTML", ok and sentmail and "text/html" in sentmail[0][2] and "text/plain" in sentmail[0][2])

# ══ 3. drop-ship lane ═════════════════════════════════════════════════
SIG2 = SIG.copy()
SIG2.loc[SIG2["asin"] == "B0GRIP0001", "title"] = "Boldfit Hand Gripper Adjustable 0.3 kg"
ds = dropship.shortlist(SIG2, CATS)
ids = [x["asin"] for x in ds]
check("shortlist: ₹299–999, durable, sourceable, not heavy (no 10 kg dumbbells, no ₹239 weights, no brand); weight-known first, the bar after it labelled 'weigh a sample'",
      ids == ["B0GRIP0001", "B0PULL0001"] and "weigh a sample" in ds[1]["line"], [(x["asin"], x["line"]) for x in ds])
SIG4 = SIG2.copy(); SIG4.loc[SIG4["asin"] == "B0GRIP0001", "compliance"] = "REVIEW: Insecticides Act — licence"
check("shortlist: a licence-flagged product is never a pilot candidate", "B0GRIP0001" not in [x["asin"] for x in dropship.shortlist(SIG4, CATS)])
check("shortlist line shows fees, shipping and the most a supplier can charge", "a supplier price under" in ds[0]["line"] and "ship ~₹55" in ds[0]["line"], ds[0]["line"])
check("shortlist text explains the filters and how to start a pilot", "/pilot add" in dropship.shortlist_text(ds) and "₹299–999" in dropship.shortlist_text(ds))
check("readiness list: seller of record, GST place of business, Legal Metrology, sample, fee preview",
      all(any(k in x for x in dropship.READINESS) for k in ("YOUR name", "GST", "Legal Metrology", "sample", "fee preview")))
P, O, r = dropship.command(None, None, ["pilot", "add", "B0GRIP0001", "120"], TODAY, SIG2)
check("/pilot add starts P1 with the product's title", P.iloc[0]["pilot_id"] == "P1" and "Boldfit" in P.iloc[0]["title"] and "P1 started" in r, r)
for i in range(2, 4):
    P, O, r = dropship.command(P, O, ["pilot", "add", f"B0XXXX000{i}", "100"], TODAY, SIG2)
P, O, r = dropship.command(P, O, ["pilot", "add", "B0XXXX0009", "100"], TODAY, SIG2)
check("/pilot add refuses a 4th active pilot", "max 3" in r)
P, O, r = dropship.command(P, O, ["pilot", "add", "nonsense"], TODAY, SIG2)
check("/pilot add with bad input explains the format", "Use: /pilot add" in r)
P, O, _ = dropship.command(P, O, ["order", "P1", "12", "499"], TODAY, SIG2)
st = dropship.pilot_status(P, O, SIG2, "2026-11-30")
p1 = [x for x in st if x["pilot_id"] == "P1"][0]
check("pilot: orders and margin estimated with the fee model", p1["orders"] == 12 and p1["margin"] and p1["margin"] > 0.25 and p1["verdict"].startswith("collecting"), p1)
P, O, _ = dropship.command(P, O, ["cancel", "P1", "1"], TODAY, SIG2)
p1 = [x for x in dropship.pilot_status(P, O, SIG2, "2026-11-30") if x["pilot_id"] == "P1"][0]
check("pilot kill rule: >2.5% supplier cancellations after 10 orders → STOP", p1["verdict"].startswith("STOP — supplier cancellations"), p1["verdict"])
p2 = [x for x in dropship.pilot_status(P, O, SIG2, "2026-12-10") if x["pilot_id"] == "P2"][0]
check("pilot kill rule: under 5 orders in 14 days → stop or fix the listing", p2["verdict"].startswith("STOP or fix the listing"), p2["verdict"])
P, O, r = dropship.command(P, O, ["pilot", "stop", "P2", "no", "demand"], TODAY, SIG2)
check("/pilot stop ends it", P.loc[P["pilot_id"] == "P2", "status"].iloc[0] == "stopped" and "no demand" in P.loc[P["pilot_id"] == "P2", "reason"].iloc[0])
P5, O5, _ = dropship.command(None, None, ["pilot", "add", "B0GRIP0001", "120"], TODAY, SIG2)
P5, O5, _ = dropship.command(P5, O5, ["order", "P1", "25", "499"], TODAY, SIG2)
SIG3 = SIG2.copy(); SIG3.loc[SIG3["asin"] == "B0GRIP0001", "exit_flag"] = "falling"
k = dropship.pilot_status(P5, O5, SIG3, "2026-12-05")[0]
check("pilot: 20+ orders, margin ≥25%, no cancels → KEEP, with a warning when the product's own demand starts slipping",
      k["verdict"].startswith("KEEP") and "slipping" in k["verdict"], k["verdict"])

# ══ 4. Operator pieces ════════════════════════════════════════════════
check("commands parsed; anything else ignored", OP.parse_command("/lead L1 call") == ("lead", ["L1", "call"])
      and OP.parse_command("/order@OneZeroDeals_bot P1 2 499") == ("order", ["P1", "2", "499"]) and OP.parse_command("hello") == (None, []))
feed = {"events": [{"k": "ev:2026-11-22T10:00:00Z:b", "text": "/lead L1 call"}, {"k": "ev:2026-11-22T09:00:00Z:a", "text": "/money earnkaro 2 90"}],
        "uploads": [{"k": "up:2026-11-22T11:00:00Z:c", "name": "r.csv", "caption": "", "text": "x"}]}
ev, up = OP.new_items(feed, {"ev:2026-11-22T10:00:00Z:b"})
check("queue: already-handled keys are skipped; oldest first", [e["k"] for e in ev] == ["ev:2026-11-22T09:00:00Z:a"] and len(up) == 1)
check("report dates: from the file, the caption, else the 7 days to yesterday",
      OP.report_range("Fee-Earnings from 2026-11-01 to 2026-11-07\n", "", "2026-11-22") == ("2026-11-01", "2026-11-07")
      and OP.report_range("", "2026-11-16 to 2026-11-22", "2026-11-23") == ("2026-11-16", "2026-11-22")
      and OP.report_range("", "", "2026-11-23") == ("2026-11-16", "2026-11-22"))
tags = T.Tags("onezeronetwor-21", "telegram=oztg-21, telegram.durable=oztgdur-21, whatsapp=ozwa-21")
csv_ = ("Fee-Tracking Summary from 2026-11-16 to 2026-11-22\nTracking Id,Clicks,Items Ordered,Items Shipped,Ad Fees\n"
        "oztg-21,300,12,11,180.50\noztgdur-21,280,11,10,\"1,210.00\"\nozwa-21,40,1,1,15\n")
rows = OP.parse_upload(csv_, "", "2026-11-23", tags, growth.parse_assoc)
byc = {r["channel"]: r for r in rows}
check("upload: an Associates report with a title line is read; ₹1,210.00 with a comma parses; dates from the file",
      byc["telegram.durable"]["earnings"] == 1210.0 and byc["telegram"]["clicks"] == 300 and rows[0]["from"] == "2026-11-16", rows)
check("upload: an unreadable file gives no rows (the Operator says so)", OP.parse_upload("hello\nworld", "", "2026-11-23", tags, growth.parse_assoc) == [])
money = OP.add_money(None, rows)
money = OP.add_money(money, rows)
check("money: re-sending the same report does not double it", len(money) == 3)
mr, rep = OP.money_command(["earnkaro", "2", "90"], "2026-11-23")
money = OP.add_money(money, mr)
check("/money logs a programme with no report file", "₹90" in rep and len(money) == 4)
plog = pd.DataFrame([{"posted_at": f"2026-11-{16 + i % 7:02d} 11:00", "channel": "telegram", "arm": "durable" if i % 2 else "momentum"}
                     for i in range(46)])
v, d = OP.arm_compare(money, plog, tags.map)
check("arms: per-post earnings once both arms have 10 orders; durable earns more here → holds", v.startswith("Durable holds on earnings per post"), (v, d))
v2, _ = OP.arm_compare(money, plog.head(20), tags.map)
check("arms: collecting until each arm has 20 posts in the report's dates", v2.startswith("Collecting"), v2)
v3, d3 = OP.arm_compare(money, plog, {"telegram": "oztg-21"})
check("arms: without telegram.durable in AFF_TAGS it says what to add", "Arms not separable" in v3 and "telegram.durable" in d3)
tot, gl = OP.goal_line(money, 1999, 1, 50000)
aff_expected = (180.5 + 1210 + 15) * 30 / 7 + 90 * 30 / 7
check("goal: affiliate run-rate from the latest report + EarnKaro + seller MRR, against ₹50,000",
      abs(tot - (aff_expected + 1999)) < 1 and "of ₹50,000" in gl and "₹999 pilot" in gl, (tot, gl))
L = pd.DataFrame([{"date": f"2026-11-{d:02d}", "metric": "members", "key": "telegram", "value": str(v_)}
                  for d, v_ in zip(range(14, 24), [100, 104, 110, 112, 115, 120, 121, 119, 117, 115])]
                 + [{"date": "2026-11-23", "metric": "joins", "key": "tg-forward", "value": "3"}])
a = OP.audience(L, growth.series)
check("audience: members, today, 7 days, joins by source, and 'fell 3 nights running'",
      a["now"] == 115 and a["d1"] == -2 and a["d7"] == 115 - 110 and a["joins"] == {"tg-forward": 3} and a["falling3"], a)
ctx = {"today": "2026-11-23", "audience": a, "posts_today": {"durable": 5, "momentum": 2}, "bot_users": 4, "x_line": "X: 3 posts this month, $0.05",
       "goal_line": gl, "arms": (v, d), "tier": vendor.tier(mk(15, 2)), "subs": 1, "pay_lines": ["💳 Kobo: ₹999 pilot link ready → https://rzp.io/i/1"],
       "pilots": [p1], "processed": ["/lead L1 call → Kobo: call."], "notes": ["⚠️ Could not read r.csv: send the tracking-ID summary report as CSV"],
       "crm": crm.assign(status="new"), "report_age": 10}
F = OP.floor(ctx, esc)
check("Live Floor: every section present, under one Telegram message", all(k in F for k in ("Live Floor", "📺", "💰", "🏪", "📦", "✅ <b>Today</b>", "🧾"))
      and len(F) < 3900, F)
td = OP.todos(ctx)
check("to-dos: the payment step, the hook problem, the stale report, the pilot STOP, the member fall, the file problem — max 6",
      any("pilot link" in t for t in td) and any("hook problem" in t for t in td)
      and any("Associates" in t for t in td) and len(td) <= 6, td)
check("to-dos: a hook problem stops the 'send outreach' to-do (fix the message first)", not any(t.startswith("Send ") and "outreach" in t for t in td), td)
td_ok = OP.todos(dict(ctx, tier=vendor.tier(mk(5, 1))))
check("to-dos: outreach comes first when the hook is not the problem", td_ok[0].startswith("Send ") and "outreach" in td_ok[0], td_ok)
check("to-dos: nothing to do → says so", "Nothing needs you today" in OP.floor({"today": "x", "crm": pd.DataFrame(columns=vendor.CRM_COLS), "report_age": 1}, esc))
LM = OP.leads_message(crm.assign(status="new"), {"sports": b}, vendor.outreach, vendor.contact_links, esc)
check("/leads: each new lead with its id, a copyable message and where to find the contact", "<pre>" in LM and lid in LM and "Find them" in LM and "amazon.in/s?k=" in LM)

# ══ 5. end to end: operator + vendor runs ═════════════════════════════
TMP = tempfile.mkdtemp()
tf.LOCAL_OUT_DIR = TMP
SIG.assign(compliance="").to_csv(os.path.join(TMP, "TREND_SIGNALS.csv"), index=False)
H = pd.DataFrame([{"date": f"2026-11-{d:02d}", "list": "bestsellers", "category_slug": "sports", "asin": a_, "rank": i + 1,
                   "price_inr": 499, "rating": 4, "reviews": 10, "title": t_}
                  for d in range(10, 24) for i, (a_, t_) in enumerate(zip(SIG["asin"], SIG["title"]))])
H.to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
sent, docs, pushed = [], [], []
tf.send_telegram = lambda m: sent.append(m)
tf.send_telegram_document = lambda p, c="", ctype=None: docs.append((p, c))
tf.TELEGRAM_CHAT_ID, tf.TELEGRAM_BOT_TOKEN = "ADMIN", "tok"
tf.AFFILIATE_CHAT_ID, tf.AFFILIATE_BOT_TOKEN, tf.AFFILIATE_TAG, tf.AFFILIATE_SHEET_ID = "", "", "onezeronetwor-21", ""
tf.CATEGORIES = dict(tf.CATEGORIES, sports="Sports, Fitness & Outdoors")
os.environ["AFF_TAGS"] = "telegram=oztg-21, telegram.durable=oztgdur-21"
def run_at(dstr, hour, fn):
    tf.NOW_IST = datetime.strptime(dstr, "%Y-%m-%d").replace(hour=hour, minute=5, tzinfo=IST)
    tf.TODAY = dstr
    fn()

tf.worker_push = lambda **kw: (pushed.append(kw), True)[1]
tf.worker_get = lambda path: None
run_at("2026-11-23", 8, tf.main_vendor)                       # Monday
crm_e = pd.read_csv(os.path.join(TMP, "VENDOR_CRM.csv"), dtype=str).fillna("")
check("vendor run: leads found and written to VENDOR_CRM as 'new' (no big brand)", len(crm_e) >= 2 and set(crm_e["status"]) == {"new"}
      and "Nivia" not in set(crm_e["brand"]), crm_e[["brand", "kind"]].to_dict("records"))
check("vendor run: a sample Radar per lead category comes to you as a file", any("radar_sample_sports" in p for p, _ in docs))
check("vendor run: /leads text pushed to the bot and sent to you", pushed and "leads" in pushed[-1] and "<pre>" in pushed[-1]["leads"]
      and any("sellers to contact" in m for m in sent))
check("vendor run: drop-ship shortlist sent and kept in DS_SHORTLIST", any("Drop-ship shortlist" in m for m in sent)
      and os.path.exists(os.path.join(TMP, "DS_SHORTLIST.csv")))
first = crm_e.iloc[0]["lead_id"]
feed = {"events": [{"k": "ev:2026-11-23T14:00:00Z:a", "text": f"/lead {first} contacted"},
                   {"k": "ev:2026-11-23T15:00:00Z:b", "text": f"/lead {first} email buyer@kobo.in"},
                   {"k": "ev:2026-11-23T16:00:00Z:c", "text": f"/lead {first} call"},
                   {"k": "ev:2026-11-23T16:30:00Z:d", "text": "/pilot add B0GRIP0001 120"},
                   {"k": "ev:2026-11-23T16:40:00Z:e", "text": "/order P1 3 499"}],
        "uploads": [{"k": "up:2026-11-23T12:00:00Z:f", "name": "fees.csv", "caption": "", "text": csv_}]}
tf.worker_get = lambda path: feed if path == "/events" else None
calls.clear(); rz_state["s"] = "created"
os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test", "sec"
_orig = vendor.Razorpay
vendor.Razorpay = lambda *a, **k: _orig("rzp_test", "sec", http=rz_http)
sent.clear(); pushed.clear()
run_at("2026-11-23", 22, tf.main_operator)
crm_e = pd.read_csv(os.path.join(TMP, "VENDOR_CRM.csv"), dtype=str).fillna("")
row = crm_e[crm_e["lead_id"] == first].iloc[0]
check("operator: bot commands applied in order — contacted, email, call → a ₹999 link made the same night",
      row["email"] == "buyer@kobo.in" and row["status"] == "link_sent" and row["link_url"].startswith("https://rzp.io"), row.to_dict())
check("operator: the pilot and its order are recorded", pd.read_csv(os.path.join(TMP, "PILOT_ORDERS.csv")).iloc[0]["qty"] == 3)
M = pd.read_csv(os.path.join(TMP, "MONEY.csv"))
check("operator: the uploaded Associates report lands in MONEY by tracking ID", set(M["channel"]) >= {"telegram", "telegram.durable"})
floor_msgs = [m for m in sent if "Live Floor" in m]
check("operator: ONE Live Floor report to you, with what was done since last night", len(floor_msgs) == 1 and "Done since last night" in floor_msgs[0]
      and "pilot link ready" in floor_msgs[0], floor_msgs[:1])
check("operator: Live Floor and /leads pushed to the bot", pushed and "floor" in pushed[-1] and "Live Floor" in pushed[-1]["floor"])
check("operator: Monday → the kill-line review goes out too", any("Kill lines" in m for m in sent))
done = pd.read_csv(os.path.join(TMP, "OPERATOR_DONE.csv"))
check("operator: every command and upload marked done", len(done) == 6)
sent.clear()
n_posts = len([c for c in calls if c[0] == "POST"])
run_at("2026-11-24", 22, tf.main_operator)
crm_e = pd.read_csv(os.path.join(TMP, "VENDOR_CRM.csv"), dtype=str).fillna("")
check("operator: a second night does not re-apply old commands or make a second link",
      len([c for c in calls if c[0] == "POST"]) == n_posts and crm_e[crm_e["lead_id"] == first].iloc[0]["status"] == "link_sent"
      and len(pd.read_csv(os.path.join(TMP, "PILOT_ORDERS.csv"))) == 1)
rz_state["s"] = "paid"
run_at("2026-11-25", 22, tf.main_operator)
crm_e = pd.read_csv(os.path.join(TMP, "VENDOR_CRM.csv"), dtype=str).fillna("")
check("operator: payment seen → pilot until +30 days", crm_e[crm_e["lead_id"] == first].iloc[0]["status"] == "pilot"
      and crm_e[crm_e["lead_id"] == first].iloc[0]["expires"] == "2026-12-25")
FL = pd.read_csv(os.path.join(TMP, "FLOOR_LOG.csv"))
check("operator: FLOOR_LOG keeps one row a day (history without a spreadsheet to fill)", list(FL["date"]) == ["2026-11-23", "2026-11-24", "2026-11-25"])
mail = []
vendor.smtp_ready = lambda: True
vendor.send_email = lambda to, subj, page, text="": mail.append((to, subj, page))
docs.clear()
run_at("2026-11-30", 8, tf.main_vendor)
check("vendor run: the paying seller gets their category Radar by email", mail and mail[0][0] == "buyer@kobo.in"
      and "Sports, Fitness" in mail[0][1] and "<html" in mail[0][2], mail[:1])
vendor.Razorpay = _orig

check("VERSION is 1.70 or later", tuple(int(x) for x in tf.VERSION.split(".")) >= (1, 70))
here = os.path.dirname(os.path.abspath(__file__))
wfs = {n: open(os.path.join(here, ".github", "workflows", n)).read() for n in ("operator.yml", "vendor.yml", "growth.yml")}
check("workflows: operator nightly 22:00 IST + on demand; vendor Mondays; growth.yml no longer scheduled",
      'cron: "30 16 * * *"' in wfs["operator.yml"] and re.search(r"BR_MODE:\s+operator", wfs["operator.yml"])
      and 'cron: "0 3 * * 1"' in wfs["vendor.yml"] and re.search(r"BR_MODE:\s+vendor", wfs["vendor.yml"])
      and "schedule:" not in wfs["growth.yml"])
check("workflows: payment and email secrets are optional and passed through", all(k in wfs["operator.yml"] for k in ("RAZORPAY_KEY_ID", "SMTP_HOST", "WORKER_URL")))
print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
