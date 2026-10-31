#!/usr/bin/env python3
"""Offline tests: SUPPLIER_QUOTES → CALCULATOR, FLIP_TRACKER verdicts, weekly brief."""
import os, sys, glob, json, shutil, subprocess, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
import openpyxl, pandas as pd
import calculator as C, workbook_tabs as W, brief as B, picks as P
ok = True
def check(n, c, d=""):
    global ok; ok &= bool(c); print(f"{'PASS' if c else 'FAIL'}  {n}  {d}")
skill = glob.glob("/root/.claude/skills/**/xlsx/scripts/recalc.py", recursive=True)
def recalc(p):
    if not skill: return None
    out = subprocess.run([sys.executable, skill[0], p, "120"], capture_output=True, text=True).stdout
    return json.loads(out[out.index("{"):])

path = os.path.join(TMP, "CALCULATOR.xlsx")
sig = lambda a, price: {"asin": a, "title": f"Product {a}", "category_slug": "kitchen", "trend_score": 80,
                        "signal": "📈", "price_inr": price, "compliance": "NOT FLAGGED (≠ cleared — verify)", "url": "u"}
C.update_xlsx(path, [sig("B0QUOTED01", 449), sig("B0NOQUOTE1", 399)], today="2026-11-08")
wb = openpyxl.load_workbook(path)
check("SUPPLIER_QUOTES and FLIP_TRACKER tabs created", {W.QUOTES_TAB, W.FLIP_TAB} <= set(wb.sheetnames), str(wb.sheetnames))
q = wb[W.QUOTES_TAB]
def put_quote(row, vals):
    for col, v in zip("ABCDEFGHIJKLMNOPQ", vals):
        q[f"{col}{row}"] = v
r0 = W.Q_FIRST + 1
put_quote(r0,     ["B0QUOTED01", "x", "S1 Traders", "Delhi", "", 100, 62, 300, 55, None, None, .18, "Y", 4, "NA", 10, ""])
put_quote(r0 + 1, ["B0QUOTED01", "x", "S2 Cheap",   "Surat", "", 20, 40, None, None, None, None, .18, "Y", 2, "NA", 7, "bad sample"])
put_quote(r0 + 2, ["B0QUOTED01", "x", "S3 Good",    "Pune",  "", 10, 60, 50, 58, None, None, .18, "Y", 4, "NA", 9, ""])
f = wb[W.FLIP_TAB]; fr = W.F_FIRST + 1
today = date.today()
batches = [  # id, asin, product, channel, start, bought, cost, price, sold, rto, ret, dmg, fees, ship, ads, rating
    ("KILL-1", "B0A", "slow", "Amazon", today - timedelta(days=31), 50, 70, 449, 10, 2, 0, 0, 1300, 900, 1500, 4.0),
    ("ITER-1", "B0B", "meh", "Amazon", today - timedelta(days=31), 50, 70, 449, 22, 3, 1, 0, 2900, 1800, 800, 4.1),
    ("RUN-1", "B0C", "new", "Meesho", today - timedelta(days=5), 50, 70, 449, 3, 0, 0, 0, 0, 300, 0, None),
    ("LOSS-1", "B0D", "fast-but-loss", "Amazon", today - timedelta(days=31), 50, 70, 299, 40, 10, 5, 2, 4800, 4500, 3000, 4.3),
]
for i, b in enumerate(batches):
    for col, v in zip("ABCDEFGHIJKLMNOP", b):
        f[f"{col}{fr + i}"] = v
wb.save(path)
C.update_xlsx(path, [sig("B0QUOTED01", 449), sig("B0NOQUOTE1", 399)], today="2026-11-09")   # next daily run
wb2 = openpyxl.load_workbook(path)
check("your quotes and batches survive the next daily run",
      wb2[W.QUOTES_TAB][f"C{r0 + 2}"].value == "S3 Good" and wb2[W.FLIP_TAB][f"A{fr}"].value == "KILL-1")
