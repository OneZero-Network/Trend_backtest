#!/usr/bin/env python3
"""Offline tests: compliance flags, calculator merge/preserve, Sheets path, YouTube check.
Recalculates with LibreOffice when available (xlsx skill's recalc.py or soffice)."""
import os, sys, glob, json, shutil, subprocess, tempfile
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openpyxl
import calculator as C, compliance as K, youtube_check as Y
ok = True
def check(n, c, d=""):
    global ok; ok &= bool(c); print(f"{'PASS' if c else 'FAIL'}  {n}  {d}")
TMP = tempfile.mkdtemp()

# ── compliance
check("toys category flags a wooden block set (no electronics keyword)",
      K.compliance_flags("Wooden Building Blocks Set 50 pcs", "toys").startswith("REVIEW"))
check("power bank flagged via keyword in any category", "BIS" in K.compliance_flags("20000mAh Power Bank", "sports"))
check("protein flagged FSSAI", "FSSAI" in K.compliance_flags("Whey Protein 1kg Chocolate", "sports"))
nf = K.compliance_flags("Stainless Steel Water Bottle 1L", "kitchen")
check("unflagged says NOT cleared", "≠ cleared" in nf, nf)
check("1 seller on bestseller → brand-owned note (not 'no demand')", "brand-owned" in K.competition_note(1, 5000))
check("12 sellers → crowded but demand-aware", "Crowded" in K.competition_note(12, 10000) and "10,000" in K.competition_note(12, 10000))

# ── regression: real titles from the first live run (Oct 31)
real = {
 # changed after the Oct 31 digest: decor lights peak for festivals even without the word "Diwali"
 "Desidiya 200 LED Waterfall Curtain String Lights 3x2m Warm White | 8 Lighting Modes": "Festive decor",
 "Kaku Fancy Dresses Halloween Costumes for Kids 3 to 8 Years": "Halloween",
 "FLYNGO 12 Diya Diwali Curtain String LED Lights for Decoration - Warm White": "Diwali",
 "Shreeji Creations Clay Diya|Traditional Clay Diya|Pack of 12": "Diwali",
 "Raj Fancy Raj Halloween Dress | Costume and Accessories | For Girls and Boys": "Halloween",
 "Skybags Brat Pro Max 35L Laptop Backpack (Upto 15.6\"), 3 Compartments": None,
}
bad = {t: K.seasonal_flag(t) for t, exp in real.items() if (exp is None) != (K.seasonal_flag(t) == "")
       or (exp and exp not in K.seasonal_flag(t))}
check("seasonal flag on real Diwali/Halloween/festive-decor titles, not on backpack", not bad, str(bad))
for t in ("Desidiya 200 LED Waterfall Curtain String Lights 3x2m Warm White | 8 Lighting Modes",
          "Festivora 35 Feet 8mm Big Size LED Pixel String Lights | Super Bright"):
    check(f"festive decor lights flagged seasonal: {t[:30]}…", "Festive decor" in K.seasonal_flag(t))
check("ordinary LED bulb / lunch box NOT seasonal", K.seasonal_flag("Philips 9W LED Bulb") == ""
      and K.seasonal_flag("Milton Steel Lunch Box 3 Containers") == "")
check("analog watch no longer flagged as electronics",
      "BIS CRS" not in K.compliance_flags("MOOVER Women's Analog Quartz Watch with Stainless Steel Strap", "watches"))
check("smart watch still flagged", "BIS CRS" in K.compliance_flags("Noise Smart Watch 1.8 inch", "watches"))
check("'tea light candles' not flagged FSSAI",
      "FSSAI" not in K.compliance_flags("FLYNGO 12 Pcs LED Tea Light Candles, Flameless", "kitchen"))
check("green tea still flagged FSSAI", "FSSAI" in K.compliance_flags("Organic Green Tea 100 bags", "grocery"))

# ── regressions from the live digest (Oct 31)
check("'Tap Extension for Kitchen Sink' NOT flagged as electronics",
      "BIS CRS" not in K.compliance_flags("Amazon Basics Flexible Tap Extension for Kitchen Sink with Universal Connector", "home-improvement"))
check("'Extension Board 4 socket' still flagged", "BIS CRS" in K.compliance_flags("Extension Board with 4 Sockets", "home-improvement"))

