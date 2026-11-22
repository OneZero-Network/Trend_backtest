"""
Done-For-You Sourcing & Margin Audit — v1.80. A one-time paid service for D2C brands and marketplace sellers:
for one product (₹10,000) or one category up to 5 products (₹20,000), deliver demand evidence from our daily data,
at least 3 supplier quotes (at least 2 in writing), and a landed Easy Ship margin sheet with live formulas.

Why it is built this way:
  * "Verified" is a promise. `build` refuses until the job has AUDIT_MIN_QUOTES quotes and AUDIT_MIN_WRITTEN written
    ones, so the report can never go out on listing prices alone.
  * No inventory, no shipping to customers: none of drop shipping's GST / returns / cancellation exposure. The risk
    that remains is being wrong → every number carries its basis, and the report says what it does not cover.
  * It is labour. Hours are logged per job (/audit hours) and the Live Floor shows ₹ per hour, so a busy month that
    pays ₹600 an hour is visible.

Lifecycle (owner commands in the bot; the Operator does the rest each night or on /refresh):
  /audit new <client> | <product or ASIN> | <selling price ₹> [| sku|category]
  /audit email A1 buyer@brand.in       /audit fee A1 [amount]  → 50% advance link (Razorpay) or instructions
  /audit paid A1 [advance|balance]     /quote A1 <supplier> price=… moq=… [gst= freight= pack= kg= dims= lead= written=yes]
  /audit hours A1 2.5                  /audit build A1 → report HTML + margin XLSX to you for review
  /audit deliver A1 → to the client by email (or to you to forward) + balance link     /audit lost A1 <reason>
"""
import html
import os
import re
from datetime import date, timedelta

import pandas as pd

import compliance
import fees
import quotes as Q

FEE_SKU = int(os.environ.get("AUDIT_FEE_SKU") or 10000)
FEE_CATEGORY = int(os.environ.get("AUDIT_FEE_CATEGORY") or 20000)
ADVANCE = float(os.environ.get("AUDIT_ADVANCE_PCT") or 0.5)
MIN_QUOTES = int(os.environ.get("AUDIT_MIN_QUOTES") or 3)
MIN_WRITTEN = int(os.environ.get("AUDIT_MIN_WRITTEN") or 2)
TARGET_PER_MONTH = int(os.environ.get("AUDIT_TARGET_PER_MONTH") or 3)
DELIVER_TO = (os.environ.get("AUDIT_DELIVER_TO") or "your warehouse city").strip()

AUDIT_COLS = ["job_id", "client", "email", "phone", "product", "asin", "category", "target_price", "tier", "fee",
              "status", "created", "status_at", "adv_link_id", "adv_url", "adv_status", "bal_link_id", "bal_url",
              "bal_status", "paid_inr", "hours", "built", "delivered", "verdict", "notes", "gst_registered", "channel", "generic"]
STATUSES = ("new", "fee_sent", "advance_paid", "quotes_in", "report_ready", "delivered", "closed", "lost")
H = lambda x: html.escape(str(x if x is not None else ""), quote=True)


def _f(v):
    try:
        x = float(str(v).replace(",", "").replace("₹", "").strip())
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def frame(df, cols=AUDIT_COLS):
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy().astype(object)
    for c in cols:
        if c not in df:
            df[c] = ""
    return df[cols + [c for c in df.columns if c not in cols]].fillna("").astype(str)


def _row(audits, jid):
    m = audits.index[audits["job_id"].str.upper() == str(jid).upper()]
    return m[0] if len(m) else None


