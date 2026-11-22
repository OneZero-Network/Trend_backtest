"""
Supplier quotes → landed cost → Amazon margin — v1.80. The layer Keepa cannot give a seller.

IndiaMART listing prices (sourcing.py) are TRIAGE: often GST-unclear, stale, or bait for an enquiry. A quote is what a
supplier will actually invoice. This module turns quotes into numbers a seller can act on:

  rfq_message     the exact questions to send each supplier, so every quote comes back comparable
  parse_quote     /quote A1 Shree Traders price=210 gst=18 moq=100 freight=1500 kg=0.45 dims=30x10x5 lead=7 written=yes
  landed          per-unit landed cost = price + packaging + freight ÷ MOQ (+ GST when the buyer cannot claim it back)
  compare         every supplier at the target selling price: Amazon fees, the published Easy Ship weight fee on the
                  chargeable (actual vs volumetric) weight, returns, profit, margin, cash tied up at MOQ, break-even units
  margin_xlsx     the same table as a spreadsheet with live formulas: the client changes the price or a quote, every
                  number follows. Fee constants sit on an Assumptions sheet with their source.

A quote is "written" only when the supplier sent it on letterhead, email or IndiaMART's quotation tool. The audit
report needs written quotes (audit.py), because a verbal number is not something a client can buy against.
"""
import math
import re
import shlex
from datetime import date

import pandas as pd

import fees

QUOTE_COLS = ["job_id", "supplier", "city", "url", "price", "gst_pct", "moq", "freight", "pack", "kg", "dims",
              "lead_days", "sample_inr", "valid_until", "written", "added", "notes"]
_KEYS = {"price": "price", "gst": "gst_pct", "moq": "moq", "freight": "freight", "pack": "pack", "kg": "kg",
         "dims": "dims", "lead": "lead_days", "sample": "sample_inr", "valid": "valid_until", "written": "written",
         "city": "city", "url": "url", "note": "notes"}
_NUM = {"price", "gst_pct", "moq", "freight", "pack", "kg", "lead_days", "sample_inr"}


def _f(v):
    try:
        x = float(str(v).replace(",", "").replace("₹", "").strip())
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def rfq_message(product, qty, deliver_to, sender="OneZero"):
    """What to send each supplier (IndiaMART enquiry, WhatsApp or email). A person sends it."""
    return (f"Namaste, we are evaluating {product} for regular orders. Please share a written quotation with:\n"
            f"1. Price per piece excluding GST, and the GST rate (HSN)\n"
            f"2. Minimum order quantity\n"
            f"3. Packed weight and carton size (L × B × H cm) per piece\n"
            f"4. Freight to {deliver_to} for {qty} pieces\n"
            f"5. Dispatch time after order\n"
            f"6. Sample price and how soon a sample can ship\n"
            f"7. Payment terms\n"
            f"8. Can you pack plain or with our label?\n"
            f"A quotation on your letterhead, by email or through IndiaMART's quotation is fine. Thank you. — {sender}")


def parse_quote(args, today):
    """['A1', 'Shree', 'Traders', 'price=210', 'moq=100', …] (or one string) → (row dict, error or '')."""
    text = " ".join(args) if isinstance(args, (list, tuple)) else str(args)
    try:
        toks = shlex.split(text)
    except ValueError:
        toks = text.split()
    if len(toks) < 3:
        return None, "Use: /quote A1 <supplier name> price=210 moq=100 [gst=18 freight=1500 pack=5 kg=0.45 dims=30x10x5 lead=7 written=yes]"
    row = {c: "" for c in QUOTE_COLS}
    row["job_id"], row["added"] = toks[0].upper(), str(today)
    name = []
    for t in toks[1:]:
        if "=" in t:
            k, v = t.split("=", 1)
            col = _KEYS.get(k.strip().lower())
            if not col:
                return None, f"Unknown field '{k}'. Fields: " + ", ".join(sorted(_KEYS))
            if col in _NUM and _f(v) is None:
                return None, f"{k} must be a number (got '{v}')"
            if col == "dims" and not fees.parse_dims(v):
                return None, f"dims must look like 30x10x5 (cm), got '{v}'"
            row[col] = v.strip()
        else:
            name.append(t)
    row["supplier"] = " ".join(name).strip()[:80]
    if not row["supplier"]:
        return None, "Name the supplier: /quote A1 Shree Traders price=210 moq=100"
    if _f(row["price"]) is None or _f(row["moq"]) is None or _f(row["moq"]) <= 0:
        return None, "price= and moq= are required"
    row["written"] = "yes" if str(row["written"]).lower() in ("yes", "y", "1", "true") else "no"
    if row["gst_pct"] == "":
        row["gst_pct"], row["notes"] = "18", (row["notes"] + " · GST rate not given: 18% assumed").strip(" ·")
    return row, ""


