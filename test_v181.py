"""v1.81: calculator on 2026 fees (weight fee, unit check, brand triage), cleaner signals, legacy tabs hidden, owner
to-dos, seller hints in the bot index, the cross-promo kit, and the bot's /margin agreeing with fees.py to the rupee."""
import glob, json, os, shutil, subprocess, sys, tempfile
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
os.environ.pop("AMZ_FEES", None)
import trend_fetcher as tf
import calculator as C, fees, growth, operator_agent as OP, radar_index

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info)[:700] if not ok else ""))
    if not ok: FAILS.append(name)

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()

# ══ calculator: the 2026 fee block and what the formulas do with it ═══════
fb = C.fee_block()
check("fee block carries fees.py's closing bands and the Easy Ship weight table",
      [r[1] for r in fb[2:6]] == [fees.DEFAULTS[k] for k in ("closing_0_300", "closing_301_500", "closing_501_1000", "closing_above_1000")]
      and [r[1] for r in fb[8:13]] == list(fees.SHIP_TABLE["easy_ship"]) and "ASSUMED" in fb[4][2], fb[2:13])

def sig(asin, price, title, cat="kitchen", gate=""):
    return {"asin": asin, "title": title, "category_slug": cat, "trend_score": 50, "signal": "🧱 Durable", "price_inr": price,
            "seller_offers": 4, "bought_past_month_min": 500, "compliance": "not flagged", "competition": "Moderate",
            "url": f"https://amazon.in/dp/{asin}", "stock_gate": gate}
path = os.path.join(TMP, "CALCULATOR.xlsx")
C.update_xlsx(path, [sig("P1", 549, "Yoga Mat 6mm Anti Skid"), sig("P2", 1299, "Hex Dumbbell 2 kg", "sports"),
                     sig("P3", 299, "Steel Water Bottle 500 g"), sig("P4", 450, "Lunch Box 3 Containers"),
                     sig("P5", 499, "Herbal Shampoo 340 ml", gate="NO — brand-led listing")], today="2026-11-25")
import openpyxl
wb = openpyxl.load_workbook(path); ws = wb[C.CALC_TAB]
rowof = {ws[f"A{r}"].value: r for r in range(2, ws.max_row + 1)}
check("weight read from the title into its own column", ws[f"{C.L['weight']}{rowof['P2']}"].value == 2
      and ws[f"{C.L['weight']}{rowof['P3']}"].value == 0.5 and ws[f"{C.L['weight']}{rowof['P1']}"].value in ("", None))
for a, cost in (("P1", 150), ("P4", 2), ("P5", 100)):           # P4: ₹2 'per piece' against ₹450 — a unit mistake
    ws[f"{C.L['ind_cost']}{rowof[a]}"] = cost
wb.save(path)
soffice = shutil.which("soffice")
if soffice:
    out = os.path.join(TMP, "calc")
    subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", out, path], capture_output=True, timeout=180)
    try:
        v = openpyxl.load_workbook(os.path.join(out, "CALCULATOR.xlsx"), data_only=True)[C.CALC_TAB]
        g = lambda a, k: v[f"{C.L[k]}{rowof[a]}"].value
        check("referral 0% below ₹1,000; the category rate at ₹1,299", g("P1", "referral") == 0 and g("P4", "referral") == 0
              and (g("P2", "referral") or 0) > 0, [g(a, "referral") for a in ("P1", "P2", "P4")])
        check("closing fee by 2026 band: ₹27 ≤300 · ₹23 ≤500 · ₹45 ≤1,000 · ₹75 above",
              [g(a, "closing") for a in ("P3", "P4", "P1", "P2")] == [27, 23, 45, 75], [g(a, "closing") for a in ("P3", "P4", "P1", "P2")])
        check("Easy Ship weight fee when the title has a weight (2 kg → ₹112, 500 g → ₹55); category default otherwise",
              g("P2", "ship") == 112 and g("P3", "ship") == 55 and (g("P1", "ship") or 0) > 0, [g(a, "ship") for a in ("P1", "P2", "P3")])
        check("indicative margin computed for a sane cost", isinstance(g("P1", "est_margin"), (int, float)) and g("P1", "est_verdict"),
              (g("P1", "est_margin"), g("P1", "est_verdict")))
        check("a cost under 10% of price gets no margin and a 'check the unit' triage", g("P4", "est_margin") in ("", None)
              and str(g("P4", "est_verdict")).startswith("Check the unit"), (g("P4", "est_margin"), g("P4", "est_verdict")))
        check("a brand-led / NO stock gate gets no triage at all", g("P5", "est_verdict") in ("", None), g("P5", "est_verdict"))
        errs = [(r, c.value) for row in v.iter_rows() for c in row for r in [c.coordinate] if isinstance(c.value, str) and c.value.startswith("#")]
        check("no formula errors after recalculation", not errs, errs[:5])
    except Exception as exc:
        check("calculator recalculated in LibreOffice", False, exc)