# ── calculator merge + preserve
def sig(asin, score, price, cat="kitchen", title=None, comp=None):
    return {"asin": asin, "title": title or f"Product {asin}", "category_slug": cat, "trend_score": score,
            "signal": "📈", "price_inr": price, "seller_offers": 4, "bought_past_month_min": 1000,
            "compliance": comp or K.NOT_FLAGGED, "competition": "Moderate", "url": f"https://amazon.in/dp/{asin}"}
path = os.path.join(TMP, "CALCULATOR.xlsx")
C.update_xlsx(path, [sig("A1", 80, 999), sig("A2", 70, 499, "electronics"), sig("A3", 60, 299, "toys",
                     comp="REVIEW: BIS (toys QCO)")], today="2026-10-01")
wb = openpyxl.load_workbook(path); ws = wb[C.CALC_TAB]
rowof = {ws[f"A{r}"].value: r for r in range(2, ws.max_row + 1)}
r2 = rowof["A2"]; r3 = rowof["A3"]; r1 = rowof["A1"]
ws[f"{C.L['sourcing']}{r2}"] = 150; ws[f"{C.L['moq']}{r2}"] = 300
ws[f"{C.L['referral']}{r2}"] = 0.05                     # user override of a default
ws[f"{C.L['notes']}{r2}"] = "Quote from Delhi supplier"
ws[f"{C.L['sourcing']}{r3}"] = 60
ws.append(["", "My manual idea", "garden", "", "", 799] + [""] * 5 + [300])   # manual row, no ASIN
wb[C.ASSUMP_TAB]["B3"] = 0.20                           # user edits a threshold
wb.save(path)
# next run: different order, new ASIN, A1 missing; 25 days later → A1 (no quote) must drop
C.update_xlsx(path, [sig("A9", 95, 1299, "beauty"), sig("A3", 65, 299, "toys", comp="REVIEW: BIS (toys QCO)")],
              today="2026-10-26")
rows = C.read_xlsx(path); by = {r["asin"]: r for r in rows if r["asin"]}
check("stale row without quote dropped", "A1" not in by)
check("row with your quote kept although absent today", "A2" in by)
check("your inputs preserved (quote, MOQ, override, notes)",
      by["A2"]["sourcing"] == 150 and by["A2"]["moq"] == 300 and by["A2"]["referral"] == 0.05
      and by["A2"]["notes"] == "Quote from Delhi supplier", str({k: by["A2"][k] for k in ("sourcing", "moq", "referral", "notes")}))
check("manual row (no ASIN) kept", any(r["product"] == "My manual idea" for r in rows))
check("assumption edit not overwritten", openpyxl.load_workbook(path)[C.ASSUMP_TAB]["B3"].value == 0.20)
check("rows with quotes sorted first", rows[0]["sourcing"] not in ("", None))

def recalc(p):
    skill = glob.glob("/root/.claude/skills/**/xlsx/scripts/recalc.py", recursive=True)
    if skill:
        out = subprocess.run([sys.executable, skill[0], p, "90"], capture_output=True, text=True).stdout
        return json.loads(out[out.index("{"):])
    if shutil.which("soffice"):
        subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", os.path.dirname(p), p],
                       capture_output=True); return {"status": "success", "total_errors": 0}
    return None
rc = recalc(path)
if rc is None:
    print("SKIP  LibreOffice not available — formula values not checked")
else:
    check("recalc: zero formula errors", rc.get("status") == "success" and rc.get("total_errors") == 0, str(rc)[:200])
    v = openpyxl.load_workbook(path, data_only=True)[C.CALC_TAB]
    rr = {v[f"A{r}"].value or v[f"B{r}"].value: r for r in range(2, v.max_row + 1)}
    g = lambda key, k: v[f"{C.L[k]}{rr[key]}"].value
    # A2 by hand: price 499, sourcing 150, electronics defaults: defect .03, ship 60, rto .08, ads .08, gst .18; referral override .05, closing 30
    usable = 150 / .97; fees = 499 * .05 + 30; gst = 499 * .18 / 1.18 - usable * .18
    rto = .08 / .92 * 60 * 2; ads = 499 * .08; total = usable + fees + gst + 60 + rto + ads; margin = (499 - total) / 499
    check("A2 margin matches hand calculation (override + electronics defaults, row moved)",
          abs(g("A2", "margin") - margin) < 1e-6, f"sheet {g('A2', 'margin'):.4f} vs hand {margin:.4f}")
    check("min-margin edit (20%) used by decision",
          (g("A2", "decision").startswith("GO")) == (margin >= .20 and (499 - total) / (150 * 1.18) >= .30), g("A2", "decision"))
    check("toy with quote held for certification", g("A3", "decision").startswith("HOLD") or g("A3", "decision").startswith("NO-GO"),
          g("A3", "decision"))
    check("no quote → NEED SOURCING QUOTE", g("A9", "decision") == "NEED SOURCING QUOTE")
    check("beauty default pulled by category lookup", abs(g("A9", "ship") - 50) < 1e-9)
    check("unknown category falls back to 'default' row", abs(g("My manual idea", "ship") - 60) < 1e-9)

