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
            stars = int(r.get("stargazers_count") or 0)
            age = (now - pushed).days
            note = ("hobby (<20★)" if stars < MAINTAINED_STARS else
                    "stale (no commit for 1+ year)" if age > MAINTAINED_DAYS else
                    "active" if age <= 90 else "maintained")
            top.append({"repo": r.get("full_name"), "stars": stars, "updated": str(r.get("pushed_at", ""))[:10],
                        "licence": lic, "reusable": lic in PERMISSIVE, "url": r.get("html_url"), "note": note})
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
                           f"{yes} people said they'd pay (need ≥{min_yes})"))
    return out


# ══════════════════════════════════════════════════════════════════════
# v1.36 OPEN DISCOVERY — any tool people (developers or not) are asking for
# ══════════════════════════════════════════════════════════════════════
# Needs are DISCOVERED each week (not a fixed list): each request is reduced to its key words, similar
# requests from different sources are grouped, and the group is checked against GitHub.
HN_QUERIES = ["is there a tool", "is there an app", "looking for a tool", "tool that", "app that"]
TREND_OPEN_SEEDS = ["is there an app", "chrome extension for", "free tool to", "online converter", "app to automate"]
ISSUE_LABELS = ["feature request", "enhancement", "feature"]
MIN_ISSUE_REACTIONS = 10

_LEAD = re.compile(r"^\s*(ask hn|tell hn|show hn|\[?feature( request)?\]?|feat|request|rfe|proposal|idea|suggestion)\s*[:\-–]\s*", re.I)
_ASK = re.compile(r"\b(is there (a|an|any)|are there (any)?|looking for (a|an)?|need (a|an)|recommend(ation)?s? for|"
                  r"how (do|can|would|should) (i|you|we)|what('s| is) the best|best way to|any (good )?|"
                  r"tool (that|to|for)|app (that|to|for)|software (that|to|for)|way to|possible to|"
                  r"please add|add support for|support for|ability to|option to|allow (to|users to)?|would be nice|"
                  r"it would be (great|nice)|can (we|you|i))\b", re.I)
_STOP = set("""a an the and or but if then else for to of in on at by with from as is are was were be been being it its
this that these those there here what which who whom whose when where why how do does did can could should would
will shall may might must i you we they he she me my our your their them us any some all each every no not only
just also very more most such than too into onto over under again further once same so out up down about above
below between both few other own off while during before after because until like get got using use used via
without within per vs anyone someone something anything thing things way ways tool tools app apps software
feature request support add allow option ability new good best simple easy free online please hn ask
automatically automatic programmatically code script""".split())


def need_tokens(text):
    """'Ask HN: Is there a tool that converts Figma designs to Flutter?' → ['convert','figma','design','flutter']"""
    t = _LEAD.sub("", str(text or "").lower())
    t = _ASK.sub(" ", t)
    toks = []
    for w in re.findall(r"[a-z][a-z0-9+#.\-]{1,}", t):
        w = w.strip(".-")
        if w in _STOP or len(w) < 3 or w.isdigit():
            continue
        if w.endswith("ies") and len(w) > 5:
            w = w[:-3] + "y"
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            w = w[:-1]
        for suf in ("ing", "ed"):
            if w.endswith(suf) and len(w) > len(suf) + 3:
                w = w[:-len(suf)]; break
        if w not in toks:
            toks.append(w)
    return toks[:6]


def cluster_needs(items, min_overlap=0.5):
    """items: [{'source','text','weight','url'}] → groups of the same need across sources.
    Two needs join if their key words overlap ≥ min_overlap (Jaccard) or share 3+ words."""
    groups = []
    for it in items:
        toks = set(need_tokens(it["text"]))
        if len(toks) < 2:
            continue
        best, best_j = None, 0.0
        for g in groups:
            inter = len(toks & g["tokens"])
            j = inter / max(1, len(toks | g["tokens"]))
            if (j >= min_overlap or inter >= 3) and j > best_j:
                best, best_j = g, j
        if best is None:
            best = {"tokens": set(toks), "items": [], "core": None}
            groups.append(best)
        best["items"].append(it)
        best["tokens"] |= toks
    for g in groups:
        cnt = {}
        for it in g["items"]:
            for w in need_tokens(it["text"]):
                cnt[w] = cnt.get(w, 0) + 1
        g["core"] = [w for w, _ in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))][:4]
    return groups