else:
    print("SKIP LibreOffice recalculation (soffice not installed)")

# ══ signals: rising searches, falling labels ═══════════════════════════
check("a rising search must share a word with its seed ('yoga mats for women' yes, 'samsung smart tv' no)",
      tf.related_to_seed("yoga mats for women", "yoga mat") and not tf.related_to_seed("samsung smart tv", "yoga mat")
      and not tf.related_to_seed("levis jeans", "yoga mat") and tf.related_to_seed("air fryer 5l", "air fryers"))

# ══ legacy tabs: hidden only while they still hold the template rows ═══
check("template rows are placeholders; a typed number is not", tf.Store.is_placeholder([["date", "amount"], ["yyyy-mm-dd", "type your rate"]])
      and not tf.Store.is_placeholder([["date", "amount"], ["2026-11-20", "450"]])
      and not tf.Store.is_placeholder([["date", "amount"], ["example", "450"]]))
class WS:
    def __init__(self, i, vals, hidden=False): self.id, self._vals, self._properties = i, vals, {"hidden": hidden}
    def get_all_values(self): return self._vals
class WB:
    def __init__(self, tabs): self.tabs, self.req = tabs, []
    def worksheet(self, n):
        if n not in self.tabs: raise KeyError(n)
        return self.tabs[n]
    def batch_update(self, body): self.req.append(body)
st = object.__new__(tf.Store)
st.wb = WB({"AFF_ACTUALS": WS(1, [["h"], ["example row — replace"]]), "COMMISSION": WS(2, [["h", "v"], ["electronics", "0.04"]]),
            "WAITLIST": WS(3, [["h"]], hidden=True)})
hid = st.hide_placeholder_tabs()
check("only the untouched template tab is hidden; your data and already-hidden tabs are left alone",
      hid == ["AFF_ACTUALS"] and len(st.wb.req) == 1
      and st.wb.req[0]["requests"][0]["updateSheetProperties"]["properties"] == {"sheetId": 1, "hidden": True}, (hid, st.wb.req))

# ══ owner to-dos ═══════════════════════════════════════════════════════
td = OP.todos({"tags_missing": ["telegram.durable", "whatsapp"], "report_age": 2, "audience": {"falling3": True}})
check("missing tracking IDs become a to-do that names them", any("tracking IDs" in t and "telegram.durable, whatsapp" in t for t in td), td)
check("a 3-night member fall no longer tells you to cut posts (your call)", not any("drop one posting slot" in t for t in td)
      and any("posting frequency stays as you set it" in t for t in td), td)

# ══ bot index: seller hints ════════════════════════════════════════════
days = pd.date_range("2026-11-12", "2026-11-25").strftime("%Y-%m-%d")
H = pd.DataFrame([{"list": "bestsellers", "asin": a, "category_slug": "toys", "date": d, "rank": r, "title": t}
                  for d in days for a, r, t in (("B0TOY00001", 3, "Kids Ride-On Car 2.5 kg"), ("B0TOY00002", 9, "Puzzle Set"))])
S = pd.DataFrame([{"asin": "B0TOY00001", "compliance": "REVIEW: BIS (toys QCO) — IS 9873; check age grading"},
                  {"asin": "B0TOY00002", "compliance": "not flagged"}])