# ── demand evidence + supplier shortlist ─────────────────────────────
def evidence(job, signals):
    """Our observed data for the product (by ASIN, else by title words). → dict or None when we have nothing."""
    if signals is None or not len(signals):
        return None
    s = signals
    if job.get("asin") and job["asin"] in set(s["asin"]):
        r = s[s["asin"] == job["asin"]].iloc[0]
    else:
        words = [w for w in re.findall(r"[a-z0-9]+", str(job.get("product", "")).lower()) if len(w) > 2][:4]
        if not words:
            return None
        t = s["title"].astype(str).str.lower()
        hit = s[[all(w in x for w in words) for x in t]]
        if hit.empty:
            return None
        r = hit.assign(_d=pd.to_numeric(hit.get("durable_score"), errors="coerce")).sort_values("_d", ascending=False).iloc[0]
    d, w = _f(r.get("durable_days")), _f(r.get("durable_window"))
    cat = str(r.get("category_slug") or "")
    peers = s[s["category_slug"].astype(str) == cat] if cat else s.iloc[0:0]
    return {"asin": r["asin"], "title": str(r.get("title"))[:120], "rank": _f(r.get("bs_rank_today")),
            "median": _f(r.get("median_rank")), "days": f"{int(d)} of {int(w)}" if d is not None and w else "",
            "durable": str(r.get("durable")).lower() == "true", "falling": str(r.get("exit_flag") or "") == "falling",
            "exit_note": str(r.get("exit_note") or ""), "reviews": _f(r.get("reviews")), "rpd": _f(r.get("reviews_per_day")),
            "price": _f(r.get("price_inr")), "category": cat, "peers_on_list": int(len(peers)),
            "peers_lasting": int((peers.get("durable", pd.Series(dtype=str)).astype(str).str.lower() == "true").sum())}


def shortlist(job, sourcing_rows, n=8):
    """IndiaMART listings our sourcing run already found for this ASIN (kept = comparable per-piece price)."""
    if sourcing_rows is None or not len(sourcing_rows) or not job.get("asin"):
        return []
    s = sourcing_rows[(sourcing_rows["asin"].astype(str) == job["asin"])]
    if "kept" in s:
        s = s[s["kept"].astype(str).str.lower().isin(["true", "1"])]
    s = s.assign(_p=pd.to_numeric(s.get("per_piece"), errors="coerce")).sort_values("_p")
    s = s.drop_duplicates("supplier") if "supplier" in s else s
    return [{"supplier": r.get("supplier", ""), "city": r.get("city", ""), "per_piece": _f(r.get("per_piece")),
             "moq": r.get("moq", ""), "url": r.get("url", "")} for _, r in s.head(n).iterrows()]


def enrich(audits, signals):
    """A job opened with an ASIN gets the product's title and category from our data (for the report and the RFQ)."""
    a = frame(audits)
    if signals is None or not len(signals) or a.empty:
        return a
    by = signals.drop_duplicates("asin").set_index("asin")
    for i, r in a.iterrows():
        if r["asin"] and r["asin"] in by.index and (r["product"] == r["asin"] or not r["category"]):
            t = re.split(r"\s[|–]\s|,\s|\s\(", str(by.at[r["asin"], "title"]))[0].strip()[:90]
            if r["product"] == r["asin"] and t:
                a.at[i, "product"] = t
            a.at[i, "category"] = str(by.at[r["asin"], "category_slug"] or "")
    return a


def generic_name(product):
    """What to call the product to a supplier: no brand (asking a wholesaler for a brand invites copies)."""
    import sourcing
    g = sourcing.make_query(product)
    return g or str(product)


def search_link(product):
    import sourcing
    return sourcing.search_url(sourcing.make_query(product) or product)