SOURCE_WEIGHT = {"github_issue": 3.0, "hn": 2.5, "softwarerecs": 2.5, "stackoverflow": 1.0, "gtrends": 1.5}


def open_rows(groups, supply, week, history_rows=None, top_n=10, weeks_needed=3):
    """→ OPPORTUNITIES rows (open needs), ranked, with weeks_in_top / status. supply: {need_key: gh_supply}."""
    rows = []
    for g in groups:
        srcs = sorted({it["source"] for it in g["items"]})
        d = sum(SOURCE_WEIGHT.get(it["source"], 1) * math.log10(1 + float(it.get("weight") or 0)) * 5 for it in g["items"])
        d += 10 * (len(srcs) - 1)                                   # independent sources agreeing
        key = " ".join(sorted(g["core"][:3]))
        sp = supply.get(key, {"gh_total": 0, "gh_maintained": 0, "gh_easy": 0, "gh_top": []})
        m, e = sp.get("gh_maintained", 0), sp.get("gh_easy", 0)
        kind, factor = ("BUILD", 1.0) if m == 0 else ("PACKAGE", 0.8) if e <= 1 else ("CROWDED", 1 / (1 + e))
        top = sp.get("gh_top", [])
        ex = sorted(g["items"], key=lambda it: -float(it.get("weight") or 0))
        rows.append({"week": week, "bucket": key, "problem": " ".join(g["core"]), "gap": kind,
                     "score": round(min(100.0, d * factor), 1), "sources": ",".join(srcs), "requests": len(g["items"]),
                     "smb_group": ",".join(sorted({b for it in g["items"] for b in bucket_of(it["text"])})),
                     "gh_total": sp.get("gh_total", 0), "gh_maintained": m, "gh_easy": e,
                     "reusable_code": " | ".join(f"{t['repo']} ({t['licence']}, ★{t['stars']})" for t in top if t["reusable"]),
                     "do_not_reuse": " | ".join(f"{t['repo']} ({t['licence']})" for t in top if not t["reusable"]),
                     "examples": " · ".join(str(it["text"])[:80] for it in ex[:3]),
                     "links": " ".join(str(it.get("url", "")) for it in ex[:3] if it.get("url"))})
    rows.sort(key=lambda r: -r["score"])
    prev = {}
    for r in history_rows or []:
        if str(r.get("in_top", "")).lower() in ("true", "1"):
            prev.setdefault(r["bucket"], set()).add(r["week"])
    for i, r in enumerate(rows):
        r["in_top"] = i < top_n
        r["weeks_in_top"] = len(prev.get(r["bucket"], set()) | ({week} if r["in_top"] else set()))
        r["status"] = ("SHORTLIST — test with a waitlist page" if r["weeks_in_top"] >= weeks_needed
                       and len(r["sources"].split(",")) >= 2 and r["gap"] != "CROWDED" else "watch")
    return rows


# ══════════════════════════════════════════════════════════════════════
# v1.37 STRONGER JUDGEMENT — competition beyond GitHub, buildability, effort, audience, data edge,
# distribution-independent gate, streaks that survive re-wording
# ══════════════════════════════════════════════════════════════════════
MIN_REUSE_STARS = 20          # below this a repo is a practice project, not a base to build on

