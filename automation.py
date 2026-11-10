"""
Automation-opportunity engine (admin only) — for bizopstool.

Question it answers each week: which business problems are people struggling to automate, and is there
already a good, usable, legally reusable solution on GitHub?

DEMAND (two independent sources, grouped into 16 fixed problem groups so weeks are comparable):
  * Stack Overflow (official API): this week's questions on Excel/VBA, Google Sheets/Apps Script,
    automation… with their view counts. A question = someone stuck on the problem right now.
  * Google Trends: rising searches around automation seeds ("excel macro", "gst invoice generator"…).
SUPPLY (GitHub search API):
  * how many projects match · how many are MAINTAINED (updated ≤ 12 months, ≥ 20 stars) · how many of those
    look usable by non-programmers (app / add-on / extension / no-code) · their LICENCE.
GAP TYPES:
  * BUILD    — demand, nothing maintained exists
  * PACKAGE  — maintained code exists but it's for developers → make it usable (reuse only if permissive licence)
  * CROWDED  — several maintained, easy-to-use tools already exist
SHORTLIST: top-5 gap score for 3+ weeks AND both sources → test with an honest waitlist page before building.
Licences: MIT / Apache / BSD / ISC can be reused with credit. GPL obliges you to open your own code.
NO licence = all rights reserved → do NOT reuse that code.
"""
import math
import re
from datetime import datetime, timedelta, timezone

# (key, label, title/search pattern, GitHub search query)
BUCKETS = [
    ("gst_invoice", "GST invoices & billing",
     r"\b(invoice|invoices|invoicing|billing|bill|gst|e-?invoice|receipt|receipts)\b", "gst invoice generator"),
    ("email_auto", "Automatic emails / mail merge",
     r"\b(send(ing)? (an )?e-?mails?|mail ?merge|gmail|outlook|email automation|auto\w* e-?mails?)\b", "mail merge google sheets"),
    ("whatsapp_msg", "WhatsApp messages to customers (official API only)",
     r"\bwhats ?app\b", "whatsapp business cloud api"),
    ("pdf_docs", "PDF / document generation",
     r"\b(pdf|pdfs|docx|word document|generate documents?|certificates?|letters?)\b", "generate pdf from spreadsheet"),
    ("reports", "Automatic reports & dashboards",
     r"\b(report|reports|reporting|dashboard|dashboards|summary|pivot)\b", "automated excel report"),
    ("data_merge", "Combine / clean data from many files",
     r"\b(merge|combine|consolidate|multiple (files|sheets|workbooks)|import csv|clean(ing)? data|duplicates?)\b",
     "merge multiple excel files"),
    ("inventory", "Stock / inventory tracking & alerts",
     r"\b(inventory|stock (level|alert|management|tracking)|warehouse|reorder|sku|skus)\b", "inventory management small business"),
    ("reconcile", "Payment / bank / marketplace reconciliation",
     r"\b(reconcil\w*|bank statements?|settlements?|payouts?|payment matching)\b", "bank reconciliation"),
    ("attendance_payroll", "Attendance & payroll",
     r"\b(attendance|payroll|salary slips?|timesheets?|leave tracker)\b", "attendance payroll system"),
    ("reminders", "Reminders & notifications",
     r"\b(remind\w*|notif\w*|due dates?|follow ?ups?|scheduled? (e-?mails?|messages?))\b", "reminder notification automation"),
    ("forms", "Forms → sheets / data entry",
     r"\b(google forms?|form submissions?|data entry|web forms?|form to sheet)\b", "google forms automation"),
    ("price_tracking", "Price / competitor tracking (respect site terms)",
     r"\b(price track\w*|price monitor\w*|competitor prices?|price alerts?)\b", "price tracker"),
    ("tally_accounting", "Tally / accounting exports",
     r"\b(tally|ledgers?|accounting|journal entr\w*|balance sheet)\b", "tally erp excel"),
    ("ecommerce_ops", "Amazon / Flipkart / Shopify seller operations",
     r"\b(amazon seller|flipkart seller|meesho|shopify|woocommerce|order (export|sync)|product listings?)\b",
     "shopify order automation"),
    ("qr_barcode", "Barcodes / QR codes / labels",
     r"\b(barcodes?|qr ?codes?|label print\w*|shipping labels?)\b", "barcode label generator"),
    ("backup_sync", "Backup / sync between apps",
     r"\b(backups?|sync\w*|copy (data|rows) (between|to)|two-?way)\b", "google sheets sync"),
]
_BX = [(k, lbl, re.compile(rx, re.I), gq) for k, lbl, rx, gq in BUCKETS]
LABEL = {k: lbl for k, lbl, _, _ in BUCKETS}

