"""
Unit-economics calculator, auto-filled from daily trend signals.

Two tabs (same layout in Google Sheets and in a local .xlsx):
  CALC_ASSUMPTIONS  thresholds + per-category defaults. Written ONCE; your edits are never overwritten.
  CALCULATOR        one row per candidate ASIN (+ any manual rows you add with an empty ASIN).

Every run:
  * new actionable signals (top N) are appended
  * product info columns (price, sellers, compliance…) refresh for products seen today
  * YOUR inputs are preserved: sourcing cost, MOQ, notes, and any default you overrode with a value
  * rows with no sourcing quote that haven't appeared for STALE_DAYS are dropped
  * all formula cells are regenerated for the row's position (so they never point at the wrong row)

Model (per unit, price on Amazon.in is GST-inclusive):
  net usable cost   = sourcing ex-GST / (1 - defect%)
  amazon fees       = price × referral% + closing fee
  GST payable       = price × GST/(1+GST)  −  ITC × usable cost × purchase GST%
  RTO cost          = RTO%/(1-RTO%) × shipping × return multiplier   (per DELIVERED order; item restocked)
  total cost        = usable + fees + GST payable + shipping + RTO + ad cost
  profit, margin = profit/price, ROI = profit / (sourcing incl. GST), cash for MOQ = MOQ × sourcing incl. GST
"""
import re
from datetime import datetime

CALC_TAB = "CALCULATOR"
ASSUMP_TAB = "CALC_ASSUMPTIONS"
STALE_DAYS = 21
TOP_N = 25
MAX_UNQUOTED = 40     # rows waiting for a quote; beyond this, lowest-scoring are dropped (yours never)

# ── column layout ─────────────────────────────────────────────────────
COLS = [  # (key, header, kind)  kind: info=refreshed by fetcher · input=yours · default=formula you may override · calc=formula
    ("asin", "ASIN", "info"), ("product", "Product", "info"), ("category", "Category", "info"),
    ("trend_score", "Trend score", "info"), ("signal", "Signal", "info"),
    ("price", "Amazon price incl. GST (₹)", "info"), ("sellers", "Sellers on listing", "info"),
    ("bought", "Bought/month (min)", "info"), ("compliance", "Compliance", "info"), ("seasonal", "Seasonal", "info"),
    ("stock_gate", "Stock gate (held 2–3 weeks?)", "info"),
    ("competition", "Competition note", "info"), ("url", "Amazon URL", "info"),
    ("ind_cost", "Indicative cost/pc (IndiaMART median, NOT a quote)", "info"),
    ("ind_range", "IndiaMART range P25–P75 (₹)", "info"),
    ("ind_matches", "Comparable listings / suppliers", "info"),
    ("ind_conf", "Estimate confidence", "info"), ("ind_url", "IndiaMART search", "info"),
    ("search_words", "IndiaMART search words (optional override)", "input"),
    ("sourcing", "Sourcing cost ex-GST (₹) — best logged quote (or type one)", "default"),
    ("purchase_gst", "Purchase GST %", "default"), ("defect", "Defect %", "default"),
    ("moq", "MOQ (units)", "input"), ("ship", "Shipping per order (₹)", "default"),
    ("referral", "Referral fee %", "default"), ("closing", "Closing fee (₹)", "default"),
    ("rto", "RTO %", "default"), ("ads", "Ads/PPC per unit (₹)", "default"),
    ("sale_gst", "Sale GST %", "default"),
    ("usable", "Net usable cost (₹)", "calc"), ("fees", "Amazon fees (₹)", "calc"),
    ("gst_pay", "GST payable (₹)", "calc"), ("rto_cost", "RTO cost per delivered (₹)", "calc"),
    ("total", "Total cost per unit (₹)", "calc"), ("profit", "Net profit per unit (₹)", "calc"),
    ("margin", "Net margin %", "calc"), ("roi", "ROI on inventory %", "calc"),
    ("cash", "Cash needed for MOQ (₹)", "calc"), ("decision", "Decision", "calc"),
    ("est_margin", "Est. margin % at indicative cost", "calc"),
    ("est_verdict", "Triage (indicative — get a real quote)", "calc"),
    ("last_seen", "Last seen in signals", "info"), ("notes", "Your notes", "input"),
]
KEYS = [c[0] for c in COLS]
L = {k: None for k in KEYS}