def landed(q, gst_registered=True):
    """Per-unit landed cost (₹, ex-GST when registered). → dict with the parts."""
    price, moq = _f(q.get("price")) or 0.0, max(_f(q.get("moq")) or 1.0, 1.0)
    pack, freight, gst = _f(q.get("pack")) or 0.0, _f(q.get("freight")) or 0.0, (_f(q.get("gst_pct")) or 0.0) / 100
    tax = 0.0 if gst_registered else price * gst                     # no input tax credit without GST registration
    unit = price + pack + freight / moq + tax
    return {"price": price, "pack": pack, "freight": freight, "freight_unit": round(freight / moq, 2),
            "gst_pct": gst * 100, "gst_cost": round(tax, 2), "landed": round(unit, 2), "moq": int(moq)}


def compare(quotes, target_price, channel="easy_ship", gst_registered=True, cfg=None):
    """Every supplier at the target selling price. → list of dicts, best margin first."""
    cfg = cfg or fees.config()
    out = []
    for _, q in (quotes.iterrows() if isinstance(quotes, pd.DataFrame) else enumerate(quotes)):
        q = dict(q)
        L = landed(q, gst_registered)
        dims = fees.parse_dims(q.get("dims"))
        kg = _f(q.get("kg"))
        e = fees.economics(float(target_price), cost=L["landed"], kg=kg, dims=dims, cfg=cfg, channel=channel)
        profit = e.get("profit")
        capital = L["landed"] * L["moq"]
        out.append({"supplier": q.get("supplier"), "city": q.get("city", ""), "written": q.get("written") == "yes",
                    "lead_days": _f(q.get("lead_days")), "valid_until": q.get("valid_until", ""), **L,
                    "kg": kg, "dims": dims, "chargeable_kg": e["chargeable_kg"], "ship": e["ship"],
                    "fees": e["fees"], "profit": profit, "margin": e.get("margin"), "capital": round(capital),
                    "breakeven_units": (math.ceil(capital / profit) if profit and profit > 0 else None),
                    "econ": e, "notes": q.get("notes", "")})
    out.sort(key=lambda r: (r["margin"] is None, -(r["margin"] or 0), r["capital"]))
    return out


def verdict(rows, target=0.25):
    """Go / Go with conditions / No-go — judged on WRITTEN quotes only."""
    w = [r for r in rows if r["written"] and r["margin"] is not None]
    if not w:
        return "No verdict", "no written quote with a known weight yet"
    best = max(w, key=lambda r: r["margin"])
    m = best["margin"]
    if m >= target:
        return "Go", f"{best['supplier']} leaves {m:.0%} after Amazon fees and shipping (target {target:.0%})"
    if m >= 0.10:
        return "Go with conditions", f"best written quote leaves {m:.0%}: negotiate price or MOQ, or sell higher"
    return "No-go", f"best written quote leaves {m:.0%} — below 10% after fees and shipping"