# Known commercial tools per business problem group: GitHub can't see them, but buyers can.
COMMERCIAL = {
    "gst_invoice": ["Zoho Invoice", "Vyapar", "Khatabook", "Tally", "myBillBook", "ClearTax"],
    "email_auto": ["Gmail mail merge (YAMM, GMass)", "Mailchimp"],
    "whatsapp_msg": ["Interakt", "WATI", "AiSensy"],
    "pdf_docs": ["Google Docs/Sheets add-ons", "Autocrat"],
    "reports": ["Looker Studio", "Power BI", "Zoho Analytics"],
    "data_merge": ["Power Query (Excel)", "Google Sheets IMPORTRANGE"],
    "inventory": ["Zoho Inventory", "Vyapar", "Marg"],
    "reconcile": ["Tally", "Zoho Books"],
    "attendance_payroll": ["Zoho Payroll", "greytHR", "Keka"],
    "reminders": ["Google Calendar", "Zapier"],
    "forms": ["Google Forms", "Zapier"],
    "tally_accounting": ["Tally", "Zoho Books"],
    "ecommerce_ops": ["Shopify apps", "Unicommerce", "EasyEcom"],
    "qr_barcode": ["many free generators"],
    "backup_sync": ["Zapier", "Make"],
}
PLATFORM_HARD = re.compile(r"\b(whats ?app|instagram|insta|facebook|tiktok|linkedin|twitter|x\.com|youtube "
                           r"(download|downloader)|netflix|spotify|bank|banking|upi|aadhaar|amazon (data|scrap|price)|"
                           r"scrap\w*|bypass|captcha|ios|iphone|apple watch|airpods|tesla|car|printer driver)\b", re.I)
EFFORT_L = re.compile(r"\b(ai|llm|gpt|model|machine learning|video|real.?time|sync\w*|collaborat\w*|mobile app|"
                      r"android|ios|marketplace|erp|crm|payment|blockchain|game|3d|compiler)\b", re.I)
EFFORT_S = re.compile(r"\b(convert\w*|formatter|format|generat\w*|calculat\w*|validat\w*|template|regex|json|csv|"
                      r"markdown|pdf|qr|barcode|counter|timer|cheat ?sheet|checklist|extension|bookmarklet)\b", re.I)
DEV_WORDS = re.compile(r"\b(api|sdk|cli|npm|pip|python|javascript|typescript|react|node|docker|kubernetes|git|github|"
                       r"vscode|neovim|vim|terminal|json|yaml|regex|sql|database|postgres|mysql|compiler|library|"
                       r"framework|plugin|webpack|vite|rust|golang|java|kotlin|swift|linux|bash|ci|deploy)\b", re.I)
BIZ_WORDS = re.compile(r"\b(invoice|gst|billing|inventory|stock|seller|shop|store|customer|order|payroll|"
                       r"attendance|accounting|tally|excel|sheet|sheets|report|whatsapp|crm|sales|lead|ecommerce|"
                       r"amazon|flipkart|meesho|shopify|business|small business|msme)\b", re.I)
DATA_EDGE = re.compile(r"\b(amazon|flipkart|meesho|seller|sellers|ecommerce|e-commerce|bestseller|product research|"
                       r"trend|trending|price track\w*|price history|competitor|inventory|stock|sourcing|indiamart|"
                       r"margin|seasonal|festival|marketplace)\b", re.I)


def judge(text_all, smb_groups):
    """→ dict(audience, effort, hard, data_edge, commercial) for one need, from its requests' words."""
    t = str(text_all)
    dev, biz = len(DEV_WORDS.findall(t)), len(BIZ_WORDS.findall(t)) + 2 * bool(smb_groups)
    commercial = sorted({c for g in smb_groups for c in COMMERCIAL.get(g, [])})
    return {"audience": "Business" if biz > dev else "Developer" if dev > biz else "General",
            "effort": "L" if EFFORT_L.search(t) else "S" if EFFORT_S.search(t) else "M",
            "hard": bool(PLATFORM_HARD.search(t)),
            "data_edge": bool(DATA_EDGE.search(t)),
            "commercial": commercial}


def match_history(key_tokens, hist_keys, min_j=0.5):
    """Carry a weekly streak when a need's wording shifts: best previous key with Jaccard ≥ min_j."""
    best, bj = None, 0.0
    for hk in hist_keys:
        h = set(hk.split())
        j = len(key_tokens & h) / max(1, len(key_tokens | h))
        if j >= min_j and j > bj:
            best, bj = hk, j
    return best