# ── commands ─────────────────────────────────────────────────────────
def command(audits, qdf, args, today, rzp=None, sender="OneZero"):
    """/audit … and /quote … → (audits, quotes, reply, actions). actions: [('build'|'deliver', job_id)] for the
    Operator, which makes and sends files."""
    audits, qdf = frame(audits), frame(qdf, Q.QUOTE_COLS)
    acts = []
    if not args:
        return audits, qdf, "Use /audit new <client> | <product or ASIN> | <price>", acts
    if args[0] == "quote":
        row, err = Q.parse_quote(args[1:], today)
        if err:
            return audits, qdf, err, acts
        i = _row(audits, row["job_id"])
        if i is None:
            return audits, qdf, f"No audit {row['job_id']}.", acts
        qdf = pd.concat([qdf, pd.DataFrame([row])], ignore_index=True)
        jq = qdf[qdf["job_id"] == row["job_id"]]
        nw = int((jq["written"] == "yes").sum())
        if len(jq) >= MIN_QUOTES and nw >= MIN_WRITTEN and audits.at[i, "status"] in ("new", "fee_sent", "advance_paid"):
            audits.at[i, "status"], audits.at[i, "status_at"] = "quotes_in", str(today)
        need = max(0, MIN_QUOTES - len(jq)), max(0, MIN_WRITTEN - nw)
        return audits, qdf, (f"{row['job_id']}: quote from {row['supplier']} saved ({len(jq)} quotes, {nw} written)."
                             + (f" Needs {need[0]} more quote(s), {need[1]} more written." if any(need) else
                                f" Ready: /audit build {row['job_id']}")), acts
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "new":
        parts = [p.strip() for p in " ".join(args[2:]).split("|")]
        if len(parts) < 3 or _f(parts[2]) is None:
            return audits, qdf, "Use: /audit new <client> | <product or ASIN> | <selling price ₹> [| sku|category]", acts
        client, prod, price = parts[0], parts[1], _f(parts[2])
        tier = "category" if len(parts) > 3 and parts[3].lower().startswith("cat") else "sku"
        asin = prod.upper() if re.fullmatch(r"[A-Za-z0-9]{10}", prod) and any(ch.isdigit() for ch in prod) else ""
        jid = f"A{len(audits) + 1}"
        audits = pd.concat([audits, pd.DataFrame([{**{c: "" for c in AUDIT_COLS}, "job_id": jid, "client": client[:60],
                            "product": prod[:120], "asin": asin, "target_price": str(price), "tier": tier,
                            "fee": str(FEE_CATEGORY if tier == "category" else FEE_SKU), "status": "new",
                            "created": str(today), "status_at": str(today), "hours": "0", "paid_inr": "0",
                            "gst_registered": "yes", "channel": "easy_ship"}])], ignore_index=True)
        return audits, qdf, (f"{jid} opened for {client}: {prod} at ₹{price:,.0f} ({tier}, ₹{(FEE_CATEGORY if tier == 'category' else FEE_SKU):,}). "
                             f"Tonight's report has the supplier shortlist and the RFQ text. Next: /audit email {jid} <address>, "
                             f"then /audit fee {jid}"), acts
    jid = args[2] if len(args) > 2 else ""
    i = _row(audits, jid)
    if i is None:
        return audits, qdf, f"No audit {jid or '(missing id)'}. /audits lists them.", acts
    name = audits.at[i, "client"]
    rest = args[3:]
    if sub == "email":
        v = rest[0] if rest else ""
        if not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", v):
            return audits, qdf, f"That doesn't look like an email address: {v}", acts
        audits.at[i, "email"] = v
        return audits, qdf, f"{jid} {name}: email saved.", acts
    if sub == "name":
        g = " ".join(rest).strip()
        if len(g) < 3:
            return audits, qdf, "Use: /audit name A1 doorway pull up bar", acts
        audits.at[i, "generic"] = g[:80]
        return audits, qdf, f"{jid}: suppliers will be asked for '{g[:80]}'.", acts
    if sub == "gst":
        audits.at[i, "gst_registered"] = "no" if rest and rest[0].lower().startswith("n") else "yes"
        return audits, qdf, f"{jid}: client GST registered = {audits.at[i, 'gst_registered']}.", acts
    if sub == "channel":
        ch = (rest[0].lower() if rest else "")
        if ch not in fees.SHIP_TABLE:
            return audits, qdf, "Channel: easy_ship | fc_regional | fc_national", acts
        audits.at[i, "channel"] = ch
        return audits, qdf, f"{jid}: margins on {ch}.", acts
    if sub == "fee":
        amt = _f(rest[0]) if rest else _f(audits.at[i, "fee"])
        if not amt:
            return audits, qdf, "Use: /audit fee A1 [amount ₹]", acts
        audits.at[i, "fee"] = str(int(amt))
        adv = round(amt * ADVANCE)
        if rzp is not None and rzp.on:
            lid, url, _ = rzp.create(adv, f"{sender} sourcing & margin audit {jid} — {int(ADVANCE * 100)}% advance",
                                     f"{jid}-adv-{today}", name=name, email=audits.at[i, "email"], phone=audits.at[i, "phone"])
            audits.loc[i, ["adv_link_id", "adv_url", "adv_status", "status", "status_at"]] = [lid, url, "created", "fee_sent", str(today)]
            return audits, qdf, f"{jid}: ₹{adv:,} advance link → {url}" + (" (Razorpay emailed it)" if audits.at[i, "email"] else " — send it on WhatsApp"), acts
        audits.loc[i, ["status", "status_at"]] = ["fee_sent", str(today)]
        return audits, qdf, f"{jid}: send a ₹{adv:,} advance link from the Razorpay dashboard, then /audit paid {jid} advance", acts
    if sub == "paid":
        part = (rest[0].lower() if rest else "advance")
        fee = _f(audits.at[i, "fee"]) or 0
        if part.startswith("b") or part.startswith("f"):
            audits.at[i, "paid_inr"], audits.at[i, "bal_status"] = str(int(fee)), "paid"
            audits.loc[i, ["status", "status_at"]] = ["closed" if audits.at[i, "status"] == "delivered" else audits.at[i, "status"], str(today)]
            return audits, qdf, f"{jid} {name}: paid in full (₹{fee:,.0f}).", acts
        audits.at[i, "paid_inr"], audits.at[i, "adv_status"] = str(int(round(fee * ADVANCE))), "paid"
        if audits.at[i, "status"] in ("new", "fee_sent"):
            audits.loc[i, ["status", "status_at"]] = ["advance_paid", str(today)]
        return audits, qdf, f"{jid} {name}: advance paid. Send the RFQs (tonight's report has them) and log quotes with /quote {jid} …", acts
    if sub == "hours":
        h = _f(rest[0]) if rest else None
        if h is None or h <= 0:
            return audits, qdf, "Use: /audit hours A1 2.5", acts
        audits.at[i, "hours"] = str(round((_f(audits.at[i, "hours"]) or 0) + h, 2))
        return audits, qdf, f"{jid}: {audits.at[i, 'hours']} hours logged.", acts
    if sub == "build":
        acts.append(("build", audits.at[i, "job_id"]))
        return audits, qdf, f"{jid}: building the report and margin sheet.", acts
    if sub == "deliver":
        if audits.at[i, "status"] not in ("report_ready", "delivered"):
            return audits, qdf, f"{jid}: build the report first (/audit build {jid}) and read it.", acts
        acts.append(("deliver", audits.at[i, "job_id"]))
        return audits, qdf, f"{jid}: delivering to {audits.at[i, 'email'] or 'you (no client email)'}.", acts
    if sub == "lost":
        audits.loc[i, ["status", "status_at", "notes"]] = ["lost", str(today), (audits.at[i, "notes"] + " | lost: " + " ".join(rest)).strip(" |")]
        return audits, qdf, f"{jid} {name}: marked lost.", acts
    return audits, qdf, "Unknown /audit command. new · email · fee · paid · hours · build · deliver · lost · gst · channel", acts