check("quoted ASIN detected (example row ignored)",
      W.quoted_asins(None, path) == {"B0QUOTED01"}, str(W.quoted_asins(None, path)))
rows = {r["asin"]: r for r in C.read_xlsx(path)}
check("calculator sourcing cell is the best-quote formula (not a typed value)",
      C._is_default_formula(rows["B0QUOTED01"]["sourcing"]) and not C.has_quote(rows["B0NOQUOTE1"]))
check("has_quote counts a logged ★ quote", C.has_quote(rows["B0QUOTED01"], {"B0QUOTED01"}))

rc = recalc(path)
if rc is None:
    print("SKIP  LibreOffice not available")
else:
    check("recalc: zero formula errors across all tabs", rc.get("total_errors") == 0, str(rc)[:160])
    v = openpyxl.load_workbook(path, data_only=True)
    vq = v[W.QUOTES_TAB]
    eff = {vq[f"C{r}"].value: (vq[f"R{r}"].value, vq[f"S{r}"].value, vq[f"T{r}"].value) for r in range(r0, r0 + 3)}
    check("tier price chosen for test qty 50 (S3: 58 at ≥50; S1: 62, below its MOQ 100)",
          eff["S3 Good"][0] == 58 and eff["S1 Traders"][0] == 62 and "MOQ 100" in str(eff["S1 Traders"][1]), str(eff))
    check("cheapest quote with a bad sample (2/5) is NOT best; S3 is",
          eff["S2 Cheap"][2] in (None, "") and eff["S3 Good"][2] == "★ best", str({k: x[2] for k, x in eff.items()}))
    vc = v[C.CALC_TAB]
    g = {vc[f"A{r}"].value: r for r in range(2, vc.max_row + 1)}
    check("best quote (₹58) flows into CALCULATOR sourcing", vc[f"{C.L['sourcing']}{g['B0QUOTED01']}"].value == 58)
    check("no quote → sourcing blank → NEED SOURCING QUOTE",
          vc[f"{C.L['decision']}{g['B0NOQUOTE1']}"].value == "NEED SOURCING QUOTE")
    check("quoted row gets a real decision", vc[f"{C.L['decision']}{g['B0QUOTED01']}"].value != "NEED SOURCING QUOTE",
          vc[f"{C.L['decision']}{g['B0QUOTED01']}"].value)
    vf = v[W.FLIP_TAB]
    ex = W.F_FIRST
    S = 31 * 449; T = 31 * 70; U = 70; V = S * .18 / 1.18 - 31 * 70 * .18; Wn = S - T - U - V - 4100 - 2600 - 1500
    check("example batch: real margin matches hand calculation", abs(vf[f"X{ex}"].value - Wn / S) < 1e-9,
          f"sheet {vf[f'X{ex}'].value:.4f} vs hand {Wn / S:.4f}")
    check("example batch (62% sold, 12.5% margin, 4.2★, day 35) → SCALE", vf[f"AA{ex}"].value == "SCALE — reorder 2×",
          vf[f"AA{ex}"].value)
    verd = {vf[f"A{r}"].value: vf[f"AA{r}"].value for r in range(fr, fr + 4)}
    check("20% sold by day 31 → KILL", verd["KILL-1"].startswith("KILL"), verd["KILL-1"])
    check("44% sold, positive margin → ITERATE", verd["ITER-1"].startswith("ITERATE"), verd["ITER-1"])
    check("day 5 → RUNNING (no early verdict)", verd["RUN-1"].startswith("RUNNING"), verd["RUN-1"])
    check("fast seller at a loss → KILL (sell-through alone is not success)", verd["LOSS-1"].startswith("KILL"),
          f"{verd['LOSS-1']} · margin {vf[f'X{fr + 3}'].value:.1%}")
    check("RTO % = RTO ÷ (delivered + RTO)", abs(vf[f"Y{ex}"].value - 4 / 35) < 1e-9)

