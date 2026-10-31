"""
Two manual-entry tabs, created ONCE (never overwritten by the fetcher):

SUPPLIER_QUOTES — log every IndiaMART / wholesaler quote (3+ per product). The best effective
  price at your test quantity for each ASIN feeds CALCULATOR's "Sourcing cost" automatically.

FLIP_TRACKER — one row per test batch. Enter what actually happened (units, orders, RTO, returns,
  fees, ads); it computes REAL margin, sell-through, RTO % and applies the scale/kill rules you
  fixed BEFORE buying (rules live at the top of the tab).

Formulas use only functions that work identically in Excel, LibreOffice and Google Sheets
(IF, AND, OR, SUMIFS, COUNTIFS, IFERROR, TODAY).
"""

QUOTES_TAB = "SUPPLIER_QUOTES"
FLIP_TAB = "FLIP_TRACKER"
Q_FIRST, Q_LAST = 6, 300          # data rows in SUPPLIER_QUOTES
F_FIRST, F_LAST = 12, 200         # data rows in FLIP_TRACKER
QA = "'" + QUOTES_TAB + "'!"

# ── SUPPLIER_QUOTES ───────────────────────────────────────────────────
Q_COLS = [  # (letter, header, kind)
    ("A", "ASIN (match CALCULATOR)", "input"), ("B", "Product", "input"), ("C", "Supplier", "input"),
    ("D", "City", "input"), ("E", "Contact / IndiaMART link", "input"),
    ("F", "Qty tier 1 (MOQ)", "input"), ("G", "Price tier 1 ₹/pc ex-GST", "input"),
    ("H", "Qty tier 2", "input"), ("I", "Price tier 2 ₹/pc", "input"),
    ("J", "Qty tier 3", "input"), ("K", "Price tier 3 ₹/pc", "input"),
    ("L", "GST % on invoice", "input"), ("M", "Sample received? (Y/N)", "input"),
    ("N", "Sample quality 1–5", "input"), ("O", "Certificate (BIS etc.) Y/N/NA", "input"),
    ("P", "Lead time (days)", "input"), ("Q", "Notes", "input"),
    ("R", "Price at your test qty ₹/pc", "calc"), ("S", "MOQ warning", "calc"), ("T", "Best for this ASIN", "calc"),
]


def quote_formulas(r):
    test = "$B$3"
    eff = (f'=IF(OR($A{r}="",$G{r}=""),"",IF(AND($K{r}<>"",{test}>=$J{r}),$K{r},'
           f'IF(AND($I{r}<>"",{test}>=$H{r}),$I{r},$G{r})))')
    warn = f'=IF(OR($F{r}="",$A{r}=""),"",IF({test}<$F{r},"MOQ "&$F{r}&" > test qty — negotiate",""))'
    # best = lowest price at test qty for this ASIN, among quotes whose sample was not rated below 3
    ok = f'OR($N{r}="",$N{r}>=3)'
    best = (f'=IF(OR($R{r}="",NOT({ok})),"",IF(COUNTIFS($A${Q_FIRST}:$A${Q_LAST},$A{r},'
            f'$R${Q_FIRST}:$R${Q_LAST},"<"&$R{r},$N${Q_FIRST}:$N${Q_LAST},">=3")'
            f'+COUNTIFS($A${Q_FIRST}:$A${Q_LAST},$A{r},$R${Q_FIRST}:$R${Q_LAST},"<"&$R{r},'
            f'$N${Q_FIRST}:$N${Q_LAST},"")=0,"★ best",""))')
    return {"R": eff, "S": warn, "T": best}