ix = radar_index.build(S, H, days[-1], {"toys": "Toys"}, version="1.81")
check("index carries a short certification flag and the title weight for /sell", ix["items"]["B0TOY00001"].get("k") == "BIS (toys QCO)"
      and ix["items"]["B0TOY00001"].get("g") == 2.5 and "k" not in ix["items"]["B0TOY00002"] and "g" not in ix["items"]["B0TOY00002"],
      {a: {k: v for k, v in it.items() if k in "kg"} for a, it in ix["items"].items()})

# ══ cross-promo kit ════════════════════════════════════════════════════
led = growth.add_rows(None, [("2026-11-24", "members", "telegram", 13), ("2026-11-24", "joins", "swap-1", 2), ("2026-11-25", "joins", "swap-1", 1)])
kit = growth.promo_kit({"swap-1": "https://t.me/+s1", "swap-2": "https://t.me/+s2"}, led, "@onezerodeals")
check("kit: DM with our size, a post per tracked swap link with its joins, our post labelled a swap",
      "(13 members)" in kit and "https://t.me/+s1" in kit and "joins so far: 3" in kit and "joins so far: 0" in kit
      and "Collab (channel swap)" in kit and "material connection" in kit and "no Amazon product images" in kit, kit)
check("kit never promises a posting frequency or buying members", "a day" not in kit.split("Rules")[0] and "Never pay for members" in kit)
check("kit without swap links says how they get made", "No swap links yet" in growth.promo_kit({}, led, "@x"))

# ══ the bot's /margin = fees.economics (same fees, same rounding inputs) ══
grid = [(p, c, k, d) for p in (199, 299, 450, 549, 999, 1000, 1499) for c in (None, 150) for k in (None, 0.3, 1.2, 3.5, 7.0)
        for d in (None, (30, 20, 10))]
js = """
import { marginCalc } from %s;
const grid = JSON.parse(process.argv[1]);
console.log(JSON.stringify(grid.map(([p, c, k, d]) => marginCalc(p, c, k, d))));
""" % json.dumps("file://" + os.path.join(HERE, "cloudflare_worker", "worker.js"))
node = shutil.which("node")
if node:
    r = subprocess.run([node, "--input-type=module", "-e", js, json.dumps([[p, c, k, list(d) if d else None] for p, c, k, d in grid])],
                       capture_output=True, text=True, timeout=60)
    try:
        got = json.loads(r.stdout)
        bad = []
        for (p, c, k, d), m in zip(grid, got):
            e = fees.economics(p, cost=c, kg=k, dims=d)
            pairs = [("fees", e["fees"], m["fees"]), ("ship", e["ship"], m.get("ship")),
                     ("max_cost_before_ship", e["max_cost_before_ship"], m["maxCostBeforeShip"]),
                     ("max_cost", e["max_cost"], m.get("maxCost")), ("profit", e.get("profit"), m.get("profit")),
                     ("margin", e.get("margin"), m.get("margin"))]
            for name, a, b in pairs:
                if (a is None) != (b is None) or (a is not None and abs(a - b) > (0.0001 if name == "margin" else 0.01)):
                    bad.append((p, c, k, d, name, a, b))
        check(f"bot /margin agrees with fees.economics on {len(grid)} cases (fees, ship, max cost, profit, margin)", not bad, bad[:5])
    except Exception as exc:
        check("bot /margin parity", False, (exc, r.stderr[-400:]))
else:
    print("SKIP JS parity (node not installed)")

# ══ the outgoing-post check: the last gate before a public chat ═════════
import compliance as K, picks as P, content, savings
good = '🔍 <b>T</b> — 26 Nov · <i>#ad</i>\n\n1. <a href="https://www.amazon.in/dp/B0X?tag=a-21">Mat</a> · 14 days on the list'
check("post check: a labelled, price-free post passes", K.post_check(good) == [], K.post_check(good))
check("post check: label only at the end is not upfront", any("upfront" in p for p in K.post_check(
      '<a href="https://www.amazon.in/dp/B0X?tag=a-21">Mat</a>\n\n<i>#ad · As an Amazon Associate</i>')))