# ── migration: quotes typed under the OLD header survive the rename
old_hdr = [h if k != "sourcing" else "Sourcing cost ex-GST (₹) — IndiaMART quote" for k, h, _ in C.COLS]
line = ["B0OLDHDR01"] + [""] * (len(C.COLS) - 1); line[C.KEYS.index("sourcing")] = 123
check("quote typed under the old header name is not lost after the rename",
      C.parse([old_hdr, line])[0]["sourcing"] == 123)

# ── Google Sheets: tabs created once, never overwritten
class WS:
    def __init__(s): s.vals = []
    def update(s, range_name=None, values=None, value_input_option=None): s.vals = [list(r) for r in values]
    def format(s, *a, **k): pass
    def freeze(s, *a, **k): pass
class WB:
    def __init__(s): s.tabs = {}
    def worksheet(s, n):
        import gspread
        if n not in s.tabs: raise gspread.WorksheetNotFound(n)
        return s.tabs[n]
    def add_worksheet(s, title, rows, cols): s.tabs[title] = WS(); return s.tabs[title]
m = WB()
made1 = W.ensure_gsheet(m); m.tabs[W.QUOTES_TAB].vals[2][1] = 100; made2 = W.ensure_gsheet(m)
check("Sheets: both tabs created once, your edits kept", made1 == [W.QUOTES_TAB, W.FLIP_TAB] and made2 == []
      and m.tabs[W.QUOTES_TAB].vals[2][1] == 100)

# ── brief
from datetime import datetime
d = lambda i: (datetime(2026, 11, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
H = pd.DataFrame([(d(i), "bestsellers", "kitchen", a, (max(5, 40 - 4 * i) if a == "K1" else 20 + j))
                  for i in range(9) for j, a in enumerate(["K1", "K2", "K3"])],
                 columns=["date", "list", "category_slug", "asin", "rank"])
S_ = pd.DataFrame([
    {"asin": "K1", "title": "Steel Bottle & Cup <1L>", "category_slug": "kitchen", "trend_score": 70, "actionable": "True",
     "discount_driven": "False", "short_dip": "False", "seasonal": "", "competition": "Moderate competition (4 sellers)",
     "compliance": "NOT FLAGGED (≠ cleared — verify)"},
    {"asin": "K2", "title": "Diwali Diya Lights", "category_slug": "kitchen", "trend_score": 90, "actionable": "True",
     "discount_driven": "False", "short_dip": "False", "seasonal": "SEASONAL: Diwali", "competition": "", "compliance": ""},
    {"asin": "K3", "title": "Flash Kettle", "category_slug": "kitchen", "trend_score": 99, "actionable": "True",
     "discount_driven": "True", "short_dip": "False", "seasonal": "", "competition": "", "compliance": ""}])
heat = pd.DataFrame([{"category": "Home & Kitchen", "top10_score": 55, "movers": 3}])
page, text = B.build(S_, heat, "Forward test (14d): picks 60% vs random 30% → 2.00×", H, d(8), P,
                     {"kitchen": "Home & Kitchen"}, weeks_tracked=6)
open(os.path.join(TMP, "brief.html"), "w").write(page)
check("brief: track record shown first", page.index("Track record") < page.index("Rising this week") and "2.00×" in page)
check("brief: rising product with its exact reason", "Climbed <b>#36 → #8</b> in Home &amp; Kitchen" in page)
check("brief: discount-driven excluded; seasonal kept OUT of 'rising' but listed in Seasonal watch",
      "Flash Kettle" not in page and page.index("Diwali Diya") > page.index("Seasonal watch"))
check("brief: no prices, titles escaped", "₹" not in page and "&lt;1L&gt;" in page and "&amp;" in page)
check("brief: asks willingness to pay", "monthly price" in page)
check("brief: dark-mode safe", "prefers-color-scheme:dark" in page)
shutil.copy(os.path.join(TMP, "brief.html"), "/tmp/claude-0/-home-claude/da96ca2d-182b-53ee-9c25-bd549c57a931/scratchpad/sample_brief.html")
shutil.rmtree(TMP)
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