def quotes_values():
    rows = [["Supplier quotes — log 3+ quotes per product. YELLOW = you type. The ★ best price at your test "
             "quantity flows into CALCULATOR → Sourcing cost automatically (type a number there to override)."],
            [],
            ["Test quantity (units)", 50, "Your first test batch size. Tier prices are picked for this quantity."],
            ["Rule", "Samples rated below 3/5 are never chosen as best. Always get a sample before paying for a batch."],
            [h for _, h, _ in Q_COLS]]
    example = ["EXAMPLE0001", "EXAMPLE — delete this row", "Sample Traders", "Delhi", "indiamart.com/…",
               100, 62, 300, 55, 1000, 48, 0.18, "Y", 4, "NA", 12, "Asked for 50 pcs at tier-1 price"]
    rows.append(example + [quote_formulas(Q_FIRST)[c] for c in "RST"])
    for r in range(Q_FIRST + 1, Q_FIRST + 40):
        rows.append([""] * 17 + [quote_formulas(r)[c] for c in "RST"])
    return rows


def best_quote_formula(asin_cell):
    """For CALCULATOR: lowest-qualified price at test qty for this ASIN, or "" if none logged."""
    rng = lambda c: f"{QA}${c}${Q_FIRST}:${c}${Q_LAST}"
    return (f'=IFERROR(SUMIFS({rng("R")},{rng("A")},{asin_cell},{rng("T")},"★ best")/'
            f'COUNTIFS({rng("A")},{asin_cell},{rng("T")},"★ best"),"")')


# ── FLIP_TRACKER ──────────────────────────────────────────────────────
F_RULES = [  # row, label, value, note
    (3, "Decide at day", 30, "Verdicts other than RUNNING appear after this many days."),
    (4, "SCALE: min sell-through", 0.60, "Share of units sold (delivered) by the decision day."),
    (5, "SCALE: min real margin", 0.10, "Net profit ÷ revenue after RTO, returns, fees, ads, GST."),
    (6, "SCALE: min rating", 4.0, "Leave the rating blank if there are no reviews yet."),
    (7, "KILL: max sell-through", 0.30, "Below this by the decision day → liquidate at cost."),
    (8, "Sale GST % / purchase GST % (ITC)", 0.18, "Used for GST payable = output GST − input credit."),
]
F_COLS = [
    ("A", "Batch ID", "input"), ("B", "ASIN", "input"), ("C", "Product", "input"),
    ("D", "Channel (Amazon / Meesho)", "input"), ("E", "Start date", "input"),
    ("F", "Units bought", "input"), ("G", "Unit cost ex-GST incl. inbound (₹)", "input"),
    ("H", "Selling price incl. GST (₹)", "input"), ("I", "Units delivered (sold)", "input"),
    ("J", "RTO units", "input"), ("K", "Customer returns", "input"),
    ("L", "Damaged / unsellable", "input"), ("M", "Marketplace fees total (₹)", "input"),
    ("N", "Shipping total incl. RTO (₹)", "input"), ("O", "Ad spend (₹)", "input"),
    ("P", "Avg rating", "input"),
    ("Q", "Days running", "calc"), ("R", "Sell-through %", "calc"), ("S", "Revenue (₹)", "calc"),
    ("T", "Cost of units sold (₹)", "calc"), ("U", "Loss on damaged (₹)", "calc"),
    ("V", "GST payable (₹)", "calc"), ("W", "Net profit (₹)", "calc"), ("X", "Real margin %", "calc"),
    ("Y", "RTO %", "calc"), ("Z", "Return %", "calc"), ("AA", "Verdict", "calc"),
]


