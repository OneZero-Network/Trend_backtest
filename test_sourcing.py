#!/usr/bin/env python3
"""Offline tests for the IndiaMART sourcing run (fixtures are representative, NOT captured
from a live IndiaMART page — the first live run's debug pages should replace them)."""
import os, sys, json, glob, shutil, subprocess, tempfile
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update({"BR_OUTPUT_DIR": TMP, "BR_CATEGORIES": "kitchen"})
import openpyxl
import sourcing as S, calculator as C, trend_fetcher as tf
ok = True
def check(n, c, d=""):
    global ok; ok &= bool(c); print(f"{'PASS' if c else 'FAIL'}  {n}  {d}")

# ── query
q = S.make_query("Desidiya 200 LED Waterfall Curtain String Lights 3x2m Warm White | 8 Lighting Modes")
check("brand dropped, product words kept", q == "led waterfall curtain string", q)
check("override wins", S.make_query("Anything", "steel lunch box 3 compartment") == "steel lunch box 3 compartment")
check("brand-only titles don't crash", S.make_query("Skybags") != None)

# ── units
check("piece kept", S.per_piece(120, "piece", "LED light")[0] == 120)
check("dozen ÷ 12", S.per_piece(1200, "dozen", "LED light")[0] == 100)
check("pack of 6 ÷ 6", S.per_piece(600, "piece", "Steel bowl pack of 6")[0] == 100)
check("kg dropped (not comparable)", S.per_piece(450, "kg", "LED light")[0] is None)
check("box without count dropped", S.per_piece(2000, "box", "LED light")[0] is None)
check("relevance overlap", S.relevance("led waterfall curtain string", "Waterfall LED Curtain String Light 3x2") == 1.0
      and S.relevance("led waterfall curtain string", "Cotton Door Curtain") == .25)

# ── parsing: card layout (representative)
def card(title, price_txt, extra=""):
    return (f'<div class="card"><a href="https://www.indiamart.com/proddetail/{abs(hash(title))}.html">{title}</a>'
            f'<span class="price">{price_txt}</span>{extra}<div class="companyname">Supplier {title[:5]}</div>'
            f'<span class="city">Delhi</span></div>')
HTML = "<html><body>" + "".join([
    card("LED Waterfall Curtain String Light", "₹ 95 / Piece", "<span>Minimum Order Quantity: 100 Piece</span>"),
    card("Warm White LED Waterfall Curtain String Lights 3x2", "₹ 110/Piece"),
    card("LED Curtain String Waterfall Light", "₹ 1,320 / Dozen"),
    card("Waterfall Curtain LED String Light Decorative", "₹ 125 / Piece"),
    card("LED Waterfall String Curtain Light Premium", "₹ 140 / Piece"),
    card("Cotton Door Curtain Plain", "₹ 80 / Piece"),                         # irrelevant
    card("LED Waterfall Curtain String Light Roll", "₹ 450 / Kg"),             # wrong unit
    card("LED Waterfall Curtain String Commercial", "₹ 2,999 / Piece"),        # above Amazon price
    '<div class="card"><a href="https://www.indiamart.com/proddetail/9.html">LED Waterfall Curtain</a>'
    '<span>Get Latest Price</span></div>',                                     # price on request
]) + "</body></html>"
L = S.parse_search(HTML)
check("cards parsed (price-on-request skipped)", len(L) == 8, f"{len(L)} listings")
check("MOQ + unit extracted", L[0]["moq"] == "100 Piece" and L[2]["unit"] == "dozen", f"{L[0]['moq']} / {L[2]['unit']}")
E = S.estimate(q, L, amazon_price=499)
kept = sorted(d["per_piece"] for d in E["detail"] if d["kept"])
check("only comparable, relevant, below-Amazon prices kept", kept == [95, 110, 110, 125, 140], str(kept))
check("estimate = median 110, with range and confidence", E["ind_cost"] == 110 and E["ind_low"] and E["confidence"] in ("medium", "low", "high"),
      f"₹{E['ind_cost']} ({E['ind_low']}–{E['ind_high']}, {E['confidence']})")
check("too few matches → no estimate", S.estimate(q, L[:2], 499)["ind_cost"] is None)

# ── parsing: JSON-LD layout
LD = {"@type": "ItemList", "itemListElement": [{"item": {"@type": "Product", "name": f"LED Waterfall Curtain String Light {i}",
      "url": f"https://www.indiamart.com/proddetail/{i}.html", "offers": {"price": str(100 + i * 5),
      "priceSpecification": {"unitText": "Piece"}, "seller": {"name": f"S{i}"}}}} for i in range(5)]}
L2 = S.parse_search(f'<script type="application/ld+json">{json.dumps(LD)}</script>')
check("JSON-LD listings parsed", len(L2) == 5 and L2[0]["unit"] == "piece")

# ── end to end (local mode): calculator → sourcing run → indicative columns, quote untouched
calc = os.path.join(TMP, "CALCULATOR.xlsx")
base = {"category_slug": "kitchen", "signal": "📈", "compliance": "NOT FLAGGED (≠ cleared — verify)", "url": "u"}
C.update_xlsx(calc, [{**base, "asin": "B0LIGHTS01", "title": "Desidiya 200 LED Waterfall Curtain String Lights 3x2m",
                      "trend_score": 80, "price_inr": 499},
                     {**base, "asin": "B0QUOTED01", "title": "Generic Steel Lunch Box", "trend_score": 70, "price_inr": 399},
                     {**base, "asin": "B0NOMATCH1", "title": "Zzz Unobtainium Gadget Thing", "trend_score": 60, "price_inr": 999}],
              today="2026-11-01")