def _col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


for i, k in enumerate(KEYS):
    L[k] = _col_letter(i)

# ── assumptions tab layout ────────────────────────────────────────────
A = "'" + ASSUMP_TAB + "'!"  # quoted sheet ref (works in Excel, LibreOffice, Sheets)
THRESHOLDS = [  # (label, value, note) → rows 3.. in column B
    ("Min net margin", 0.15, "GO needs margin ≥ this. Your original sheet used 15%."),
    ("Min ROI on inventory", 0.30, "Profit ÷ sourcing cost incl. GST. Guards cheap-but-slow products."),
    ("Input tax credit claimable (1=yes, 0=no)", 1, "1 if GST-registered and claiming ITC on purchases."),
    ("Return shipping multiplier", 2, "RTO wastes forward + reverse shipping → 2× shipping per RTO."),
]
TH_ROW = {"min_margin": 3, "min_roi": 4, "itc": 5, "ret_mult": 6}
CAT_HEADER_ROW = 9
CAT_COLS = ["Category slug", "Referral fee %", "Closing fee (₹)", "Shipping per order (₹)", "RTO %",
            "Ads % of price", "Sale GST %", "Purchase GST %", "Defect %", "Source / note"]
_PH = "PLACEHOLDER — replace with Amazon Seller Central fee schedule & your courier rates"
CAT_DEFAULTS = [  # placeholders, deliberately round numbers: NOT Amazon's actual fees
    ("default", 0.10, 30, 60, 0.10, 0.08, 0.18, 0.18, 0.03, _PH),
    ("electronics", 0.08, 30, 60, 0.08, 0.08, 0.18, 0.18, 0.03, _PH),
    ("kitchen", 0.10, 30, 80, 0.12, 0.08, 0.18, 0.18, 0.04, _PH),
    ("beauty", 0.08, 25, 50, 0.10, 0.10, 0.18, 0.18, 0.02, _PH),
    ("apparel", 0.15, 25, 50, 0.25, 0.10, 0.12, 0.12, 0.03, _PH + " (apparel GST slab depends on price)"),
    ("toys", 0.10, 30, 60, 0.10, 0.08, 0.18, 0.18, 0.03, _PH),
]
CAT_FIRST = CAT_HEADER_ROW + 1
CAT_LAST = CAT_FIRST + 40          # room for you to add categories
DEF_COL = {"referral": "B", "closing": "C", "ship": "D", "rto": "E", "ads_pct": "F",
           "sale_gst": "G", "purchase_gst": "H", "defect": "I"}


def _lookup(key, r):
    col = DEF_COL[key]
    rng = f"{A}${col}${CAT_FIRST}:${col}${CAT_LAST}"
    keys = f"{A}$A${CAT_FIRST}:$A${CAT_LAST}"
    dflt = f"{A}${col}${CAT_FIRST}"     # 'default' row
    return f"IFERROR(INDEX({rng},MATCH(${L['category']}{r},{keys},0)),{dflt})"


def default_formula(key, r):
    if key == "sourcing":                       # best ★ quote from SUPPLIER_QUOTES, "" if none
        import workbook_tabs
        return workbook_tabs.best_quote_formula(f"$A{r}")
    if key == "ads":
        return f"=${L['price']}{r}*{_lookup('ads_pct', r)}"
    return "=" + _lookup(key, r)