# ── the client's spreadsheet (live formulas) ─────────────────────────
def margin_xlsx(path, job, rows, gst_registered=True, channel="easy_ship", cfg=None):
    """One sheet per job: inputs at the top, one row per supplier, every derived cell a formula."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    cfg = cfg or fees.config()
    wb = Workbook()
    ws = wb.active
    ws.title = "Margin sheet"
    A = wb.create_sheet("Assumptions")
    bold, head = Font(bold=True), PatternFill("solid", fgColor="EEEEEE")
    inp = PatternFill("solid", fgColor="FFF7D6")
    a, b, c, d, e = fees.SHIP_TABLE.get(channel, fees.SHIP_TABLE["easy_ship"])
    assumptions = [
        ("Weight fee ≤ 0.5 kg (₹)", a, fees.SHIP_BASIS + f" — {channel}"),
        ("Weight fee ≤ 1 kg (₹)", b, ""), ("Weight fee ≤ 2 kg (₹)", c, ""),
        ("Weight fee per kg, 2–5 kg (₹)", d, ""), ("Weight fee per kg above 5 kg (₹)", e, ""),
        ("Closing fee ≤ ₹300", cfg["closing_0_300"], "published, highest fee group (after the 7 Sep 2026 rise)"),
        ("Closing fee ₹301–500", cfg["closing_301_500"], "published, highest fee group"),
        ("Closing fee ₹501–1,000", cfg["closing_501_1000"], "ASSUMED — confirm in Seller Central's fee preview"),
        ("Closing fee above ₹1,000", cfg["closing_above_1000"], "published ('*' categories)"),
        ("Referral fee at ₹1,000 and above", cfg["referral_above_1000"], "ASSUMED — category rate; 0% below ₹1,000 (16 Mar 2026)"),
        ("GST rate on the sale", cfg["gst"], "ASSUMED — use your product's HSN rate"),
        ("Returns and undelivered share", cfg["rto"], "ASSUMED — each costs shipping out and back"),
        ("GST registered (claims input tax credit)", "Yes" if gst_registered else "No", "No → supplier GST becomes a cost"),
    ]
    A.append(["Assumption", "Value", "Source / note"])
    for x in assumptions:
        A.append(list(x))
    for col in "ABC":
        A.column_dimensions[col].width = {"A": 40, "B": 12, "C": 70}[col]
    for cell in A[1]:
        cell.font, cell.fill = bold, head
    R = lambda i: f"Assumptions!$B${i + 2}"                         # assumption i (0-based) → its cell
    ws["A1"] = f"{job.get('product', '')} — sourcing & margin sheet"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Prepared {date.today().isoformat()} for {job.get('client', '')}. Yellow cells are inputs; change them and every number follows."
    ws["A4"], ws["B4"] = "Selling price on Amazon (₹, incl. GST)", float(job.get("target_price") or 0)
    ws["A5"], ws["B5"] = "Price excluding GST (₹)", f"=B4/(1+{R(10)})"
    ws["A6"], ws["B6"] = "Referral fee (₹)", f"=IF(B4<1000,0,B4*{R(9)})"
    ws["A7"], ws["B7"] = "Closing fee (₹)", f"=IF(B4<=300,{R(5)},IF(B4<=500,{R(6)},IF(B4<=1000,{R(7)},{R(8)})))"
    ws["A8"], ws["B8"] = "Returns multiplier on shipping", f"=1+2*{R(11)}/(1-{R(11)})"
    ws["B4"].fill = inp
    for r_ in range(4, 9):
        ws[f"A{r_}"].font = bold
    hdr = ["Supplier", "Written quote", "Price ex-GST (₹)", "GST %", "MOQ", "Freight for MOQ (₹)", "Packaging / unit (₹)",
           "Landed / unit (₹)", "Actual kg", "L cm", "B cm", "H cm", "Chargeable kg", "Amazon weight fee (₹)",
           "Profit / unit (₹)", "Margin", "Cash at MOQ (₹)", "Break-even units", "Dispatch days", "Valid until"]
    top = 10
    for j, h in enumerate(hdr, 1):
        cell = ws.cell(row=top, column=j, value=h)
        cell.font, cell.fill = bold, head
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for i, r in enumerate(rows):
        n = top + 1 + i
        dims = r.get("dims") or (None, None, None)
        vals = [r["supplier"], "yes" if r["written"] else "no", r["price"], r["gst_pct"], r["moq"], r["freight"], r["pack"]]
        for j, v in enumerate(vals, 1):
            ws.cell(row=n, column=j, value=v)
        ws.cell(row=n, column=8, value=f"=C{n}+G{n}+F{n}/E{n}+IF({R(12)}=\"No\",C{n}*D{n}/100,0)")
        ws.cell(row=n, column=9, value=r["kg"])
        for j, v in zip((10, 11, 12), dims):
            ws.cell(row=n, column=j, value=v)
        ws.cell(row=n, column=13, value=f"=IF(AND(I{n}=\"\",J{n}=\"\"),\"\",MAX(0.5,N(I{n}),N(J{n})*N(K{n})*N(L{n})/5000))")
        ws.cell(row=n, column=14, value=(f"=IF(M{n}=\"\",\"\",IF(M{n}<=0.5,{R(0)},IF(M{n}<=1,{R(1)},IF(M{n}<=2,{R(2)},"
                                         f"IF(M{n}<=5,{R(2)}+{R(3)}*ROUNDUP(M{n}-2,0),{R(2)}+3*{R(3)}+{R(4)}*ROUNDUP(M{n}-5,0))))))"))
        ws.cell(row=n, column=15, value=f"=IF(N{n}=\"\",\"\",$B$5-$B$6-$B$7-N{n}*$B$8-H{n})")
        ws.cell(row=n, column=16, value=f"=IF(O{n}=\"\",\"\",O{n}/$B$5)")
        ws.cell(row=n, column=17, value=f"=H{n}*E{n}")
        ws.cell(row=n, column=18, value=f"=IF(O{n}=\"\",\"\",IF(O{n}>0,ROUNDUP(Q{n}/O{n},0),\"never\"))")
        ws.cell(row=n, column=19, value=r["lead_days"])
        ws.cell(row=n, column=20, value=r["valid_until"])
        for j in (3, 4, 5, 6, 7, 9, 10, 11, 12):
            ws.cell(row=n, column=j).fill = inp
        ws.cell(row=n, column=16).number_format = "0%"
        for j in (8, 14, 15, 17):
            ws.cell(row=n, column=j).number_format = "#,##0.00"
    widths = [28, 9, 11, 7, 8, 12, 11, 12, 9, 7, 7, 7, 10, 12, 12, 9, 13, 11, 9, 11]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=top, column=j).column_letter].width = w
    ws.row_dimensions[top].height = 45
    ws.cell(row=top + len(rows) + 2, column=1,
            value="Profit = price ex-GST − referral − closing − weight fee × returns multiplier − landed cost. "
                  "GST on Amazon's fees is not counted: a registered seller claims it back.")
    ws.freeze_panes = ws.cell(row=top + 1, column=2)
    wb.save(path)
    return path