wb = openpyxl.load_workbook(calc); ws = wb[C.CALC_TAB]
for r in range(2, ws.max_row + 1):
    if ws[f"A{r}"].value == "B0QUOTED01":
        ws[f"{C.L['sourcing']}{r}"] = 150                      # user already has a real quote
wb.save(calc)
calls = []
def fake_fetch(sb, url, retries=3):
    calls.append(url)
    return HTML if "waterfall" in url else "<html><body>No results</body></html>"
tf.fetch_html, tf.new_session = fake_fetch, (lambda: None)
tf.MIN_GAP_SEC = 0; tf.TODAY = "2026-11-01"; tf.LOCAL_OUT_DIR = TMP
msgs = []; tf.send_telegram = msgs.append
tf.main_sourcing()
rows = {r["asin"]: r for r in C.read_xlsx(calc)}
check("row with your quote NOT searched", not any("lunch" in c for c in calls), str(calls))
check("quote cell untouched", rows["B0QUOTED01"]["sourcing"] == 150)
check("indicative cost filled (not the quote cell)", rows["B0LIGHTS01"]["ind_cost"] == 110
      and not C.has_quote(rows["B0LIGHTS01"]), str({k: rows["B0LIGHTS01"][k] for k in ("ind_cost", "ind_conf", "sourcing")}))
check("no-match product: confidence 'none', no cost", rows["B0NOMATCH1"]["ind_cost"] in ("", None)
      and "none" in str(rows["B0NOMATCH1"]["ind_conf"]))
check("unparsed IndiaMART page saved for diagnosis", glob.glob(os.path.join(TMP, "debug", "indiamart_B0NOMATCH1*")))
check("Telegram says listed prices are not quotes", any("not quotes" in m for m in msgs))
calls.clear(); tf.main_sourcing()
check("re-run within 7 days does not search again", calls == [], str(calls))

skill = glob.glob("/root/.claude/skills/**/xlsx/scripts/recalc.py", recursive=True)
if skill:
    out = subprocess.run([sys.executable, skill[0], calc, "90"], capture_output=True, text=True).stdout
    rc = json.loads(out[out.index("{"):])
    check("recalc: zero formula errors", rc.get("total_errors") == 0, str(rc)[:120])
    v = openpyxl.load_workbook(calc, data_only=True)[C.CALC_TAB]
    g = {v[f"A{r}"].value: r for r in range(2, v.max_row + 1)}
    em = v[f"{C.L['est_margin']}{g['B0LIGHTS01']}"].value
    # hand: kitchen defaults defect .04, referral .10, closing 30, ship 80, rto .12, ads .08, gst .18, itc 1, ret 2
    u = 110 / .96; fees = 499 * .10 + 30; gst = 499 * .18 / 1.18 - u * .18; rto = .12 / .88 * 80 * 2
    hand = (499 - (u + fees + gst + 80 + rto + 499 * .08)) / 499
    check("indicative margin matches hand calculation", abs(em - hand) < 1e-6, f"sheet {em:.4f} vs hand {hand:.4f}")
    tv = v[f"{C.L['est_verdict']}{g['B0LIGHTS01']}"].value
    check("triage verdict shown", tv.startswith("Worth requesting a quote") == (hand >= .15), tv)
    check("real decision still waits for your quote", v[f"{C.L['decision']}{g['B0LIGHTS01']}"].value == "NEED SOURCING QUOTE")
    check("no-match row: blank triage", v[f"{C.L['est_verdict']}{g['B0NOMATCH1']}"].value in (None, ""))
shutil.rmtree(TMP)
# ── v1.21: "₹ ," (no digits) crashed the 9 Nov run — must be skipped, not fatal
_bad = ('<div><a href="/products/?id=1"><span class="line-clamp-2">Odd Item</span><p>₹ , / Piece</p></a></div>'
        '<div><a href="/products/?id=2"><span class="line-clamp-2">Good Item</span><p>₹ 120 / Piece</p></a></div>')
_r = S.parse_search(_bad)
check("price without digits is skipped, rest parsed", [x["title"] for x in _r] == ["Good Item"] and _r[0]["price"] == 120.0, _r)

# ── real page (9 Nov 2026): GitHub's US runners get export.indiamart.com, links are /products/?id=
_h = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_fixtures", "indiamart_export_football_2026-11-09.html"),
          encoding="utf-8", errors="ignore").read()
_L = S.parse_search(_h)
check("export-site page parsed (was 0 listings before v1.20)", len(_L) >= 20, len(_L))
check("export-site titles exclude the price text", all("₹" not in l["title"] for l in _L), [l["title"] for l in _L[:3]])
check("export-site links are absolute export URLs", all(l["url"].startswith("https://export.indiamart.com/products/") for l in _L))
_E = S.estimate("storm white moulded football", _L)
check("real page gives an estimate", _E["ind_cost"] is not None and _E["matches"] >= 3, (_E["matches"], _E["confidence"]))

print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