def calc_formula(key, r):
    c = {k: f"${L[k]}{r}" for k in KEYS}
    th = {k: f"{A}$B${v}" for k, v in TH_ROW.items()}
    blank = f'{c["sourcing"]}=""'
    f = {
        "usable": f'=IF({blank},"",{c["sourcing"]}/(1-{c["defect"]}))',
        "fees": f'={c["price"]}*{c["referral"]}+{c["closing"]}',
        "gst_pay": f'=IF({blank},"",{c["price"]}*{c["sale_gst"]}/(1+{c["sale_gst"]})'
                   f'-{th["itc"]}*{c["usable"]}*{c["purchase_gst"]})',
        "rto_cost": f'=IF({c["rto"]}>=1,"",{c["rto"]}/(1-{c["rto"]})*{c["ship"]}*{th["ret_mult"]})',
        "total": f'=IF({blank},"",{c["usable"]}+{c["fees"]}+{c["gst_pay"]}+{c["ship"]}+{c["rto_cost"]}+{c["ads"]})',
        "profit": f'=IF({blank},"",{c["price"]}-{c["total"]})',
        "margin": f'=IF(OR({blank},{c["price"]}=0),"",{c["profit"]}/{c["price"]})',
        "roi": f'=IF({blank},"",{c["profit"]}/({c["sourcing"]}*(1+{c["purchase_gst"]})))',
        "cash": f'=IF(OR({blank},{c["moq"]}=""),"",{c["moq"]}*{c["sourcing"]}*(1+{c["purchase_gst"]}))',
        # indicative margin: same cost model with the IndiaMART median in place of your quote
        "est_margin": (f'=IF(OR(NOT(ISNUMBER({c["ind_cost"]})),{c["price"]}=0),"",'
                       f'({c["price"]}-({c["ind_cost"]}/(1-{c["defect"]})+{c["fees"]}'
                       f'+{c["price"]}*{c["sale_gst"]}/(1+{c["sale_gst"]})-{th["itc"]}*{c["ind_cost"]}/(1-{c["defect"]})*{c["purchase_gst"]}'
                       f'+{c["ship"]}+{c["rto_cost"]}+{c["ads"]}))/{c["price"]})'),
        "est_verdict": (f'=IF({c["est_margin"]}="","",IF({c["est_margin"]}>={th["min_margin"]},'
                        f'"Worth requesting a quote ("&{c["ind_conf"]}&" confidence)",'
                        f'"Likely not viable at listed prices"))'),
        "decision": (f'=IF(AND({c["stock_gate"]}<>"",ISERROR(SEARCH("STOCK-READY",{c["stock_gate"]}))),'
                     f'"WAIT — "&{c["stock_gate"]},'
                     f'IF(ISNUMBER(SEARCH("Quarantined",{c["signal"]})),"HOLD — price-dip quarantine",'
                     f'IF(LEFT({c["seasonal"]},8)="SEASONAL","HOLD — seasonal: only if stock lands ≥2 weeks before the event",'
                     f'IF({blank},"NEED SOURCING QUOTE",'
                     f'IF({c["margin"]}<{th["min_margin"]},"NO-GO (margin)",'
                     f'IF({c["roi"]}<{th["min_roi"]},"NO-GO (ROI)",'
                     f'IF(LEFT({c["compliance"]},6)="REVIEW","HOLD — certification check first",'
                     f'"GO — still verify compliance & sample")))))))'),
    }
    return f[key]


_DEFAULT_RX = re.compile(r"^=\s*(\$?[A-Z]+\$?\d+\s*\*\s*)?IFERROR\((INDEX|SUMIFS)\(", re.I)


def _is_default_formula(v):
    return isinstance(v, str) and bool(_DEFAULT_RX.match(v))


def has_quote(r, quoted_asins=()):
    """True if the row has a real sourcing cost: a typed number, or a logged ★ quote for its ASIN."""
    v = r.get("sourcing")
    if str(r.get("asin") or "") in quoted_asins:
        return True
    return v not in (None, "", "None") and not _is_default_formula(v)