# quarantine refresh: existing row updated from refresh list, new quarantined product NOT added
q = sig("A2", 70, 499, "electronics"); q["signal"] = "⏸ Quarantined — price dip <48h"
nq = sig("QNEW", 99, 999); nq["signal"] = "⏸ Quarantined — price dip <48h"
C.update_xlsx(path, [sig("A9", 95, 1299, "beauty")], today="2026-10-27", refresh=[q, nq])
rows = {r["asin"]: r for r in C.read_xlsx(path) if r["asin"]}
check("existing row refreshed to quarantine status; quarantined newcomer not added",
      "Quarantined" in rows["A2"]["signal"] and "QNEW" not in rows)
rc2 = recalc(path)
if rc2:
    v = openpyxl.load_workbook(path, data_only=True)[C.CALC_TAB]
    d = {v[f"A{r}"].value: v[f"{C.L['decision']}{r}"].value for r in range(2, v.max_row + 1)}
    check("quarantined row decision = HOLD even with a quote", d["A2"].startswith("HOLD — price-dip"), d["A2"])
    check("recalc after refresh: zero errors", rc2.get("total_errors") == 0)

# seasonal HOLD + unquoted-row cap + ISBN zero-padding
p2 = os.path.join(TMP, "C2.xlsx")
ss = sig("B0SEASON01", 99, 299); ss["seasonal"] = "SEASONAL: Diwali"
C.update_xlsx(p2, [ss] + [sig(f"B0CAP{i:05d}", 50 - i * .1, 199) for i in range(24)], today="2026-10-30")
C.update_xlsx(p2, [ss] + [sig(f"B0CAQ{i:05d}", 40 - i * .1, 199) for i in range(24)], today="2026-10-31")
r2 = C.read_xlsx(p2)
check(f"unquoted rows capped at {C.MAX_UNQUOTED}", len(r2) == C.MAX_UNQUOTED, f"{len(r2)} rows")
wbx = openpyxl.load_workbook(p2); wsx = wbx[C.CALC_TAB]
rs = [r for r in range(2, wsx.max_row + 1) if wsx[f"A{r}"].value == "B0SEASON01"][0]
wsx[f"{C.L['sourcing']}{rs}"] = 50; wbx.save(p2)
rc3 = recalc(p2)
if rc3:
    vv = openpyxl.load_workbook(p2, data_only=True)[C.CALC_TAB]
    dec = [vv[f"{C.L['decision']}{r}"].value for r in range(2, vv.max_row + 1) if vv[f"A{r}"].value == "B0SEASON01"][0]
    check("seasonal product with a great quote is still HOLD", dec.startswith("HOLD — seasonal"), dec)
rows_isbn = C.parse([[h for _, h, _ in C.COLS], ["8640378"] + [""] * (len(C.COLS) - 1)])
check("ISBN ASIN re-padded after Sheets stripped zeros", rows_isbn[0]["asin"] == "0008640378")

# ── Google Sheets path (mock)
class WS:
    def __init__(s): s.vals = []
    def get_all_values(s, value_render_option=None): return s.vals
    def clear(s): s.vals = []
    def update(s, range_name=None, values=None, value_input_option=None): s.vals = [list(r) for r in values]
class WB:
    def __init__(s): s.tabs = {}
    def worksheet(s, n):
        import gspread
        if n not in s.tabs: raise gspread.WorksheetNotFound(n)
        return s.tabs[n]
    def add_worksheet(s, title, rows, cols): s.tabs[title] = WS(); return s.tabs[title]