def open_rows_v2(groups, supply, extra, week, history_rows=None, top_n=10, weeks_needed=3, focus="all"):
    """Like open_rows, plus: competition beyond GitHub (Show HN launches, npm packages, known commercial
    tools), HARD (platform access), effort S/M/L, audience, data edge, reuse only ≥ MIN_REUSE_STARS,
    and streaks matched across re-wordings. extra: {key: {'show_hn': n, 'npm': n}}."""
    hist = history_rows or []
    prev = {}
    for r in hist:
        if str(r.get("in_top", "")).lower() in ("true", "1"):
            prev.setdefault(r["bucket"], set()).add(r["week"])
    rows = []
    for g in groups:
        srcs = sorted({it["source"] for it in g["items"]})
        key = " ".join(sorted(g["core"][:3]))
        text_all = " ".join(str(it["text"]) for it in g["items"])
        smb = sorted({b for it in g["items"] for b in bucket_of(it["text"])})
        j = judge(text_all, smb)
        if focus in ("business", "developer") and j["audience"].lower() not in (focus, "general"):
            continue
        sp = supply.get(key, {"gh_total": 0, "gh_maintained": 0, "gh_easy": 0, "gh_top": []})
        ex_ = extra.get(key, {})
        launches, npm = int(ex_.get("show_hn", 0)), int(ex_.get("npm", 0))
        competitors = sp.get("gh_easy", 0) + min(launches, 5) + len(j["commercial"]) + (1 if npm >= 5 else 0)
        d = sum(SOURCE_WEIGHT.get(it["source"], 1) * math.log10(1 + float(it.get("weight") or 0)) * 5 for it in g["items"])
        d += 10 * (len(srcs) - 1) + (15 if j["data_edge"] else 0)
        if j["hard"]:
            kind, factor = "HARD", 0.3
        elif sp.get("gh_maintained", 0) == 0 and competitors == 0:
            kind, factor = "BUILD", 1.0
        elif competitors <= 1:
            kind, factor = "PACKAGE", 0.8
        else:
            kind, factor = "CROWDED", 1 / (1 + competitors)
        factor *= {"S": 1.0, "M": 0.85, "L": 0.6}[j["effort"]]
        top = [t for t in sp.get("gh_top", [])]
        ex = sorted(g["items"], key=lambda it: -float(it.get("weight") or 0))
        hk = key if key in prev else match_history(set(key.split()), prev.keys())
        rows.append({"week": week, "bucket": hk or key, "problem": " ".join(g["core"]), "gap": kind,
                     "score": round(min(100.0, d * factor), 1), "sources": ",".join(srcs), "requests": len(g["items"]),
                     "audience": j["audience"], "effort": j["effort"], "data_edge": j["data_edge"],
                     "smb_group": ",".join(smb), "commercial": ", ".join(j["commercial"]),
                     "show_hn_launches": launches, "npm_packages": npm,
                     "gh_total": sp.get("gh_total", 0), "gh_maintained": sp.get("gh_maintained", 0), "gh_easy": sp.get("gh_easy", 0),
                     "reusable_code": " | ".join(f"{t['repo']} ({t['licence']}, ★{t['stars']}, last commit {t['updated']}, "
                                                 f"{t.get('note', '')})" for t in top
                                                 if t["reusable"] and t["stars"] >= MIN_REUSE_STARS),
                     "do_not_reuse": " | ".join(f"{t['repo']} ({t['licence']})" for t in top if not t["reusable"]),
                     "examples": " · ".join(str(it["text"])[:80] for it in ex[:3]),
                     "links": " ".join(str(it.get("url", "")) for it in ex[:3] if it.get("url"))})
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows):
        r["in_top"] = i < top_n
        r["weeks_in_top"] = len(prev.get(r["bucket"], set()) | ({week} if r["in_top"] else set()))
        r["status"] = ("SHORTLIST — show it to people directly, then a waitlist page"
                       if r["weeks_in_top"] >= weeks_needed and len(r["sources"].split(",")) >= 2
                       and r["gap"] in ("BUILD", "PACKAGE") else "watch")
    return rows