def assumptions_values():
    rows = [["Bazaar Radar — calculator assumptions (edit the BLUE cells; the fetcher never overwrites this tab)"],
            ["Threshold", "Value", "Note"]]
    rows += [[lab, val, note] for lab, val, note in THRESHOLDS]
    rows += [[], [f"Per-category defaults — rows {CAT_FIRST}–{CAT_LAST}. Add your own categories (use the "
                  f"fetcher's slug, e.g. 'sports'). 'default' row is used when a category isn't listed."], CAT_COLS]
    rows += [list(r) for r in CAT_DEFAULTS]
    return rows


IND_KEYS = ("ind_cost", "ind_range", "ind_matches", "ind_conf", "ind_url")


def _row_from_signal(sig, today):
    g = lambda k: sig.get(k) if sig.get(k) is not None else ""
    if sig.get("_sourcing_update"):                      # sourcing run: only touch indicative columns
        return {"asin": g("asin"), **{k: g(k) for k in IND_KEYS}}
    return {"asin": g("asin"), "product": str(g("title"))[:120], "category": g("category_slug") or g("category"),
            "trend_score": g("trend_score"), "signal": g("signal"), "price": g("price_inr"),
            "sellers": g("seller_offers"), "bought": g("bought_past_month_min"),
            "compliance": g("compliance"), "seasonal": g("seasonal"), "stock_gate": g("stock_gate"), "competition": g("competition"),
            "url": g("url"),
            "last_seen": today}