# ── the deliverable ──────────────────────────────────────────────────
def ready(job, qdf):
    jq = frame(qdf, Q.QUOTE_COLS)
    jq = jq[jq["job_id"] == job["job_id"]]
    nw = int((jq["written"] == "yes").sum())
    if len(jq) < MIN_QUOTES or nw < MIN_WRITTEN:
        return False, f"needs {max(0, MIN_QUOTES - len(jq))} more quote(s) and {max(0, MIN_WRITTEN - nw)} more written (has {len(jq)}, {nw} written)", jq
    return True, "", jq


def build(job, qdf, signals, out_dir, cfg=None, cat_names=None):
    """→ (html_path, xlsx_path, verdict tuple) or (None, None, reason)."""
    ok, why, jq = ready(job, qdf)
    if not ok:
        return None, None, why
    cfg = cfg or fees.config()
    reg = job.get("gst_registered", "yes") != "no"
    ch = job.get("channel") or "easy_ship"
    price = float(job["target_price"])
    rows = Q.compare(jq, price, channel=ch, gst_registered=reg, cfg=cfg)
    v = Q.verdict(rows, cfg["target_margin"])
    ev = evidence(job, signals)
    if ev:
        ev["category"] = (cat_names or {}).get(ev["category"], ev["category"])
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"audit_{job['job_id']}_{re.sub(r'[^A-Za-z0-9]+', '_', job['client'])[:30]}")
    xlsx = Q.margin_xlsx(base + ".xlsx", job, rows, gst_registered=reg, channel=ch, cfg=cfg)
    best = next((r for r in rows if r["written"] and r["margin"] is not None), None)
    sens = []
    if best:
        for k in (0.9, 1.0, 1.1):
            e = fees.economics(price * k, cost=best["landed"], kg=best["kg"], dims=best["dims"], cfg=cfg, channel=ch)
            sens.append((price * k, e.get("profit"), e.get("margin")))
    cf = compliance.compliance_flags(job.get("product", ""), (ev or {}).get("category", ""))
    flags = [x.strip() for x in cf.replace("REVIEW:", "").split(";") if x.strip()] if cf.startswith("REVIEW") else []
    flags.append("Packaging must carry the Legal Metrology declarations (MRP, manufacturer or packer, country of origin, "
                 "net quantity, consumer care) — ask the supplier to print them or label each unit.")
    page = report_html(job, rows, v, ev, sens, flags, cfg)
    with open(base + ".html", "w", encoding="utf-8") as fh:
        fh.write(page)
    return base + ".html", xlsx, v