def flip_formulas(r):
    rule = lambda row: f"$B${row}"
    blank = f'$F{r}=""'
    f = {
        "Q": f'=IF(OR({blank},$E{r}=""),"",TODAY()-$E{r})',
        "R": f'=IF(OR({blank},$F{r}=0),"",N($I{r})/$F{r})',
        "S": f'=IF({blank},"",N($I{r})*N($H{r}))',
        "T": f'=IF({blank},"",N($I{r})*N($G{r}))',
        "U": f'=IF({blank},"",N($L{r})*N($G{r}))',
        "V": f'=IF({blank},"",$S{r}*{rule(8)}/(1+{rule(8)})-N($I{r})*N($G{r})*{rule(8)})',
        "W": f'=IF({blank},"",$S{r}-$T{r}-$U{r}-$V{r}-N($M{r})-N($N{r})-N($O{r}))',
        "X": f'=IF(OR({blank},N($S{r})=0),"",$W{r}/$S{r})',
        "Y": f'=IF(OR({blank},N($I{r})+N($J{r})=0),"",N($J{r})/(N($I{r})+N($J{r})))',
        "Z": f'=IF(OR({blank},N($I{r})=0),"",N($K{r})/N($I{r}))',
        "AA": (f'=IF({blank},"",IF($Q{r}<{rule(3)},'
               f'IF(AND($R{r}>={rule(4)},$X{r}<>"",$X{r}>={rule(5)}),"On track to SCALE — decide at day "&{rule(3)},'
               f'"RUNNING — decide at day "&{rule(3)}),'
               f'IF(AND($R{r}>={rule(4)},$X{r}<>"",$X{r}>={rule(5)},OR($P{r}="",$P{r}>={rule(6)})),"SCALE — reorder 2×",'
               f'IF(OR($R{r}<{rule(7)},AND($X{r}<>"",$X{r}<0)),"KILL — liquidate at cost",'
               f'"ITERATE — change ONE thing (price/images/title), rerun"))))'),
    }
    return f


def flip_values():
    rows = [["Flip tracker — one row per test batch. YELLOW = what actually happened. The verdict applies the "
             "rules below, which you fix BEFORE buying (changing them after seeing results defeats the test)."], []]
    by_row = {r: (lab, val, note) for r, lab, val, note in F_RULES}
    for r in range(3, 9):
        lab, val, note = by_row[r]
        rows.append([lab, val, note])
    rows += [[], ["Data rows below. Real margin includes RTO and return shipping (enter it in 'Shipping total'), "
                      "fees, ads, damaged stock and GST (output GST − input credit)."]]
    rows.append([h for _, h, _ in F_COLS])
    calc_cols = [c for c, _, k in F_COLS if k == "calc"]
    example = ["EX-001 (example — delete)", "EXAMPLE0001", "Steel lunch box", "Amazon", None,
               50, 70, 449, 31, 4, 2, 1, 4100, 2600, 1500, 4.2]
    rows.append(example + [flip_formulas(F_FIRST)[c] for c in calc_cols])
    for r in range(F_FIRST + 1, F_FIRST + 40):
        rows.append([""] * 16 + [flip_formulas(r)[c] for c in calc_cols])
    return rows


# ── create once ───────────────────────────────────────────────────────
def ensure_xlsx(wb):
    from datetime import date, timedelta
    from openpyxl.styles import Font, PatternFill, Alignment
    blue = Font(name="Arial", color="0000FF"); bold = Font(name="Arial", bold=True)
    yellow = PatternFill("solid", fgColor="FFFF00"); grey = PatternFill("solid", fgColor="EDEDED")
    made = []
    if QUOTES_TAB not in wb.sheetnames:
        ws = wb.create_sheet(QUOTES_TAB)
        for line in quotes_values():
            ws.append(line)
        _style(ws, Q_COLS, Q_FIRST - 1, Q_FIRST, Q_FIRST + 39, blue, bold, yellow, grey,
               pct={"L"}, inr={"G", "I", "K", "R"})
        ws["B3"].font, ws["B3"].fill = blue, yellow
        made.append(QUOTES_TAB)
    if FLIP_TAB not in wb.sheetnames:
        ws = wb.create_sheet(FLIP_TAB)
        for line in flip_values():
            ws.append(line)
        ws[f"E{F_FIRST}"] = date.today() - timedelta(days=35)
        _style(ws, F_COLS, F_FIRST - 1, F_FIRST, F_FIRST + 39, blue, bold, yellow, grey,
               pct={"R", "X", "Y", "Z"}, inr={"G", "H", "M", "N", "O", "S", "T", "U", "V", "W"})
        for r in range(F_FIRST, F_FIRST + 40):
            ws[f"E{r}"].number_format = "yyyy-mm-dd"
        for r, *_ in F_RULES:
            ws[f"B{r}"].font, ws[f"B{r}"].fill = blue, yellow
            ws[f"B{r}"].number_format = "0%" if r in (4, 5, 7, 8) else "0.0" if r == 6 else "0"
        made.append(FLIP_TAB)
    return made