def waitlist_verdict_v2(rows, min_shown=20, min_rate=0.3, min_signups=30):
    """Distribution-independent gate: of people you SHOWED it to directly (Telegram, seller groups, email),
    what share said yes / joined? ≥ 30% of at least 20 → BUILD. 30+ organic sign-ups also passes."""
    out = []
    for r in rows:
        b = str(r.get("bucket", "")).strip()
        if not b or "example" in b.lower():
            continue
        n = lambda k: int(float(str(r.get(k) or 0).replace(",", "") or 0))
        shown, pos, su = n("shown"), n("positive"), n("signups")
        rate = pos / shown if shown else 0
        ok = (shown >= min_shown and rate >= min_rate) or su >= min_signups
        out.append((b, ok, f"{b}: shown to {shown} people, {pos} positive ({rate:.0%}; need ≥{min_rate:.0%} of ≥{min_shown})"
                           f" · {su} organic sign-ups (or ≥{min_signups})"))
    return out



# ══════════════════════════════════════════════════════════════════════
# v1.38 — evidence, confidence, regulated flag, focus fit, web crowding, repo quality, links
# ══════════════════════════════════════════════════════════════════════
REGULATED = re.compile(r"\b(gst|tax|taxes|tds|itr|income tax|e-?invoic\w*|invoice|invoices|payroll|salary|pf|esi|"
                       r"accounting|audit|legal|contract|agreement|medical|health|doctor|insurance|loan|kyc|aadhaar|"
                       r"pan card|stamp duty|compliance)\b", re.I)
FORUM_HOSTS = re.compile(r"(reddit|stackoverflow|stackexchange|quora|youtube|github|medium|wikipedia|facebook|"
                         r"linkedin|twitter|x\.com|news\.ycombinator|dev\.to)\.", re.I)
TOOL_PAGE = re.compile(r"\b(tool|tools|generator|converter|online|free|app|calculator|template|software|maker|"
                       r"extension|add-?on|plugin)\b", re.I)
MIN_EVIDENCE = {"requests": 4, "sources": 2}          # below this nothing may be called BUILD


def web_crowding(results):
    """Brave web results (list of {'url','title'}) → dedicated tool pages on the first page (forums excluded)."""
    pages = [r for r in results if not FORUM_HOSTS.search(str(r.get("url", "")))
             and TOOL_PAGE.search(f"{r.get('title', '')} {r.get('url', '')}")]
    hosts = []
    for r in pages:
        h = re.sub(r"^https?://(www\.)?", "", str(r.get("url", ""))).split("/")[0]
        if h and h not in hosts:
            hosts.append(h)
    return {"web_tool_pages": len(hosts), "web_examples": ", ".join(hosts[:4])}


def confidence(n_requests, n_sources):
    if n_requests >= 8 and n_sources >= 3:
        return "High"
    if n_requests >= MIN_EVIDENCE["requests"] and n_sources >= MIN_EVIDENCE["sources"]:
        return "Medium"
    return "Low"


def fits_focus(text, focus_words):
    if not focus_words:
        return True
    t = str(text).lower()
    return any(re.search(r"\b" + re.escape(w.lower()) + r"\b", t) for w in focus_words if w.strip())