SO_TAGS = ["excel-vba", "google-apps-script", "google-sheets", "excel", "automation", "vba"]
TREND_SEEDS = ["automate excel", "excel macro", "google sheets script", "gst invoice", "invoice generator",
               "whatsapp business api", "tally to excel", "inventory management", "attendance app", "mail merge",
               "bank reconciliation", "barcode generator"]
TRENDS_PER_WEEK = 6

PERMISSIVE = {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "unlicense", "0bsd", "zlib", "cc0-1.0"}
EASY = re.compile(r"\b(app|web ?app|add-?on|extension|chrome|no-?code|gui|desktop|dashboard|template|"
                  r"saas|software|tool|bot)\b", re.I)
MAINTAINED_DAYS, MAINTAINED_STARS = 365, 20


def bucket_of(text):
    """→ list of bucket keys whose pattern appears in the text (a title can touch two problems)."""
    return [k for k, _, rx, _ in _BX if rx.search(str(text or ""))]


def trend_seeds(today, n=TRENDS_PER_WEEK):
    k = datetime.fromisoformat(str(today)[:10]).isocalendar()[1] * n
    return [TREND_SEEDS[(k + i) % len(TREND_SEEDS)] for i in range(min(n, len(TREND_SEEDS)))]


# ── demand ────────────────────────────────────────────────────────────
def so_demand(questions):
    """questions: [{'title','view_count','score','is_answered','link'}] → {bucket: stats}."""
    out = {}
    for q in questions:
        for b in bucket_of(q.get("title")):
            d = out.setdefault(b, {"so_questions": 0, "so_views": 0, "so_unanswered": 0, "so_examples": []})
            d["so_questions"] += 1
            d["so_views"] += int(q.get("view_count") or 0)
            d["so_unanswered"] += 0 if q.get("is_answered") else 1
            if len(d["so_examples"]) < 3:
                d["so_examples"].append(str(q.get("title"))[:90])
    return out


def trends_demand(rising):
    """rising: [(query, rise %)] from automation seeds → {bucket: stats}."""
    out = {}
    for q, rise in rising:
        for b in bucket_of(q):
            d = out.setdefault(b, {"gt_rising": 0, "gt_max_rise": 0, "gt_examples": []})
            d["gt_rising"] += 1
            d["gt_max_rise"] = max(d["gt_max_rise"], float(rise))
            if len(d["gt_examples"]) < 3:
                d["gt_examples"].append(str(q)[:60])
    return out