def _style(ws, cols, hdr_row, first, last, blue, bold, yellow, grey, pct=(), inr=()):
    from openpyxl.styles import Font, Alignment
    for row in ws.iter_rows():
        for c in row:
            c.font = Font(name="Arial")
    ws["A1"].font = bold
    for c in ws[hdr_row]:
        c.font, c.fill = bold, grey
        c.alignment = Alignment(wrap_text=True, vertical="top")
    kinds = {letter: kind for letter, _, kind in cols}
    for letter, kind in kinds.items():
        ws.column_dimensions[letter].width = 16
        for r in range(first, last + 1):
            cell = ws[f"{letter}{r}"]
            if kind == "input":
                cell.font, cell.fill = blue, yellow
            if letter in pct:
                cell.number_format = "0.0%;(0.0%);-"
            elif letter in inr:
                cell.number_format = "₹#,##0;(₹#,##0);-"
    ws.column_dimensions["A"].width = 22
    ws.freeze_panes = ws[f"B{first}"]


def ensure_gsheet(wb):
    import gspread
    made = []
    for tab, values, ncols in ((QUOTES_TAB, quotes_values, len(Q_COLS)), (FLIP_TAB, flip_values, len(F_COLS))):
        try:
            wb.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = wb.add_worksheet(title=tab, rows=Q_LAST + 5, cols=ncols + 2)
            vals = values()
            if tab == FLIP_TAB:
                from datetime import date, timedelta
                vals[F_FIRST - 1][4] = (date.today() - timedelta(days=35)).isoformat()
            ws.update(range_name="A1", values=vals, value_input_option="USER_ENTERED")
            try:
                hdr = Q_FIRST - 1 if tab == QUOTES_TAB else F_FIRST - 1
                ws.format(f"A{hdr}:{(Q_COLS if tab == QUOTES_TAB else F_COLS)[-1][0]}{hdr}",
                          {"textFormat": {"bold": True}, "backgroundColor": {"red": .93, "green": .93, "blue": .93}})
                ws.freeze(rows=hdr)
            except Exception:
                pass
            made.append(tab)
    return made


def quoted_asins_from_values(values):
    """ASINs with at least one priced quote (ignores the example row)."""
    out = set()
    for line in values[Q_FIRST - 1:]:
        a = str(line[0]).strip() if line else ""
        if a and not a.startswith("EXAMPLE") and len(line) > 6 and str(line[6]).strip() not in ("", "None"):
            out.add(a)
    return out


def quoted_asins(store_wb=None, xlsx_path=None):
    try:
        if store_wb is not None:
            vals = store_wb.worksheet(QUOTES_TAB).get_all_values()
        else:
            import openpyxl, os
            if not xlsx_path or not os.path.exists(xlsx_path):
                return set()
            wb = openpyxl.load_workbook(xlsx_path)
            if QUOTES_TAB not in wb.sheetnames:
                return set()
            vals = [[c.value for c in row] for row in wb[QUOTES_TAB].iter_rows()]
        return quoted_asins_from_values(vals)
    except Exception:
        return set()


assert [r[0] if r else None for r in flip_values()][F_FIRST - 2] == "Batch ID", "FLIP header must sit right above F_FIRST"
assert [r[0] if r else None for r in quotes_values()][Q_FIRST - 2] == "ASIN (match CALCULATOR)", "QUOTES header misplaced"