def finalize_v38(rows, groups_by_key, web, focus_words=None):
    """Adds to open_rows_v2 output: web crowding, regulated flag, evidence + confidence (Low → UNPROVEN, never
    BUILD), score parts, focus fit (off-focus → PARKED), repo quality, links. Re-ranks; shortlist only if
    in focus, not regulated-unreviewed, Medium+ confidence."""
    out = []
    for r in rows:
        w = web.get(r["bucket"]) or web.get(" ".join(sorted(r["problem"].split()[:3]))) or {}
        r["web_tool_pages"] = int(w.get("web_tool_pages", -1)) if w else -1
        r["web_examples"] = w.get("web_examples", "") if w else ""
        text = f"{r['problem']} {r['examples']}"
        r["regulated"] = bool(REGULATED.search(text))
        r["confidence"] = confidence(int(r["requests"]), len(str(r["sources"]).split(",")))
        r["fits_focus"] = fits_focus(text, focus_words or [])
        base_gap = r["gap"]
        if r["web_tool_pages"] >= 3 and base_gap in ("BUILD", "PACKAGE"):
            r["gap"] = "CROWDED"
        if r["gap"] == "BUILD" and r["confidence"] == "Low":
            r["gap"] = "UNPROVEN"
        factor = 1.0
        if r["regulated"]:
            factor *= 0.7
        if r["gap"] == "CROWDED" and base_gap != "CROWDED":
            factor *= 0.4
        if r["confidence"] == "Low":
            factor *= 0.6
        r["score_parts"] = (f"base {r['score']} ({r['requests']} asks, {len(str(r['sources']).split(','))} sources"
                            + (", +data edge" if str(r.get("data_edge")) == "True" else "") + f", effort {r['effort']})"
                            + (" ×0.7 regulated" if r["regulated"] else "") + (" ×0.4 web-crowded" if r["gap"] == "CROWDED" and base_gap != "CROWDED" else "")
                            + (" ×0.6 low evidence" if r["confidence"] == "Low" else ""))
        r["score"] = round(float(r["score"]) * factor, 1)
        out.append(r)
    out.sort(key=lambda r: (not r["fits_focus"], -r["score"]))
    for r in out:
        if not r["fits_focus"]:
            r["status"] = "PARKED (off focus)"
        elif r["status"].startswith("SHORTLIST") and (r["confidence"] == "Low" or r["gap"] not in ("BUILD", "PACKAGE")):
            r["status"] = "watch"
        if r["status"].startswith("SHORTLIST") and r["regulated"]:
            r["status"] += " · ⚠️ check tax/legal rules & liability first"
    return out



# ══════════════════════════════════════════════════════════════════════
# v1.39 — only REAL tool requests count as demand (10 Nov run: 199 debugging questions + "Show HN" launches)
# ══════════════════════════════════════════════════════════════════════
LAUNCH = re.compile(r"^\s*(show hn|launch hn|show reddit|introducing|announcing|we built|i built|i made)\b", re.I)
TOOL_ASK = re.compile(
    r"\b(is there (a|an|any)\s+(\w+\s+){0,3}(tool|app|apps|library|lib|package|extension|plugin|service|website|site|"
    r"software|cli|api|way to (automatically|automate|bulk|batch|convert|export|sync|generate))|"
    r"are there any\s+(\w+\s+){0,3}(tools|apps|libraries|extensions|services)|"
    r"looking for (a|an)?\s*(\w+\s+){0,3}(tool|app|library|extension|service|software|alternative)|"
    r"recommend(ation)?s? (a|an|for)?\s*(\w+\s+){0,3}(tool|app|library|extension|service|software)|"
    r"alternatives? (to|for)|replacement for|wish there (was|were) (a|an)|"
    r"how (do|can) (i|we|you) (automate|automatically|bulk|batch|programmatically)|"
    r"(tool|app|script|extension|service) (to|that|which|for) (convert|automate|generate|export|import|sync|track|"
    r"monitor|merge|split|extract|schedule|send|backup|compare|clean|format))\b", re.I)


def is_tool_ask(item):
    """Demand only: someone asking for a tool. GitHub feature requests count as asks for that feature.
    Launches (Show HN…) are SUPPLY, never demand. Debugging questions without tool intent are dropped."""
    t = str(item.get("text", ""))
    if LAUNCH.search(t):
        return False
    if item.get("source") in ("github_issue", "gtrends", "softwarerecs"):
        return True                                  # issue = feature ask; Trends seeds and Software Recs are tool-intent
    return bool(TOOL_ASK.search(t))


def display_name(group):
    """Readable label: the most-weighted ask, cleaned of 'Ask HN:' / 'Is there a tool that…' wrappers."""
    best = max(group["items"], key=lambda it: float(it.get("weight") or 0))
    t = _LEAD.sub("", str(best["text"]))
    t = re.sub(r"^\s*(is there (a|an|any)\s+(good\s+)?(tool|app|library|way|service|extension)\s+(that|to|for)\s+)", "", t, flags=re.I)
    t = re.sub(r"\?+$", "", t).strip()
    return (t[:70] + "…") if len(t) > 70 else t