def report_html(job, rows, v, ev, sens, flags, cfg):
    td = "padding:6px 8px;border-bottom:1px solid #e5e5e5;font:13px Arial,sans-serif;color:#222;vertical-align:top"
    th = td + ";font-weight:bold;background:#f5f5f5;color:#555"
    p = lambda t: f"<p style='font:15px Arial,sans-serif;color:#222;line-height:1.5'>{t}</p>"
    h2 = lambda t: f"<h2 style='font:bold 18px Arial,sans-serif;color:#111;margin:22px 0 6px'>{H(t)}</h2>"

    def table(head, body):
        return ("<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;margin:6px 0 16px'><tr>"
                + "".join(f"<th align='left' style='{th}'>{H(x)}</th>" for x in head) + "</tr>"
                + "".join("<tr>" + "".join(f"<td style='{td}'>{H(c)}</td>" for c in r) + "</tr>" for r in body) + "</table></div>")
    money = lambda x: "–" if x is None else f"₹{x:,.0f}"
    pct = lambda x: "–" if x is None else f"{x:.0%}"
    color = {"Go": "#1a7f37", "Go with conditions": "#9a6700", "No-go": "#cf222e"}.get(v[0], "#555")
    parts = [f"<h1 style='font:bold 22px Arial,sans-serif;color:#111'>{H(job['product'])}: sourcing &amp; margin audit</h1>",
             p(f"For {H(job['client'])} · selling price ₹{float(job['target_price']):,.0f} on Amazon.in · "
               f"{'Easy Ship' if (job.get('channel') or 'easy_ship') == 'easy_ship' else H(job.get('channel'))} · prepared {date.today().isoformat()}"),
             f"<div style='border:2px solid {color};border-radius:8px;padding:12px 16px;margin:12px 0'>"
             f"<div style='font:bold 20px Arial,sans-serif;color:{color}'>{H(v[0])}</div>"
             f"<div style='font:15px Arial,sans-serif;color:#222;margin-top:4px'>{H(v[1])}</div></div>"]
    parts.append(h2("Demand: what our daily data shows"))
    if ev:
        bits = [f"On Amazon.in's {H(ev['category'])} bestseller list: #{int(ev['rank']) if ev['rank'] else '–'} today"
                + (f", on the list {H(ev['days'])} days" if ev["days"] else "")
                + (f", median rank {ev['median']:.0f}" if ev["median"] else "") + "."]
        if ev["falling"]:
            bits.append(f"<b>Warning:</b> it {H(ev['exit_note'])}. In our data, products that fall like that left the top 100 "
                        f"within a week far more often than steady ones.")
        bits.append(f"{ev['peers_on_list']} products are on that list; {ev['peers_lasting']} of them lasted most of the last 14 days."
                    + (f" The listing we matched has {int(ev['reviews']):,} reviews." if ev["reviews"] else ""))
        parts.append(p(" ".join(bits)))
    else:
        parts.append(p("This product is not on the bestseller lists we track, so demand is <b>not</b> evidenced by our data. "
                       "The margins below hold only if it sells."))
    parts.append(h2("Supplier quotes, landed and through Amazon's fees"))
    parts.append(table(["Supplier", "Written", "Landed / unit", "MOQ", "Chargeable kg", "Weight fee", "Profit / unit", "Margin",
                        "Cash at MOQ", "Break-even units", "Dispatch"],
                       [[r["supplier"] + (f" ({r['city']})" if r["city"] else ""), "yes" if r["written"] else "no",
                         money(r["landed"]), f"{r['moq']:,}", (f"{r['chargeable_kg']:g}" if r["chargeable_kg"] else "unknown"),
                         money(r["ship"]), money(r["profit"]), pct(r["margin"]), money(r["capital"]),
                         (f"{r['breakeven_units']:,}" if r["breakeven_units"] else "–"),
                         (f"{r['lead_days']:.0f} days" if r["lead_days"] else "–")] for r in rows]))
    if any(not r["written"] for r in rows):
        parts.append(p("<i>Quotes marked 'no' were given verbally or as a listing price. They are shown for comparison; the "
                       "verdict uses written quotes only.</i>"))
    if sens:
        parts.append(h2("If the price moves (best written quote)"))
        parts.append(table(["Selling price", "Profit / unit", "Margin"], [[money(a), money(b), pct(c)] for a, b, c in sens]))
    parts.append(h2("What the numbers include"))
    ref, rb = fees.referral_pct(float(job["target_price"]), cfg)
    clo, cb = fees.closing_fee(float(job["target_price"]), cfg)
    parts.append(p(f"Referral fee {ref:.0%} ({H(rb)}); closing fee ₹{clo} ({H(cb)}); Amazon's weight-handling fee on the "
                   f"higher of actual and volumetric weight ({H(fees.SHIP_BASIS)}); {cfg['rto']:.0%} of orders returned or "
                   f"undelivered, each costing shipping both ways (assumed); GST on the sale at {cfg['gst']:.0%} (assumed: use "
                   f"your HSN rate). GST on Amazon's fees is not counted because a registered seller claims it back."
                   + ("" if job.get("gst_registered", "yes") != "no" else " You told us you are not GST registered, so the "
                      "supplier's GST is counted as a cost.")))
    if flags:
        parts.append(h2("Check before you buy"))
        parts.append("<ul>" + "".join(f"<li style='font:14px Arial,sans-serif'>{H(f)}</li>" for f in flags) + "</ul>")
    parts.append(h2("What this audit does not cover"))
    parts.append("<ul>" + "".join(f"<li style='font:14px Arial,sans-serif;line-height:1.5'>{x}</li>" for x in [
        "Sales volume: a bestseller rank shows where a listing sits, not how many units it sells.",
        "Advertising cost, which most new listings need.",
        "Quotes expire: each supplier's validity date is in the margin sheet.",
        "Product quality: order and check a sample before any MOQ.",
        "Your exact fee group: confirm in Seller Central's fee preview before listing."]) + "</ul>")
    parts.append(p("<i>The attached spreadsheet holds every number above with live formulas: change the price or a quote and "
                   "it recalculates.</i>"))
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>Audit {H(job['job_id'])} — {H(job['product'])}</title></head><body style='margin:0;padding:16px;background:#fff'>"
            "<div style='max-width:760px;margin:0 auto'>" + "".join(parts) + "</div></body></html>")