check("post check: ₹ next to Amazon links, link shorteners, 'only 3 left' / 'lowest' are each caught",
      any("price" in p for p in K.post_check('#ad 3 picks under ₹999 <a href="https://amzn.to/x">a</a>'))
      and any("shortener" in p for p in K.post_check('#ad <a href="https://bit.ly/x">a</a>'))
      and any("urgency" in p for p in K.post_check('#ad Only 3 left <a href="https://amzn.to/x">a</a>'))
      and any("urgency" in p for p in K.post_check('#ad Lowest price ever <a href="https://amzn.to/x">a</a>')))
check("post check: ₹ in an EarnKaro offer is allowed (Amazon's price rule covers Amazon links only)",
      K.post_check(savings.message({"kind": "savings", "text": "Ajio jackets from ₹499", "url": "https://ekaro.in/x", "card": ""})) == [])
sig_r = pd.DataFrame([{"asin": "B0TOY00001", "title": "Kids Ride-On Car", "category_slug": "toys", "durable": "True",
                       "durable_days": 14, "durable_window": 14, "durable_score": 90}])
ru = content.roundup(sig_r, lambda r: "t-21", P.affiliate_link, {"toys": "Toys"}, "Amazon Hidden Bestsellers", disclosure=P.DISCLOSURE)
pp = P.post_messages(sig_r, H, "t-21", days[-1], cat_names={"toys": "Toys"}, week_label="26 Nov", style="per_product")
sg = P.post_messages(sig_r, H, "t-21", days[-1], cat_names={"toys": "Toys"}, week_label="26 Nov", style="single")
check("every generator's public post passes the check (roundup, per-product, single)",
      ru and all(K.post_check(m) == [] for m in [ru] + pp + sg), [K.post_check(m) for m in [ru] + pp + sg])
calls = []
class R:
    status_code, text = 200, "{}"
old = (tf._tg_post, tf.TELEGRAM_BOT_TOKEN, tf.TELEGRAM_CHAT_ID, tf.AFFILIATE_CHAT_ID, os.environ.get("TELEGRAM_CHAT_ID"), os.environ.get("TELEGRAM_BOT_TOKEN"))
tf._tg_post = lambda url, **kw: calls.append((url, kw.get("data", {}))) or R()
os.environ["TELEGRAM_CHAT_ID"], os.environ["TELEGRAM_BOT_TOKEN"] = "111", "OWN"
tf.TELEGRAM_BOT_TOKEN, tf.TELEGRAM_CHAT_ID, tf.AFFILIATE_CHAT_ID = "PUB", "@pub", "@pub"
held = tf.send_telegram('<a href="https://www.amazon.in/dp/B0X?tag=a-21">Mat</a> only ₹299!')
tf.send_telegram(good)
tf.TELEGRAM_CHAT_ID = "111"
tf.send_telegram('Digest: <a href="https://www.amazon.in/dp/B0X">x</a> ₹549 · lowest seen')
check("gate: a bad public post is held and the owner told why; a good one goes out; your own digests are never held",
      held is False and calls[0][1]["chat_id"] == "111" and "Held a channel post" in calls[0][1]["text"] and "OWN" in calls[0][0]
      and calls[1][1]["chat_id"] == "@pub" and calls[2][1]["chat_id"] == "111" and "₹549" in calls[2][1]["text"] and len(calls) == 3,
      [(u[-20:], d.get("chat_id"), d.get("text", "")[:60]) for u, d in calls])
tf._tg_post, tf.TELEGRAM_BOT_TOKEN, tf.TELEGRAM_CHAT_ID, tf.AFFILIATE_CHAT_ID = old[:4]
for k, v in (("TELEGRAM_CHAT_ID", old[4]), ("TELEGRAM_BOT_TOKEN", old[5])):
    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

check("version is 1.81 or later", float(tf.VERSION) >= 1.81, tf.VERSION)
print(f"\n{len(FAILS)} FAILED" if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)