# ── supply ────────────────────────────────────────────────────────────
def gh_supply(total_count, repos, now=None):
    """repos: GitHub search items (sorted by stars) → supply stats."""
    now = now or datetime.now(timezone.utc)
    maint, easy, top = 0, 0, []
    for r in repos:
        try:
            pushed = datetime.strptime(str(r.get("pushed_at", ""))[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        lic = ((r.get("license") or {}).get("spdx_id") or "none").lower()
        is_m = (now - pushed).days <= MAINTAINED_DAYS and int(r.get("stargazers_count") or 0) >= MAINTAINED_STARS
        is_e = bool(EASY.search(f"{r.get('description') or ''} {' '.join(r.get('topics') or [])}"))
        maint += is_m
        easy += is_m and is_e
        if len(top) < 3:
            top.append({"repo": r.get("full_name"), "stars": int(r.get("stargazers_count") or 0),
                        "updated": str(r.get("pushed_at", ""))[:10], "licence": lic,
                        "reusable": lic in PERMISSIVE, "url": r.get("html_url")})
    return {"gh_total": int(total_count or 0), "gh_maintained": maint, "gh_easy": easy, "gh_top": top}


# ── score ─────────────────────────────────────────────────────────────
def gap(demand, supply):
    """→ (gap_type, score 0–100). Demand = log-scaled Stack Overflow views/questions + Trends; supply discounts it."""
    d = (math.log10(1 + demand.get("so_views", 0)) * 10 + demand.get("so_questions", 0) * 2
         + demand.get("so_unanswered", 0) * 3 + demand.get("gt_rising", 0) * 8)
    m, e = supply.get("gh_maintained", 0), supply.get("gh_easy", 0)
    if m == 0:
        kind, factor = "BUILD", 1.0
    elif e <= 1:
        kind, factor = "PACKAGE", 0.8
    else:
        kind, factor = "CROWDED", 1 / (1 + e)
    return kind, round(min(100.0, d * factor), 1)


def week_rows(so, gt, supply, week, history_rows=None, top_n=5, weeks_needed=3):
    """→ OPPORTUNITIES rows for this week, ranked, with weeks_in_top and status."""
    hist = history_rows or []
    rows = []
    for b, label in LABEL.items():
        dm = {**so.get(b, {}), **gt.get(b, {})}
        if not dm:
            continue
        sp = supply.get(b, {"gh_total": 0, "gh_maintained": 0, "gh_easy": 0, "gh_top": []})
        kind, score = gap(dm, sp)
        sources = [s for s, ok in (("stackoverflow", dm.get("so_questions", 0) >= 3), ("gtrends", dm.get("gt_rising", 0) >= 1)) if ok]
        top = sp.get("gh_top", [])
        rows.append({"week": week, "bucket": b, "problem": label, "gap": kind, "score": score,
                     "sources": ",".join(sources), "so_questions": dm.get("so_questions", 0), "so_views": dm.get("so_views", 0),
                     "so_unanswered": dm.get("so_unanswered", 0), "gt_rising": dm.get("gt_rising", 0),
                     "gh_total": sp.get("gh_total", 0), "gh_maintained": sp.get("gh_maintained", 0), "gh_easy": sp.get("gh_easy", 0),
                     "reusable_code": " | ".join(f"{t['repo']} ({t['licence']}, ★{t['stars']})" for t in top if t["reusable"]),
                     "do_not_reuse": " | ".join(f"{t['repo']} ({t['licence']})" for t in top if not t["reusable"]),
                     "examples": " · ".join((dm.get("so_examples", []) + dm.get("gt_examples", []))[:3])})
    rows.sort(key=lambda r: -r["score"])
    prev_top = {}
    for r in hist:
        if str(r.get("in_top", "")).lower() in ("true", "1"):
            prev_top.setdefault(r["bucket"], set()).add(r["week"])
    for i, r in enumerate(rows):
        r["in_top"] = i < top_n
        weeks = prev_top.get(r["bucket"], set()) | ({week} if r["in_top"] else set())
        r["weeks_in_top"] = len(weeks)
        r["status"] = ("SHORTLIST — test with a waitlist page" if r["weeks_in_top"] >= weeks_needed
                       and len(r["sources"].split(",")) >= 2 and r["gap"] != "CROWDED"
                       else "watch")
    return rows


def waitlist_verdict(rows, min_signups=30, min_yes=3):
    """WAITLIST tab rows → [(bucket, ready, line)]. Build only when real people asked for it."""
    out = []
    for r in rows:
        b = str(r.get("bucket", "")).strip()
        if not b or "example" in b.lower():
            continue
        su = int(float(str(r.get("signups") or 0) or 0))
        yes = int(float(str(r.get("sellers_yes") or 0) or 0))
        ok = su >= min_signups or yes >= min_yes
        out.append((b, ok, f"{LABEL.get(b, b)}: {su} waitlist sign-ups (need ≥{min_signups}) or "
                           f"{yes} sellers said they'd pay (need ≥{min_yes})"))
    return out