# ── money and the month ──────────────────────────────────────────────
def payments(audits, today, rzp):
    """Poll advance / balance links. → (audits, lines)."""
    audits = frame(audits)
    lines = []
    if rzp is None or not rzp.on:
        return audits, lines
    for i, r in audits.iterrows():
        for kind, idc, stc in (("advance", "adv_link_id", "adv_status"), ("balance", "bal_link_id", "bal_status")):
            if r[idc] and r[stc] not in ("paid", "expired", "cancelled"):
                try:
                    s = rzp.status(r[idc])
                except Exception as exc:
                    lines.append(f"⚠️ {r['job_id']}: payment check failed ({exc})")
                    continue
                audits.at[i, stc] = s
                fee = _f(r["fee"]) or 0
                if s == "paid" and kind == "advance":
                    audits.at[i, "paid_inr"] = str(int(round(fee * ADVANCE)))
                    if r["status"] in ("new", "fee_sent"):
                        audits.loc[i, ["status", "status_at"]] = ["advance_paid", str(today)]
                    lines.append(f"✅ {r['job_id']} {r['client']} paid the advance. Send the RFQs.")
                elif s == "paid":
                    audits.at[i, "paid_inr"] = str(int(fee))
                    audits.loc[i, ["status", "status_at"]] = ["closed", str(today)]
                    lines.append(f"✅ {r['job_id']} {r['client']} paid the balance. Audit closed.")
    return audits, lines