wbm = WB()
C.update_gsheet(wbm, [sig("G1", 90, 799)], today="2026-10-01")
wbm.tabs[C.ASSUMP_TAB].vals[2][1] = 0.25                      # user edits threshold in Sheets
cal = wbm.tabs[C.CALC_TAB].vals; hdr = cal[0]
cal[1][hdr.index(C.dict(C.COLS) if False else [h for k, h, _ in C.COLS if k == "sourcing"][0])] = 333
C.update_gsheet(wbm, [sig("G2", 99, 199), sig("G1", 90, 799)], today="2026-10-02")
rows = C.parse(wbm.tabs[C.CALC_TAB].vals)
check("Sheets: assumptions tab not overwritten", wbm.tabs[C.ASSUMP_TAB].vals[2][1] == 0.25)
check("Sheets: your quote preserved & row moved first", rows[0]["asin"] == "G1" and rows[0]["sourcing"] == 333)
f = wbm.tabs[C.CALC_TAB].vals[1][hdr.index([h for k, h, _ in C.COLS if k == "usable"][0])]
check("Sheets: formulas point at the row's new position", f"${C.L['sourcing']}2" in f, f)

# ── YouTube (fake API)
class R:
    def __init__(s, j, code=200): s._j, s.status_code, s.text = j, code, json.dumps(j)
    def json(s): return s._j
    def raise_for_status(s): pass
class Sess:
    def __init__(s): s.calls = []
    def get(s, url, params=None, timeout=None):
        s.calls.append((url.rsplit("/", 1)[-1], params.get("q")))
        if url.endswith("search"):
            base = "explode" if "Power" in params["q"] else "grip"
            return R({"items": [{"id": {"videoId": f"{base}{i}"}, "snippet": {"title": "t"}} for i in range(8)]})
        items = []
        for vid in params["id"].split(","):
            bad = vid.startswith("explode")
            items.append({"id": vid, "snippet": {"title": "Power bank EXPLODED - don't buy" if bad else "Phone grip review",
                          "publishedAt": "2026-10-20T00:00:00Z"}, "statistics": {"viewCount": "50000", "likeCount": "2000"}})
        return R({"items": items})
class SessLizard(Sess):
    def get(self, url, params=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], params.get("q")))
        if url.endswith("search"):
            return R({"items": [{"id": {"videoId": f"liz{i}"}, "snippet": {"title": "t"}} for i in range(8)]})
        return R({"items": [{"id": v, "snippet": {"title": ("Fake lizard prank on my sister 😂" if i % 2 else
                 "Fake Lizards review — scam, don't buy"), "publishedAt": "2026-10-20T00:00:00Z"},
                 "statistics": {"viewCount": "90000", "likeCount": "4000"}} for i, v in enumerate(params["id"].split(","))]})
yl = Y.check_products([("LIZ", "Fake Lizards (Pack of 6) - Halloween Decoration | Propcon")], "KEY", session=SessLizard(),
                      now=datetime(2026, 10, 31, tzinfo=timezone.utc))[0]
check("'Fake' in the product's own name is not negative buzz; real negatives still count",
      yl["negative_title_share"] == 0.5, f"share {yl['negative_title_share']} (only the 'scam, don't buy' half)")
sess = Sess()
out = Y.check_products([("P1", "Ringke Phone Grip Holder for iPhone, Black (Pack of 2)"),
                        ("P2", "Ambrane 20000mAh Power Bank, 22.5W Fast Charging")], "KEY", session=sess,
                       now=datetime(2026, 10, 31, tzinfo=timezone.utc))
by = {r["asin"]: r for r in out}
check("YouTube queries use 'review' (intent), not raw keyword", all(q.endswith(("review", "unboxing")) for c_, q in sess.calls if c_ == "search"))
check("8 review results → unboxing search skipped (quota)", sum(c_ == "search" for c_, _ in sess.calls) == 2)
check("negative titles flagged even with high views", by["P2"]["verdict"].startswith("⚠"), by["P2"]["verdict"])
check("positive product gets interest verdict + top-3 links to watch",
      "interest" in by["P1"]["verdict"] and by["P1"]["top_videos"].count("youtu.be") == 3, by["P1"]["verdict"])
check("keyword extraction trims Amazon title", Y.keyword("Ringke Phone Grip Holder for iPhone, Black (Pack of 2)") == "Ringke Phone Grip Holder")

shutil.rmtree(TMP)
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