def merge(existing_rows, signals, today=None, top_n=TOP_N, stale_days=STALE_DAYS, refresh=None):
    """existing_rows: list of dicts (key → raw cell content, formulas as strings).
    signals: actionable signals, best first — the only ones ADDED as new rows.
    refresh: all of today's signals (incl. quarantined) — used to UPDATE rows already present,
    so a held product shows its current status instead of yesterday's."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    by_asin, manual = {}, []
    for r in existing_rows:
        a = str(r.get("asin") or "").strip()
        (by_asin.__setitem__(a, r) if a else manual.append(r))
    seen = set()
    for sig in (refresh or []):
        a = str(sig.get("asin") or "")
        if a in by_asin:
            new = _row_from_signal(sig, today)
            by_asin[a].update({k: v for k, v in new.items() if v != ""})
            if not sig.get("_sourcing_update"):
                seen.add(a)
    for sig in signals[:top_n]:
        new = _row_from_signal(sig, today)
        a = new["asin"]
        seen.add(a)
        if a in by_asin:
            by_asin[a].update({k: v for k, v in new.items() if v != ""})
        else:
            by_asin[a] = new
    out = []
    for a, r in by_asin.items():
        has_input = has_quote(r)
        try:
            age = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(str(r.get("last_seen"))[:10], "%Y-%m-%d")).days
        except ValueError:
            age = 0
        if a not in seen and not has_input and age > stale_days:
            continue
        out.append(r)
    out.sort(key=lambda r: (not has_quote(r), -_num(r.get("trend_score")),
                            -_num(r.get("bought"))))
    quoted = [r for r in out if has_quote(r)]
    unquoted = [r for r in out if r not in quoted][:MAX_UNQUOTED]
    return quoted + unquoted + manual


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def render(rows):
    """→ 2D list (header + rows) with formulas regenerated for each row position."""
    out = [[h for _, h, _ in COLS]]
    for i, r in enumerate(rows):
        rr = i + 2
        line = []
        for k, _, kind in COLS:
            v = r.get(k, "")
            v = "" if v is None or str(v) == "nan" else v
            if kind == "calc":
                v = calc_formula(k, rr)
            elif kind == "default" and (v == "" or _is_default_formula(v)):
                v = default_formula(k, rr)
            line.append(v)
        out.append(line)
    return out


# Previous header names → key. A renamed header must never orphan data you typed under the old name.
HEADER_ALIASES = {
    "Sourcing cost ex-GST (₹) — IndiaMART quote": "sourcing",
}


def parse(values):
    """2D list (as read back, formulas as strings) → list of row dicts keyed by our keys."""
    if not values or len(values) < 2:
        return []
    hdr = values[0]
    idx = {h: i for i, h in enumerate(hdr)}
    by_key = {k: h for k, h, _ in COLS}
    for old, key in HEADER_ALIASES.items():          # old header present, new one absent → read the old column
        if old in idx and by_key[key] not in idx:
            idx[by_key[key]] = idx[old]
    rows = []
    for line in values[1:]:
        if not any(str(x).strip() for x in line):
            continue
        r = {k: (line[idx[h]] if h in idx and idx[h] < len(line) else "") for k, h, _ in COLS}
        a = str(r.get("asin") or "").strip().lstrip("'")
        r["asin"] = a.zfill(10) if a.isdigit() and len(a) < 10 else a
        rows.append(r)
    return rows


# ── xlsx backend (local mode + the upgraded workbook) ─────────────────
def _style_xlsx(wb):
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.comments import Comment
    blue, black = Font(name="Arial", color="0000FF"), Font(name="Arial", color="000000")
    bold = Font(name="Arial", bold=True)
    yellow = PatternFill("solid", fgColor="FFFF00")
    grey = PatternFill("solid", fgColor="EDEDED")
    ws = wb[CALC_TAB]
    kinds = {i + 1: kind for i, (_, _, kind) in enumerate(COLS)}
    keys = {i + 1: k for i, (k, _, _) in enumerate(COLS)}
    for c in ws[1]:
        c.font, c.fill = bold, grey
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            kind, k = kinds.get(c.column), keys.get(c.column)
            c.font = blue if kind in ("input", "default") else Font(name="Arial")
            if kind == "input":
                c.fill = yellow
            if k in ("purchase_gst", "defect", "referral", "rto", "sale_gst", "margin", "roi"):
                c.number_format = "0.0%;(0.0%);-"
            elif k in ("price", "sourcing", "ship", "closing", "ads", "usable", "fees", "gst_pay",
                       "rto_cost", "total", "profit", "cash"):
                c.number_format = "₹#,##0;(₹#,##0);-"
    for i, (k, h, kind) in enumerate(COLS):
        ws.column_dimensions[_col_letter(i)].width = {"product": 38, "competition": 40, "compliance": 34,
                                                      "signal": 26, "decision": 30, "notes": 30}.get(k, 14)
    legend = {"sourcing": "YELLOW = you fill in (IndiaMART quote, ex-GST, per unit).",
              "purchase_gst": "BLUE = default pulled from CALC_ASSUMPTIONS by category. Type a number to override; the fetcher keeps your number.",
              "price": "Black = refreshed daily by the fetcher. Amazon.in prices include GST.",
              "decision": "GO needs margin ≥ min AND ROI ≥ min AND no compliance flag. An unflagged product is NOT legally cleared."}
    for k, txt in legend.items():
        ws[f"{L[k]}1"].comment = Comment(txt, "Bazaar Radar")
    ws.freeze_panes = "C2"
    wa = wb[ASSUMP_TAB]
    for row in wa.iter_rows():
        for c in row:
            c.font = Font(name="Arial")
    for r in (2, CAT_HEADER_ROW):
        for c in wa[r]:
            c.font, c.fill = bold, grey
    wa["A1"].font = bold
    for r in range(3, 3 + len(THRESHOLDS)):
        wa[f"B{r}"].font, wa[f"B{r}"].fill = blue, yellow
    wa["B3"].number_format = wa["B4"].number_format = "0.0%"
    for r in range(CAT_FIRST, CAT_FIRST + len(CAT_DEFAULTS)):
        for col in "BCDEFGHI":
            wa[f"{col}{r}"].font = blue
        for col in "BEFGHI":
            wa[f"{col}{r}"].number_format = "0.0%"
    wa.column_dimensions["A"].width, wa.column_dimensions["C"].width = 40, 18
    wa.column_dimensions["J"].width = 60


def write_xlsx(path, rows, keep_sheets_from=None):
    import openpyxl, os
    if keep_sheets_from and os.path.exists(keep_sheets_from):
        wb = openpyxl.load_workbook(keep_sheets_from)
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
    import workbook_tabs
    workbook_tabs.ensure_xlsx(wb)                  # SUPPLIER_QUOTES + FLIP_TRACKER, created once
    if ASSUMP_TAB not in wb.sheetnames:            # written once; never overwritten
        wa = wb.create_sheet(ASSUMP_TAB)
        for line in assumptions_values():
            wa.append(line)
    if CALC_TAB in wb.sheetnames:
        del wb[CALC_TAB]
    ws = wb.create_sheet(CALC_TAB, 0)
    for line in render(rows):
        ws.append(line)
    _style_xlsx(wb)
    wb.save(path)


def read_xlsx(path):
    import openpyxl, os
    if not os.path.exists(path):
        return []
    wb = openpyxl.load_workbook(path)               # formulas as strings (not data_only)
    if CALC_TAB not in wb.sheetnames:
        return []
    return parse([[c.value for c in row] for row in wb[CALC_TAB].iter_rows()])


def update_xlsx(path, signals, today=None, refresh=None):
    rows = merge(read_xlsx(path), signals, today, refresh=refresh)
    write_xlsx(path, rows, keep_sheets_from=path)
    return len(rows)


# ── Google Sheets backend ─────────────────────────────────────────────
def update_gsheet(wb, signals, today=None, refresh=None):
    import gspread
    import workbook_tabs
    workbook_tabs.ensure_gsheet(wb)
    try:
        wb.worksheet(ASSUMP_TAB)
    except gspread.WorksheetNotFound:
        wa = wb.add_worksheet(title=ASSUMP_TAB, rows=CAT_LAST + 5, cols=12)
        wa.update(range_name="A1", values=assumptions_values(), value_input_option="USER_ENTERED")
    try:
        ws = wb.worksheet(CALC_TAB)
        existing = parse(ws.get_all_values(value_render_option="FORMULA"))
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title=CALC_TAB, rows=200, cols=len(COLS) + 2)
        existing = []
    rows = merge(existing, signals, today, refresh=refresh)
    vals = render(rows)
    for line in vals[1:]:                       # keep all-digit ASINs (ISBNs) as text in Sheets
        if str(line[0]).isdigit():
            line[0] = "'" + str(line[0])
    ws.clear()
    ws.update(range_name="A1", values=vals, value_input_option="USER_ENTERED")
    _format_gsheet(ws, len(vals))
    return len(rows)


def _format_gsheet(ws, n_rows):
    """Number formats + input colours in Sheets (best effort; never fails the run)."""
    try:
        pct = [k for k in ("purchase_gst", "defect", "referral", "rto", "sale_gst", "margin", "roi")]
        inr = [k for k in ("price", "sourcing", "ship", "closing", "ads", "usable", "fees", "gst_pay",
                           "rto_cost", "total", "profit", "cash")]
        fmts = [{"range": f"{L[k]}2:{L[k]}{n_rows}", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}}
                for k in pct]
        fmts += [{"range": f"{L[k]}2:{L[k]}{n_rows}", "format": {"numberFormat": {"type": "CURRENCY", "pattern": "₹#,##0"}}}
                 for k in inr]
        fmts += [{"range": f"{L[k]}2:{L[k]}{n_rows}", "format": {"backgroundColor": {"red": 1, "green": 1, "blue": 0.6}}}
                 for k, _, kind in COLS if kind == "input"]
        fmts += [{"range": "A1:" + f"{L[KEYS[-1]]}1", "format": {"textFormat": {"bold": True}}}]
        ws.batch_format(fmts)
        ws.freeze(rows=1, cols=2)
    except Exception:
        pass


def read_gsheet(wb):
    import gspread
    try:
        return parse(wb.worksheet(CALC_TAB).get_all_values(value_render_option="FORMULA"))
    except gspread.WorksheetNotFound:
        return []