def balance_link(audits, i, today, rzp, sender="OneZero"):
    fee, paid = _f(audits.at[i, "fee"]) or 0, _f(audits.at[i, "paid_inr"]) or 0
    due = int(round(fee - paid))
    if due <= 0:
        return audits, ""
    if rzp is not None and rzp.on:
        lid, url, _ = rzp.create(due, f"{sender} sourcing & margin audit {audits.at[i, 'job_id']} — balance",
                                 f"{audits.at[i, 'job_id']}-bal-{today}", name=audits.at[i, "client"], email=audits.at[i, "email"])
        audits.loc[i, ["bal_link_id", "bal_url", "bal_status"]] = [lid, url, "created"]
        return audits, f"balance ₹{due:,} → {url}"
    return audits, f"send a ₹{due:,} balance link, then /audit paid {audits.at[i, 'job_id']} balance"


def month(audits, today):
    """This month: audits sold (advance paid), ₹ collected, hours, ₹ per hour, against the target."""
    a = frame(audits)
    m = str(today)[:7]
    sold = a[(a["adv_status"] == "paid") | (a["status"].isin(["advance_paid", "quotes_in", "report_ready", "delivered", "closed"]))]
    sold_m = sold[sold["created"].str[:7] == m]
    cash = float(pd.to_numeric(a.loc[a["status_at"].str[:7] == m, "paid_inr"], errors="coerce").fillna(0).sum())
    hrs = float(pd.to_numeric(a.loc[a["created"].str[:7] == m, "hours"], errors="coerce").fillna(0).sum())
    left = (date.fromisoformat(m + "-01") + timedelta(days=32)).replace(day=1) - date.fromisoformat(str(today))
    n = len(sold_m)
    verdict = ("On target" if n >= TARGET_PER_MONTH else f"Behind: {n} of {TARGET_PER_MONTH} sold, {left.days} days left")
    open_ = a[~a["status"].isin(["closed", "lost"])]
    return {"sold": n, "cash": cash, "hours": hrs, "per_hour": (cash / hrs if hrs else None), "verdict": verdict,
            "open": open_}


def next_step(r, qdf):
    st = r["status"]
    if st == "new":
        return f"save the client's email (/audit email {r['job_id']} …) and send the fee: /audit fee {r['job_id']}"
    if st == "fee_sent":
        return "waiting for the advance"
    if st == "advance_paid":
        ok, why, _ = ready(r, qdf)
        return f"collect quotes — {why}"
    if st == "quotes_in":
        return f"/audit build {r['job_id']}"
    if st == "report_ready":
        return f"read the report, then /audit deliver {r['job_id']}"
    if st == "delivered":
        return "waiting for the balance"
    return ""


def audits_message(audits, qdf, signals, sourcing_rows, esc, sender="OneZero"):
    """/audits in the bot: each open job, its next step, and for jobs collecting quotes the RFQ text + suppliers."""
    a = frame(audits)
    open_ = a[~a["status"].isin(["closed", "lost"])]
    if open_.empty:
        return "No open audits. Start one: /audit new <client> | <product or ASIN> | <selling price>"
    out = [f"🧾 <b>{len(open_)} open audit(s)</b>"]
    for _, r in open_.iterrows():
        out.append(f"\n<b>{esc(r['job_id'])} · {esc(r['client'])}</b> — {esc(r['product'])} at ₹{_f(r['target_price']) or 0:,.0f} · "
                   f"{esc(r['status'])}\nNext: {esc(next_step(r, qdf))}")
        if r["status"] in ("new", "fee_sent", "advance_paid"):
            sl = shortlist(dict(r), sourcing_rows, n=5)
            if sl:
                out.append("Suppliers our IndiaMART run found (listing prices, not quotes):")
                out += [f"  • <a href=\"{esc(x['url'])}\">{esc(x['supplier'] or 'supplier')}</a> {esc(x['city'])} — ~₹{x['per_piece']:,.0f}"
                        + (f", MOQ {esc(x['moq'])}" if x["moq"] else "") for x in sl if x["per_piece"]]
            out.append(f"Search: <a href=\"{esc(search_link(r.get('generic') or r['product']))}\">IndiaMART</a>")
            out.append(f"<pre>{esc(Q.rfq_message(r.get('generic') or generic_name(r['product']), 100, DELIVER_TO, sender))}</pre>"
                       f"<i>Product name sent to suppliers: '{esc(r.get('generic') or generic_name(r['product']))}' — change it with "
                       f"/audit name {esc(r['job_id'])} &lt;generic words&gt;</i>")
    return "\n".join(out)
