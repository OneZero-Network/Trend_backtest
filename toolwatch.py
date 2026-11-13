"""
Tool Watch — the weekly runner that hands the bizopstool team READY-TO-BUILD tickets.

Nothing here is a blank template: every ticket arrives filled in (what to build, the exact formula, worked test
cases computed by our own engine, page title/description, links to neighbouring tools, how it is judged) and you
copy it to the team as it is. Live pages are detected automatically, impressions are read automatically
(Search Console, optional) and the keep/kill rule is applied automatically.

Three feeds → one queue (a supermarket, not one shop):
  1. ENGINE   tools whose logic we already run every day for sellers (profit, minimum price, RTO loss, festival
              stock timing) — Wave 1, the first aisle.
  2. SEARCH   tool-shaped searches rising on Google Trends India (calculator / generator / converter / checker…),
              scored for rise, persistence, seller fit and how crowded page 1 already is.
  3. ASKS     what people ask for in the weekly automation report (OPPORTUNITIES tab) — vendor-only and platform-
              locked requests removed.

Honest limits (printed in the weekly message too): rising ≠ will rise (we detect early, we cannot predict); a new
site needs weeks to rank, so calendar-known and evergreen demand beat spikes; most tools will get little — the
queue works as a portfolio with kill rules, not as a promise.
"""
import html
import math
import re
from datetime import date, datetime, timedelta

import automation
import discovery
import season

SITE_PATH = "/tools/"
STANDING_VERSION = 1
ANCHOR = "image compressor"   # the term every query is compared with in Google Trends (BR_TW_ANCHOR overrides in the runner)
JUDGE_AFTER_DAYS = 56          # week 8 after the page goes live
SHIP_DAYS = 14                 # default time we ask the team for
BAR_IMPRESSIONS_WEEK = 100     # keep bar: ≥100 impressions a week …
BAR_CLICKS_28D = 20            # … or ≥20 clicks in 28 days
BOX_LIMIT = 3600               # Telegram message limit is 4096; keep room for escaping


def _d(x):
    return x if isinstance(x, date) and not isinstance(x, datetime) else (x.date() if isinstance(x, datetime) else date.fromisoformat(str(x)[:10]))


def _n(x, default=0.0):
    try:
        v = float(str(x).replace(",", "").replace("₹", "").strip() or default)
        return default if v != v else v
    except ValueError:
        return default


def clean_rows(rows):
    """Records read from Sheets (empty text) or from the local-CSV fallback (NaN) → plain strings, '' for empty."""
    out = []
    for r in rows or []:
        out.append({k: ("" if v is None or (isinstance(v, float) and v != v) or str(v).strip().lower() in ("nan", "none") else v)
                    for k, v in dict(r).items()})
    return out


def week_id(today):
    y, w, _ = _d(today).isocalendar()
    return f"{y % 100:02d}{w:02d}"


def week_start(today):
    d = _d(today)
    return (d - timedelta(days=d.weekday())).isoformat()


# ══════════════════════════════════════════════════════════════════════
# 1. The arithmetic behind the engine tools (mirrors calculator.py formulas; test_toolwatch.py cross-checks
#    this against the real workbook formulas recalculated in LibreOffice)
# ══════════════════════════════════════════════════════════════════════
DEFAULTS = dict(purchase_gst=0.18, sale_gst=0.18, defect=0.03, ship=60.0, referral=0.10, closing=30.0, rto=0.10,
                ads_pct=0.08, itc=1.0, ret_mult=2.0, moq=100)
MIN_MARGIN, MIN_ROI = 0.15, 0.30


def unit_econ(price, cost, **kw):
    p = {**DEFAULTS, **kw}
    usable = cost / (1 - p["defect"])
    fees = price * p["referral"] + p["closing"]
    gst_pay = price * p["sale_gst"] / (1 + p["sale_gst"]) - p["itc"] * usable * p["purchase_gst"]
    rto_cost = p["rto"] / (1 - p["rto"]) * p["ship"] * p["ret_mult"]
    ads = price * p["ads_pct"]
    total = usable + fees + gst_pay + p["ship"] + rto_cost + ads
    profit = price - total
    margin = profit / price if price else 0.0
    roi = profit / (cost * (1 + p["purchase_gst"])) if cost else 0.0
    cash = p["moq"] * cost * (1 + p["purchase_gst"])
    decision = ("NO-GO (margin)" if margin < MIN_MARGIN else "NO-GO (ROI)" if roi < MIN_ROI
                else "GO — still verify compliance and a sample")
    return {"usable": usable, "fees": fees, "gst_pay": gst_pay, "rto_cost": rto_cost, "ads": ads, "total": total,
            "profit": profit, "margin": margin, "roi": roi, "cash": cash, "decision": decision}


def min_price(cost, margin=MIN_MARGIN, roi=MIN_ROI, **kw):
    """Lowest selling price (GST-inclusive) that still clears both thresholds — profit is linear in price, so
    this is closed-form. → dict(price, binding, by_margin, by_roi)"""
    p = {**DEFAULTS, **kw}
    usable = cost / (1 - p["defect"])
    rto_cost = p["rto"] / (1 - p["rto"]) * p["ship"] * p["ret_mult"]
    k = usable + p["closing"] - p["itc"] * usable * p["purchase_gst"] + p["ship"] + rto_cost
    c = 1 - p["referral"] - p["sale_gst"] / (1 + p["sale_gst"]) - p["ads_pct"]
    by_margin = k / (c - margin) if c - margin > 0 else None
    by_roi = (k + roi * cost * (1 + p["purchase_gst"])) / c if c > 0 else None
    cands = [(v, n) for v, n in ((by_margin, "margin"), (by_roi, "ROI")) if v is not None]
    if not cands:
        return {"price": None, "binding": "impossible at these fees", "by_margin": by_margin, "by_roi": by_roi}
    price, binding = max(cands)
    return {"price": price, "binding": binding, "by_margin": by_margin, "by_roi": by_roi}


def rto_loss(orders, rto, ship, ret_mult=2.0, damaged=0.0, cost=0.0):
    """Monthly loss from failed deliveries: lost forward+reverse shipping, plus the share of returned units that
    cannot be resold (the part calculator.py does not model)."""
    rto_orders = orders * rto
    ship_lost = rto_orders * ship * ret_mult
    dmg_lost = rto_orders * damaged * cost
    total = ship_lost + dmg_lost
    delivered = orders - rto_orders
    return {"rto_orders": rto_orders, "ship_lost": ship_lost, "damaged_lost": dmg_lost, "total": total,
            "per_delivered": total / delivered if delivered else float("nan")}


def festival_plan(event, today):
    t = _d(today)
    fut = sorted(_d(x) for x in season.FESTIVALS.get(event, []) if _d(x) >= t)
    if not fut:
        return None
    d = fut[0]
    key, text = season.phase((d - t).days)
    return {"event": event, "date": d.isoformat(), "days_to": (d - t).days, "phase": key, "phase_text": text,
            "stock_from": (d - timedelta(days=season.STOCK_MAX_LEAD)).isoformat(),
            "stock_until": (d - timedelta(days=season.STOCK_MIN_LEAD)).isoformat(),
            "promote_from": (d - timedelta(days=season.AFF_MAX_LEAD)).isoformat(),
            "promote_until": (d - timedelta(days=season.AFF_MIN_LEAD)).isoformat(),
            "peak": (d - timedelta(days=4)).isoformat()}


def upcoming_events(today, horizon=75):
    """Events whose promotion window starts within `horizon` days and that a page can still catch (≥ 5 days of
    promotion window left after a one-week build). One page per date."""
    t = _d(today)
    plans, seen = [], set()
    for ev in sorted(season.FESTIVALS, key=lambda e: (e == "Festive decor", e)):
        p = festival_plan(ev, t)
        if not p or p["date"] in seen:
            continue
        pf, pu = _d(p["promote_from"]), _d(p["promote_until"])
        if (pf - t).days <= horizon and (pu - (t + timedelta(days=7))).days >= 5:
            plans.append(p)
            seen.add(p["date"])
    return sorted(plans, key=lambda p: p["date"])


def slugify(s):
    s = str(s).lower().replace("'", "").replace("’", "")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")


# ══════════════════════════════════════════════════════════════════════
# 2. Candidate feeds
# ══════════════════════════════════════════════════════════════════════
TOOL_NOUNS = ("calculator", "generator", "converter", "checker", "maker", "planner", "tracker", "finder",
              "estimator", "formatter", "validator", "compressor", "resizer", "splitter", "merger", "simulator",
              "template")
NOUN_RX = re.compile(r"\b(" + "|".join(TOOL_NOUNS) + r")s?\b", re.I)
NOUN_GROUP = {"calculator": "calculator", "estimator": "calculator", "simulator": "calculator",
              "generator": "generator", "maker": "generator", "converter": "converter", "checker": "checker",
              "validator": "checker", "planner": "planner", "tracker": "planner", "finder": "finder",
              "formatter": "file", "compressor": "file", "resizer": "file", "splitter": "file", "merger": "file",
              "template": "template"}
NOUN_SEEDS = ["calculator", "generator", "converter", "checker", "maker", "planner", "tracker", "finder"]
# v1.49: the old seeds ("amazon seller", "reselling"…) returned navigational searches (login, central, registration): 99 steady searches,
# none tool-shaped. A seed that already carries a tool word makes the related-search lists tool-shaped by construction.
SELLER_SEEDS = ["amazon seller calculator", "flipkart seller calculator", "meesho seller tools", "product title generator",
                "product description generator", "barcode generator", "product photo resizer", "shipping cost calculator"]
TREND_SEEDS = NOUN_SEEDS + SELLER_SEEDS
NOT_TOOL = re.compile(r"\b(login|log in|sign ?in|download|apk|near me|status|result|results|today|live|news|"
                      r"release date|customer care|contact|number|app|movie|song|lyrics|cricket|ipl|job|jobs|"
                      r"vacancy|admit card|cut ?off)\b", re.I)
# Live-event words: demand arrives as a spike and dies in days (live run 13 Nov: "murasu cyclone tracker +193,500%").
NEWS_EVENT = re.compile(r"\b(cyclone|storm|hurricane|typhoon|earthquake|quake|flood|floods|tsunami|landslide|heat ?wave|"
                        r"cold ?wave|election|elections|exit ?poll|t20|odi|world ?cup|match|toss|fixture|squad|lineup|"
                        r"breaking|verdict|strike|protest|outbreak|virus|covid)\b", re.I)
# Tools that only work with a live data feed we do not have (and may not be licensed to use).
LIVE_DATA = re.compile(r"\b(flight|train|pnr|bus|parcel|courier|shipment|awb|gold|silver|petrol|diesel|nifty|sensex|crypto|"
                       r"bitcoin|weather|traffic|gps|satellite|radar|moon|moonrise|moonset|eclipse|iss|sunrise|sunset|tide|tides)\b", re.I)
# v1.48 — gaps found in the second live run (13 Nov): typed AI prompts, site names, people-finders, social-login tools, AI image makers.
PROMPT_WORDS = re.compile(r"\b(bana do|banao|bana de|banado|karo|batao|chahiye|kaise|mere liye|meri|mera|please|write me|give me|make me)\b", re.I)
JUNK_CHARS = re.compile(r"[\U0001F000-\U0001FAFF\u2190-\u27BF\u2B00-\u2BFF]")
DOMAIN = re.compile(r"\b(www\.)?[a-z0-9-]{2,}\.(com|net|org|io|ai|in|co|app|info|us|uk)\b", re.I)
PRIVACY = re.compile(r"\b(people ?(finder|search)|person ?(finder|search)|find (a )?(person|someone)|reverse (phone|number|mobile)|"
                     r"(phone|mobile|number|location|whatsapp) ?(tracker|locator)|whois|background check|stalk\w*|spy\w*|unmask\w*)\b", re.I)
PLATFORM_LOGIN = re.compile(r"\b(unfollow\w*|ghost followers?|who (unfollowed|viewed|blocked)|profile viewer|story viewer|"
                            r"follower tracker|insta stalker|instagram (viewer|stalker))\b", re.I)
AI_MODEL = re.compile(r"(?=.*\bai\b)(?=.*\b(image|video|photo|picture|art|voice|song|music|face|anime|avatar|headshot)\b)"
                      r"|\b(music|song|audio)\b.*\b(finder|identifier|recogni\w+)\b|\b(finder|identifier)\b.*\b(music|song|audio)\b", re.I)
NOISE = {"online", "free", "india", "indian", "in", "hindi", "app", "website", "web", "tool", "tools", "best", "new",
         "for", "pc", "the", "a", "an", "to", "of", "2024", "2025", "2026", "2027", "with", "and"}
SELLER_FIT = re.compile(r"\b(amazon|flipkart|meesho|myntra|seller|sellers|reselling|reseller|ecommerce|e-commerce|"
                        r"dropship\w*|margin|profit|shipping|rto|cod|listing|sku|inventory|catalog|catalogue|"
                        r"product title|wholesale|moq|landed cost|selling price)\b", re.I)
# Simple one-line tools are what search engines' AI answers and the giants already own — scored down.
TRIVIAL = re.compile(r"\b(age|percentage|percent|bmi|date difference|days between|word count|character count|"
                     r"random number|number to words|roman|binary|unit|temperature|length|currency|time zone|"
                     r"countdown|timer|stopwatch|qr code|password|lorem)\b", re.I)
ACRONYMS = {"gst", "emi", "sip", "pdf", "qr", "ai", "roi", "rto", "cod", "mrp", "sku", "moq", "seo", "url", "json",
            "csv", "ui", "ux", "hsn", "upi", "pan", "gif", "png", "jpg", "ip", "cv"}


def tool_noun(q):
    m = NOUN_RX.search(str(q))
    return m.group(1).lower() if m else ""


def classify(q):
    """'' when a query can become a tool ticket, else the plain-words reason it cannot."""
    s = str(q)
    words = re.findall(r"[a-z0-9+#]+", s.lower())
    if not tool_noun(s):
        return "not a tool"
    if len(words) > 8 or PROMPT_WORDS.search(s):
        return "a typed AI prompt, not a search"
    if DOMAIN.search(s) or JUNK_CHARS.search(s):
        return "names a specific website — people want that site, not a new tool"
    if PRIVACY.search(s):
        return "finds private individuals (privacy / DPDP risk) — we do not build these"
    if PLATFORM_LOGIN.search(s):
        return "needs a social-network login (platform rules)"
    if AI_MODEL.search(s):
        return "needs a paid AI model or recognition service — the giants own these"
    if discovery.BANNED.search(s):
        return "banned topic"
    if automation.REGULATED.search(s):
        return "regulated (tax, loan, insurance…)"
    if NEWS_EVENT.search(s):
        return "news/weather event — spikes die in days"
    if LIVE_DATA.search(s):
        return "needs a live data feed"
    if NOT_TOOL.search(s):
        return "not a tool (login, app, jobs…)"
    if not 2 <= len(words) <= 6:
        return "too short or long"
    return ""


def tool_shaped(q):
    return classify(q) == ""


def canon_key(q):
    words = [w for w in re.findall(r"[a-z0-9+#]+", str(q).lower()) if w not in NOISE]
    return "-".join(words)


def nice_title(q):
    words = [w for w in re.findall(r"[A-Za-z0-9+#&'-]+", str(q)) if w.lower() not in {"online", "free", "india", "2026", "2027"}]
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in words) or str(q).title()


RISE_CAP = 5000.0       # Google's own "Breakout" level; beyond it a % rise only measures how tiny the base was


def trend_candidates(raw, seen, today, rejected=None):
    """raw: [{'query','rise','seed','kind','value'}] (Google Trends India, 30 days; kind 'rising' = fast-growing from a small
    base, kind 'top' = established steady searches, collected for the seller seeds only) → candidates, one per canonical key.
    seen: {key: row} from TOOLWATCH_SEEN (weeks seen so far)."""
    best = {}
    for r in raw:
        q = str(r.get("query", ""))
        kind = r.get("kind") or "rising"
        rise = discovery.rise_value(r.get("rise")) if kind == "rising" else 0.0
        top = _n(r.get("value"), 0.0) if kind == "top" else 0.0
        why_not = classify(q)
        if why_not:
            if rejected is not None and why_not not in ("not a tool", "too short or long") and (rise >= 200 or top > 0):
                rejected.append({"query": q, "rise": rise, "reason": why_not, "kind": kind})
            continue
        if (kind == "rising" and rise < 200) or (kind == "top" and top <= 0):
            continue
        key = canon_key(q)
        if not key:
            continue
        c = best.get(key)
        if c is None:
            best[key] = {"key": "q:" + key, "type": "search", "query": q.lower().strip(), "rise": rise, "top": top, "seeds": {r.get("seed")}}
        else:
            c["rise"] = max(c["rise"], rise)
            c["top"] = max(c.get("top", 0.0), top)
            c["seeds"].add(r.get("seed"))
    out = []
    for key, c in best.items():
        s = seen.get(c["key"]) or {}
        noun = tool_noun(c["query"])
        out.append({**c, "title": nice_title(c["query"]), "slug": slugify(key), "noun": noun,
                    "group": NOUN_GROUP.get(noun, "calculator"), "weeks": int(_n(s.get("weeks"), 0)) + (0 if s.get("last_week") == week_start(today) else 1),
                    "seller": bool(SELLER_FIT.search(c["query"])) or any(x in SELLER_SEEDS for x in c["seeds"]),
                    "trivial": bool(TRIVIAL.search(c["query"])),
                    "kind": "rising" if c["rise"] >= 200 else "top",
                    "seeds": sorted(x for x in c["seeds"] if x), "shape": None})
    return out


ENGINE_EVIDENCE = {
    "eng:profit": re.compile(r"\b(profit|margin|fba|fees?|roi|earnings?)\b", re.I),
    "eng:minprice": re.compile(r"\b(selling price|min(imum)? price|mrp|price calculator|pricing)\b", re.I),
    "eng:rto": re.compile(r"\b(rto|return (loss|rate|cost)|returns? calculator|cod)\b", re.I),
    "eng:festival": re.compile(r"\b(festival|season|seasonal|diwali|stock plan\w*|inventory plan\w*)\b", re.I),
}


def engine_evidence(raw, ecands):
    """Google Trends searches (top or rising, seller seeds / seller words) that match one of the Wave-1 engine tools. This is
    the only demand evidence the four engine tools can get before they are live. → [{engine_key, title, query, kind, value, seed}].
    Matching searches are evidence, not new tickets (the tool is already ticketed)."""
    titles = {c["key"]: c["title"] for c in ecands}
    out, done = [], set()
    for r in raw:
        q = str(r.get("query", "")).lower().strip()
        if q in done or not tool_noun(q) or classify(q):
            continue
        if not (SELLER_FIT.search(q) or r.get("seed") in SELLER_SEEDS):
            continue
        for key, rx in ENGINE_EVIDENCE.items():
            if key in titles and rx.search(q):
                out.append({"engine_key": key, "title": titles[key], "query": q, "kind": r.get("kind") or "rising",
                            "value": r.get("value") if (r.get("kind") == "top") else r.get("rise"), "seed": r.get("seed")})
                done.add(q)
                break
    return out


def update_seen(seen_rows, cands, today):
    """TOOLWATCH_SEEN: distinct weeks each tool-shaped rising search has appeared in (persistence)."""
    ws = week_start(today)
    by = {r.get("key"): dict(r) for r in seen_rows if r.get("key")}
    for c in cands:
        if c["type"] != "search":
            continue
        r = by.get(c["key"], {"key": c["key"], "query": c["query"], "first_seen": _d(today).isoformat(), "weeks": "0", "last_week": ""})
        if r.get("last_week") != ws:
            r["weeks"] = str(int(_n(r.get("weeks"), 0)) + 1)
            r["last_week"] = ws
        r["last_seen"] = _d(today).isoformat()
        r["max_rise"] = str(max(c["rise"], _n(r.get("max_rise"), 0)))
        by[c["key"]] = r
    cutoff = (_d(today) - timedelta(days=120)).isoformat()
    return [r for r in by.values() if str(r.get("last_seen", "9999")) >= cutoff]


DEV_TOPIC = re.compile(r"\b(library|libraries|framework|sdk|authentication|blazor|react|angular|django|laravel|kubernetes|docker|"
                       r"sanitiz\w+|middleware|microservices?|devops|terraform)\b", re.I)


def ask_candidates(opp_rows, today, limit=3):
    """OPPORTUNITIES rows of the latest week → small, buildable, non-vendor, non-regulated asks."""
    if not opp_rows:
        return []
    latest = max(str(r.get("week", "")) for r in opp_rows)
    out = []
    for r in opp_rows:
        if str(r.get("week")) != latest:
            continue
        if str(r.get("gap")) in ("VENDOR-ONLY", "HARD") or str(r.get("regulated")) == "True":
            continue
        if str(r.get("effort", "M")) == "L":
            continue
        name = str(r.get("name") or r.get("problem") or "").strip()
        if not name or discovery.BANNED.search(name) or DEV_TOPIC.search(name):      # code questions are not tools people search for
            continue
        noun = tool_noun(name) or "tool"
        out.append({"key": "a:" + canon_key(name)[:60], "type": "ask", "title": nice_title(name)[:70], "query": name.lower()[:80],
                    "slug": slugify(canon_key(name))[:60], "noun": noun, "group": NOUN_GROUP.get(noun, "calculator"),
                    "base": _n(r.get("score")), "requests": int(_n(r.get("requests"), 1)), "sources": str(r.get("sources", "")),
                    "votes": int(_n(r.get("votes"), 0)), "links": str(r.get("links", "")).split()[:3],
                    "gap": str(r.get("gap", "")), "competition": f"{r.get('gh_easy', 0)} easy GitHub tools, "
                    f"{r.get('web_tool_pages', -1) if str(r.get('web_tool_pages', '-1')) != '-1' else 'unchecked'} tool sites on page 1",
                    "reusable": str(r.get("reusable_code", ""))[:200], "seller": bool(SELLER_FIT.search(name))})
    out.sort(key=lambda c: -c["base"])
    return out[:limit]


# ══════════════════════════════════════════════════════════════════════
# 3. Engine tools (Wave 1) — fully specified, test cases computed by the code above
# ══════════════════════════════════════════════════════════════════════
def _money(x):
    return ("−" if x < 0 else "") + f"₹{abs(x):,.2f}"


def _pct(x):
    return ("−" if x < 0 else "") + f"{abs(x):.1%}"


PHASE_SHORT = {"too_late": "too late", "promote": "promote only", "prepare": "prepare creatives, do NOT order stock",
               "stock": "stock now", "early": "too early"}


def _phase_line(event, ref):
    p = festival_plan(event, ref)
    return f"{event} {p['date']}, checked {ref} ({p['days_to']} day{'s' if p['days_to'] != 1 else ''}) → {PHASE_SHORT[p['phase']]}"


def engine_candidates(today):
    t = _d(today)
    A = unit_econ(499, 150)
    B = unit_econ(299, 180)
    C = unit_econ(999, 400, itc=0, referral=0.15, rto=0.25, ship=80)
    def ue(price, cost, e, extra=""):
        return (f"price ₹{price}, cost ₹{cost}{extra} → fees {_money(e['fees'])}, GST payable {_money(e['gst_pay'])}, "
                f"RTO cost {_money(e['rto_cost'])}, profit {_money(e['profit'])}, margin {_pct(e['margin'])}, "
                f"ROI {_pct(e['roi'])} → {e['decision']}")
    mp = min_price(150)
    mp2 = min_price(400, margin=0.25, roi=0.50, referral=0.15, rto=0.25, ship=80)
    rl = rto_loss(1000, 0.15, 60, 2, damaged=0.10, cost=150)
    fp = festival_plan("Makar Sankranti/Lohri/Pongal", "2026-11-13")
    events = upcoming_events(t)
    ship_fest = max(t + timedelta(days=5), min((_d(p["promote_from"]) - timedelta(days=7) for p in events), default=t + timedelta(days=SHIP_DAYS)))
    base_defaults = ("Defaults (editable, illustrative only — never present them as Amazon's fees): purchase GST 18%, sale GST 18%, "
                     "defect 3%, shipping ₹60, referral 10%, closing ₹30, RTO 10%, ads 8% of price, input tax credit claimed. "
                     "Link to Amazon's official fee page next to the fee fields.")
    neighbours_all = ["amazon-india-seller-profit-calculator", "minimum-selling-price-calculator-amazon-india",
                      "rto-return-loss-calculator-india", "festival-stock-planner-india"]
    return [
        {"key": "eng:profit", "type": "engine", "meta_what": "Work out profit, margin and ROI after fees, GST and returns.", "base_score": 90, "title": "Amazon India Seller Profit & ROI Calculator",
         "query": "amazon india seller profit calculator", "slug": neighbours_all[0], "noun": "calculator", "group": "calculator",
         "seller": True, "ship_by": (t + timedelta(days=SHIP_DAYS)).isoformat(),
         "why": ["Sellers need one answer: will this product still make money after Amazon fees, GST, failed deliveries (RTO) and ads?",
                 "Our own engine runs exactly this model on every product it screens, every day. This tool exposes it.",
                 "Demand is NOT yet measured — this is the first bet on the seller aisle, judged at week 8.",
                 "Competition: marketplace fee calculators show fees only; ours adds sourcing cost, GST input credit, RTO loss, ROI and a GO/NO-GO."],
         "who": "Amazon.in sellers and people about to start reselling who have a supplier quote and a target price.",
         "inputs": ["Selling price ₹ (GST-inclusive) · product cost ₹ per unit (ex-GST) · units to buy (MOQ)",
                    "Purchase GST % · sale GST % · defect % · shipping ₹ per order · referral % · closing fee ₹ · RTO % · ads % of price · claim input tax credit (yes/no)"],
         "outputs": ["Usable cost, Amazon fees, GST payable, RTO cost, total cost, profit per unit, margin %, ROI %, cash needed for MOQ, and the decision (GO / NO-GO margin / NO-GO ROI) with the reason."],
         "build": [base_defaults, "Decision thresholds are editable: min margin 15%, min ROI 30%.",
                   "RTO cost is per DELIVERED order (failed orders lose forward + reverse shipping; the item is restocked)."],
         "formula": ["usable cost = cost ÷ (1 − defect%)", "fees = price × referral% + closing fee",
                     "GST payable = price × GST ÷ (1+GST) − ITC × usable cost × purchase GST%   (ITC = 1 if claimed, else 0)",
                     "RTO cost = RTO% ÷ (1 − RTO%) × shipping × 2", "ads = price × ads%",
                     "total = usable + fees + GST payable + shipping + RTO cost + ads",
                     "profit = price − total · margin = profit ÷ price · ROI = profit ÷ (cost × (1 + purchase GST%))",
                     "cash for MOQ = MOQ × cost × (1 + purchase GST%)"],
         "tests": ["1) " + ue(499, 150, A, " (all defaults)"), "2) " + ue(299, 180, B, " (all defaults)"),
                   "3) " + ue(999, 400, C, " (ITC off, referral 15%, RTO 25%, shipping ₹80)")],
         "faq_extra": "What is RTO and why does it cut my profit?", "next": "Not profitable? Use the minimum selling price tool."},
        {"key": "eng:minprice", "type": "engine", "meta_what": "Find the lowest price that still keeps your margin.", "base_score": 86, "title": "Minimum Selling Price Calculator for Amazon India",
         "query": "minimum selling price calculator amazon india", "slug": neighbours_all[1], "noun": "calculator", "group": "calculator",
         "seller": True, "ship_by": (t + timedelta(days=SHIP_DAYS)).isoformat(),
         "why": ["The reverse question sellers ask: \"what is the lowest price I can list at and still keep my margin?\"",
                 "Same model as the profit calculator, solved for price (profit is linear in price, so the answer is exact — no iteration).",
                 "Demand NOT yet measured; judged at week 8."],
         "who": "Sellers pricing a new listing or matching a competitor's price cut.",
         "inputs": ["Product cost ₹ (ex-GST) · target margin % (default 15) · target ROI % (default 30)",
                    "Same fee/GST/shipping/RTO/ads fields as the profit calculator (shared defaults)."],
         "outputs": ["Minimum price (GST-inclusive), which target binds (margin or ROI), and the full profit breakdown at that price."],
         "build": [base_defaults, "If fees + GST + ads + margin ≥ 100% of price, say \"not possible at these fees\" instead of a number."],
         "formula": ["K = usable + closing fee − ITC × usable × purchase GST% + shipping + RTO cost",
                     "c = 1 − referral% − GST÷(1+GST) − ads%",
                     "price for margin m = K ÷ (c − m)", "price for ROI r = (K + r × cost × (1 + purchase GST%)) ÷ c",
                     "minimum price = the larger of the two (usable, RTO cost, GST as in the profit calculator)"],
         "tests": [f"1) cost ₹150, margin 15%, ROI 30%, all defaults → minimum price {_money(mp['price'])} (binding: {mp['binding']}); "
                   f"by margin {_money(mp['by_margin'])}, by ROI {_money(mp['by_roi'])}",
                   f"2) cost ₹400, margin 25%, ROI 50%, referral 15%, RTO 25%, shipping ₹80, other defaults → {_money(mp2['price'])} (binding: {mp2['binding']})",
                   "3) Putting a result back into the profit calculator must give exactly the target margin (or ROI)."],
         "faq_extra": "Why is my minimum price higher than my competitor's?", "next": "Check the full breakdown in the profit calculator."},
        {"key": "eng:rto", "type": "engine", "meta_what": "See what failed deliveries really cost you each month.", "base_score": 84, "title": "RTO & Return Loss Calculator for Indian Sellers",
         "query": "rto loss calculator india", "slug": neighbours_all[2], "noun": "calculator", "group": "calculator",
         "seller": True, "ship_by": (t + timedelta(days=SHIP_DAYS)).isoformat(),
         "why": ["Failed deliveries and returns are the part most seller margin sheets leave out; in our own audit it was the biggest gap in our calculator.",
                 "This tool makes the loss visible in rupees per month and per delivered order.",
                 "Demand NOT yet measured; judged at week 8."],
         "who": "COD-heavy sellers on Amazon, Flipkart, Meesho or their own site.",
         "inputs": ["Orders dispatched per month · RTO rate % · shipping ₹ per order · return shipping multiplier (default 2) · % of returned units that cannot be resold (default 0) · unit cost ₹"],
         "outputs": ["RTO orders, shipping lost, value of unsellable returns, total monthly loss, loss per delivered order, and the saving if RTO fell by 5 points."],
         "build": ["The unsellable-returns input is the new part (not in our spreadsheet model). Keep it optional, default 0."],
         "formula": ["RTO orders = orders × RTO%", "shipping lost = RTO orders × shipping × multiplier",
                     "unsellable loss = RTO orders × unsellable% × unit cost", "total = shipping lost + unsellable loss",
                     "per delivered order = total ÷ (orders × (1 − RTO%))"],
         "tests": [f"1) 1,000 orders, RTO 15%, shipping ₹60, multiplier 2, unsellable 10%, unit cost ₹150 → RTO orders {rl['rto_orders']:.0f}, "
                   f"shipping lost {_money(rl['ship_lost'])}, unsellable loss {_money(rl['damaged_lost'])}, total {_money(rl['total'])}, "
                   f"per delivered order {_money(rl['per_delivered'])}"],
         "faq_extra": "How do I reduce RTO on COD orders?", "next": "See the effect on profit in the profit calculator."},
        {"key": "eng:festival", "type": "engine", "meta_what": "Know when to order stock and when to promote.", "base_score": 82 + (10 if events and (_d(events[0]["promote_from"]) - t).days <= 35 else 0),
         "title": "Festival Stock & Promotion Planner for Indian Sellers", "query": "festival stock planner india seller",
         "slug": neighbours_all[3], "noun": "planner", "group": "planner", "seller": True, "ship_by": ship_fest.isoformat(),
         "why": ["Seasonal products only pay inside a window: our own data showed Diwali goods peaking about 4 days BEFORE Diwali.",
                 "The engine already computes, per event, when to place the bulk order, when to promote and when it is too late.",
                 f"Calendar-known demand (not a guess): {len(events)} upcoming events need a page, the first promotion window opens {events[0]['promote_from'] if events else 'n/a'}.",
                 "Demand NOT yet measured; judged at week 8 (and on each event date)."],
         "who": "Sellers and resellers planning stock for festivals and seasons.",
         "inputs": ["Choose an event (or type a product that matches one) → the planner shows the dates. Optional: stock budget ₹ → how many days of promotion it can fund (leave out if unsure)."],
         "outputs": ["Event date · order-stock-between dates · promote-from/until dates · peak demand day (≈4 days before) · a plain verdict (stock now / promote only / too late) · add-to-calendar (.ics) and a Telegram reminder link."],
         "build": ["One main page + one landing page per upcoming event (list below). Dates come from our calendar and must be used as given; lunar-festival dates are updated by us every October."],
         "formula": [f"stock window: {season.STOCK_MAX_LEAD} to {season.STOCK_MIN_LEAD} days before the event (bulk order → sample → inbound takes ~6 weeks)",
                     f"promotion window: {season.AFF_MAX_LEAD} to {season.AFF_MIN_LEAD} days before the event; peak ≈ 4 days before",
                     "verdict by days to the event: over 120 → too early (say when it opens) · 45–120 → stock now · 31–44 → stock window closed and promotion not open yet: "
                     "prepare creatives, do NOT order new stock · 3–30 → promote only · under 3 → too late"],
         "tests": [f"1) Makar Sankranti/Lohri/Pongal 2027-01-14, checked 2026-11-13 ({fp['days_to']} days) → {PHASE_SHORT[fp['phase']]}; "
                   f"stock between {fp['stock_from']} and {fp['stock_until']}, promote {fp['promote_from']} to {fp['promote_until']}, peak {fp['peak']}",
                   "2) " + _phase_line("Makar Sankranti/Lohri/Pongal", "2026-12-05"),
                   "3) " + _phase_line("Makar Sankranti/Lohri/Pongal", "2026-12-20"),
                   "4) " + _phase_line("Makar Sankranti/Lohri/Pongal", "2027-01-13")],
         "pages": [f"/tools/festival-stock-planner-india/{slugify(p['event'].split('/')[0])}-{p['date'][:4]} — {p['date']}: "
                   f"stock {p['stock_from']}→{p['stock_until']} · promote {p['promote_from']}→{p['promote_until']} · live by "
                   f"{(_d(p['promote_from']) - timedelta(days=7)).isoformat()} · today: {PHASE_SHORT[p['phase']]} · "
                   + ("SEO: enough lead time to rank" if (_d(p['promote_from']) - t).days >= 56 else
                      "SEO: under 8 weeks to the promote window — Google will not rank it in time; serves the channel, keep it for next year")
                   for p in events],
         "faq_extra": "When should I order stock for Diwali / Rakhi / Holi?", "next": "Check the margin first in the profit calculator.",
         "urgent": bool(events and (_d(events[0]["promote_from"]) - t).days <= 35)},
    ]


# ══════════════════════════════════════════════════════════════════════
# 4. Scoring
# ══════════════════════════════════════════════════════════════════════
TINY_REL = 0.005         # average interest below this share of the anchor = below Google's own resolution next to it (the 0.02
                         # first guess put 10 of 10 live candidates under it, so it separated nothing; calibrate from TOOL_CANDIDATES)
BURST_ACTIVE = 0.15      # searched on fewer than 15% of the 90 days = a burst or a trickle, not steady demand
SPIKE_SHARE = 0.6        # ≥60% of 90 days' interest inside the last 14 days = a news-style spike
SHAPE_MULT = {"ok": 1.0, "tiny": 0.5, "spike": 0.2, "unknown": 0.6, None: 0.6}


def shape_from_series(q_vals, anchor_vals=None, tiny_rel=None, spike_share=None):
    """Google Trends interest-over-time (daily, 90 days, India) for the query (and an anchor term compared in the same
    request) → how the demand looks. A % rise alone cannot tell a real trend from a tiny base or a news spike."""
    tiny_rel = TINY_REL if tiny_rel is None else tiny_rel
    spike_share = SPIKE_SHARE if spike_share is None else spike_share
    q = [float(x) for x in q_vals]
    n = len(q)
    if n < 30:
        return {"label": "unknown", "rel": None, "recent_share": None, "avg": None, "active": None}
    total = sum(q)
    avg = total / n
    am = (sum(float(x) for x in anchor_vals) / len(anchor_vals)) if anchor_vals else 0.0
    rel = (avg / am) if am > 0 else None
    recent = (sum(q[-14:]) / total) if total > 0 else 0.0
    active = sum(1 for x in q if x > 0) / n
    if total > 0 and recent >= spike_share:
        label = "spike"
    elif total == 0 or avg < 1.0 or active < BURST_ACTIVE or (rel is not None and rel < tiny_rel):
        label = "tiny"
    else:
        label = "ok"
    return {"label": label, "rel": rel, "recent_share": recent, "avg": avg, "active": active}


def with_volume(sh, q_vals, anchor_vals, tiny_rel=None):
    """The shape comes from the query ALONE (full 0–100 resolution: next to a big anchor most small queries read 0 or 1, which
    cannot show a spike). Volume comes from a second request against the anchor and can only demote a steady curve."""
    tiny_rel = TINY_REL if tiny_rel is None else tiny_rel
    if not sh or sh.get("label") == "unknown" or not anchor_vals or not q_vals:
        return sh
    am = sum(float(x) for x in anchor_vals) / len(anchor_vals)
    if am <= 0:
        return sh
    out = dict(sh)
    out["rel"] = (sum(float(x) for x in q_vals) / len(q_vals)) / am
    if out["label"] == "ok" and out["rel"] < tiny_rel:
        out["label"] = "tiny"
    return out


def shape_note(sh, anchor="the anchor term"):
    if not sh or sh.get("label") == "unknown":
        return "search volume NOT verified (Google Trends comparison unavailable this week)"
    if sh.get("nodata") or (sh.get("label") == "tiny" and sh.get("rel") is None and sh.get("recent_share") is None):
        return "90-day demand curve: TINY — Google Trends has no data for it (too few searches to chart)"
    rel = f"{sh['rel']:.1%} of '{anchor}'" if sh.get("rel") is not None else "volume vs anchor n/a"
    rs = f"{sh['recent_share']:.0%}" if sh.get("recent_share") is not None else "n/a"
    ac = f", searched on {sh['active']:.0%} of days" if sh.get("active") is not None else ""
    return f"90-day demand curve: {sh['label'].upper()} — {rel}, {rs} of the interest in the last 14 days{ac}"


def openness(web_pages):
    """Share of the opportunity left on page 1. None = not checked. Zero tool sites is NOT a free opportunity: it more
    often means nobody wants a tool for that search. 1–2 tool sites prove tool intent and are beatable."""
    if web_pages is None or web_pages < 0:
        return 0.7, "page 1 not checked ×0.7"
    if web_pages == 0:
        return 0.5, "0 tool sites on page 1 — no proof anyone wants a tool for this ×0.5"
    if web_pages <= 2:
        return 1.0, f"{web_pages} tool site{'s' if web_pages > 1 else ''} on page 1 (proves tool intent, beatable) ×1.00"
    o = 1 / (1 + 0.35 * (web_pages - 2))
    return o, f"{web_pages} tool sites on page 1 ×{o:.2f}"


def score(c, web=None):
    """→ (score 0–100, explanation). Transparent: every multiplier is printed in the ticket."""
    parts = []
    if c["type"] == "engine":
        s = float(c["base_score"])
        parts.append(f"engine-backed (logic already built and tested) {c['base_score']}" + (" (includes +10 calendar urgency)" if c.get("urgent") else ""))
        return round(min(100.0, s), 1), "; ".join(parts)
    label = (c.get("shape") or {}).get("label")
    if c["type"] == "ask":
        s = min(100.0, c["base"]) * (1.0 if c["requests"] >= 2 else 0.7)
        parts.append(f"asks score {c['base']:.0f} ({c['requests']} asks, {c['votes']} votes)" + ("" if c["requests"] >= 2 else " ×0.7 single ask"))
        if label == "spike":
            s *= 0.2; parts.append("×0.2 news-style spike")
        mult, txt = openness((web or {}).get("web_tool_pages") if web else None)
        s *= mult
        parts.append(txt)
        if c.get("seller"):
            s *= 1.25; parts.append("×1.25 seller aisle")
        return round(min(100.0, s), 1), "; ".join(parts)
    rise = min(float(c["rise"]), RISE_CAP)
    d_rise = (min(55.0, 15 * math.log10(1 + rise)) + 5) if c["rise"] >= 200 else 0.0
    d_top = min(55.0, 15 + 0.4 * float(c.get("top") or 0)) if c.get("top") else 0.0
    d = max(d_rise, d_top)
    if d_top > d_rise:
        parts.append(f"steady top search for the seller seeds (index {c['top']:.0f}/100) → {d:.0f}")
    else:
        parts.append(f"rise +{min(c['rise'], RISE_CAP):,.0f}%" + (" (capped: a bigger % only means a tinier base)" if c["rise"] > RISE_CAP else "") + f" → {d:.0f}")
    mult = SHAPE_MULT.get(label, 0.6)
    parts.append(f"{label} demand curve ×{mult}" if label in ("ok", "tiny", "spike") else "volume not verified ×0.6")
    if c.get("weeks", 1) >= 2:
        mult *= 1.3; parts.append("×1.3 seen in 2+ weeks")
    if c.get("seller"):
        mult *= 1.25; parts.append("×1.25 seller aisle")
    if c.get("trivial"):
        mult *= 0.6; parts.append("×0.6 simple tool (AI answers / big sites own these)")
    o, txt = openness((web or {}).get("web_tool_pages") if web else None)
    parts.append(txt)
    return round(min(100.0, d * mult * o), 1), "; ".join(parts)


# ══════════════════════════════════════════════════════════════════════
# 5. Ticket writing
# ══════════════════════════════════════════════════════════════════════
SPECS = {
    "calculator": dict(
        inputs="One numeric field per variable in the formula, India defaults (₹, %), instant result, reset button.",
        outputs="The result plus every intermediate step so people see HOW it was calculated; copy/share the result.",
        build="Cite the formula source on the page (official or authoritative). If no authoritative formula exists, REJECT this ticket.",
        tests="Three worked examples from the cited source must match exactly; blank/zero/negative input shows a helpful message, never NaN."),
    "generator": dict(
        inputs="A few options (style, length, keywords) with sensible defaults; one Generate button.",
        outputs="5–10 variations, each with a Copy button, plus Regenerate.",
        build="No personal data stored. Filter offensive output. Say plainly what is generated by rule and what is random.",
        tests="Never returns empty; never repeats the same item twice in one batch; works with keyboard only."),
    "converter": dict(
        inputs="From/to selectors with the most-used units first, one value field, swap button.",
        outputs="Live result, the conversion factor used, and a small table of common values.",
        build="State the standard used (SI / Indian convention). Precision: at least 6 significant digits, rounded for display only.",
        tests="Round-trip A→B→A returns the original within rounding; three published reference values match."),
    "checker": dict(
        inputs="Paste text or upload a file (processed in the browser).",
        outputs="A pass/fail list; each failure says what is wrong and how to fix it.",
        build="Rules are listed on the page so users know what is being checked; no data leaves the browser.",
        tests="One known-good and two known-bad samples are provided by the team and must give the right verdicts."),
    "planner": dict(
        inputs="Dates and a few choices; defaults filled so the page is useful before any typing.",
        outputs="A dated plan or checklist, add-to-calendar (.ics) and CSV export.",
        build="All dates computed in the browser; show the date rule used.",
        tests="Dates correct across a month and year boundary; works for today ±365 days."),
    "finder": dict(
        inputs="A search box plus 2–3 filters.",
        outputs="A short ranked list with the reason each item matched.",
        build="NEEDS A DATASET: the ticket must state where the data comes from and its licence; if it cannot be sourced legally, REJECT.",
        tests="Empty search shows the most popular items; a query with no match shows a helpful message."),
    "file": dict(
        inputs="Drag-and-drop or choose a file; a few options.",
        outputs="A downloadable result file; before/after preview where it makes sense.",
        build="Process fully in the browser: files are never uploaded. Say so on the page.",
        tests="A 20 MB file does not freeze the tab; output opens in the common apps."),
    "template": dict(
        inputs="Fill 3–6 fields to personalise.",
        outputs="Download as xlsx/docx/pdf with a live preview.",
        build="Original template content only (no copied files); state the licence for reuse.",
        tests="Every field appears in the download; the file opens in Excel, Word and Google Docs."),
}
GROUP_WHAT = {"calculator": "Instant result with every step shown.", "generator": "Generate ready-to-use options instantly.",
              "converter": "Convert instantly with the formula shown.", "checker": "Check it in seconds and see how to fix it.",
              "planner": "Get a dated plan you can export.", "finder": "Find the right option fast.",
              "file": "Done in your browser — files never leave your device.", "template": "Download a ready-to-use file."}
FAQ_GROUP = {"calculator": "How accurate is the result and which formula is used?",
             "generator": "Can I use the generated results commercially?",
             "converter": "Which standard or rate does it use?",
             "checker": "What does each check mean and how do I fix a failure?",
             "planner": "Can I export the plan or get a reminder?",
             "finder": "Where does the data come from and how current is it?",
             "file": "Are my files uploaded anywhere?",
             "template": "Can I edit the file and use it commercially?"}


def seo_title(title):
    """≤ 60 characters, never cut mid-word: the longest suffix that fits."""
    for suffix in (" — Free, No Signup | bizopstool", " — Free Online | bizopstool", " | bizopstool", ""):
        if len(title) + len(suffix) <= 60:
            return title + suffix
    return title[:60].rsplit(" ", 1)[0]


def meta_desc(title, what):
    s = f"{title}: {what} Free, no signup, works on your phone."
    return s[:155].rstrip()


PART_MARK = "\n=====PART=====\n"


def _elen(x):
    return len(html.escape(x))


def fit(sections, limit=BOX_LIMIT, min_rank=1, hard=True):
    """sections: [(name, lines, droppable_rank)] → text ≤ limit once HTML-escaped. Rank 0 = never dropped; the highest rank goes
    first, and only sections with rank ≥ min_rank may go. The note names what was left out. hard=False → None when it cannot fit."""
    def render(secs, dropped=()):
        out = []
        for name, lines, _ in secs:
            if name:
                out.append(name)
            out += lines
            out.append("")
        if dropped:
            out.append("… not in this box (full ticket in the attached file): " + ", ".join(dropped))
        return "\n".join(out).rstrip()
    secs, dropped = list(sections), []
    while _elen(render(secs, dropped)) > limit:
        cand = [i for i, x in enumerate(secs) if x[2] >= min_rank and x[2] > 0]
        if not cand:
            if not hard:
                return None
            break
        gone = secs.pop(max(cand, key=lambda i: secs[i][2]))
        dropped.append(gone[0].split(" — ")[0].split(" (")[0].title() if gone[0] else "a section")
    text = render(secs, dropped)
    if _elen(text) > limit:                          # pathological: hard cut
        text = text[: limit - 60] + "\n… (full text in the attached file)"
    return text


def pack(blocks, limit=BOX_LIMIT, label=""):
    """Lossless split for text too long for one Telegram message: blocks (sections) are kept whole where they fit, packed
    into parts ≤ limit once escaped, each part says 'part i/n — send all'. Nothing is ever cut off silently."""
    room = limit - 130
    pieces = []
    for b in blocks:
        if not str(b).strip():
            continue
        if _elen(b) <= room:
            pieces.append(b)
            continue
        cur = ""
        for line in str(b).split("\n"):
            while _elen(line) > room:
                cut = room
                while _elen(line[:cut]) > room and cut > 40:
                    cut -= 40
                sp = line.rfind(" ", 0, cut)
                cut = sp if sp > cut // 2 else cut
                if cur:
                    pieces.append(cur)
                    cur = ""
                pieces.append(line[:cut])
                line = line[cut:].lstrip()
            trial = (cur + "\n" + line) if cur else line
            if cur and _elen(trial) > room:
                pieces.append(cur)
                cur = line
            else:
                cur = trial
        if cur:
            pieces.append(cur)
    parts, cur = [], ""
    for pc in pieces:
        trial = (cur + "\n\n" + pc) if cur else pc
        if cur and _elen(trial) > room:
            parts.append(cur)
            cur = pc
        else:
            cur = trial
    if cur:
        parts.append(cur)
    n = len(parts)
    if n > 1:
        parts = [f"{label + ' · ' if label else ''}part {i}/{n} — send all {n} parts, in order\n\n{x}" for i, x in enumerate(parts, 1)]
    return parts


def ticket_parts(sections, label=""):
    """One box when it fits after dropping only what the standing rules already say (TRACK, KEEP / KILL) and our own SCORE line;
    otherwise EVERY section, split into parts — the team never receives half a ticket."""
    one = fit(sections, BOX_LIMIT, min_rank=7, hard=False)
    if one is not None:
        return [one]
    return pack(["\n".join(([name] if name else []) + list(lines)) for name, lines, _ in sections], BOX_LIMIT, label)


def make_ticket(c, tid, today, site="", neighbours=None, score_val=0.0, parts=""):
    t = _d(today)
    spec = SPECS[c["group"]]
    slug = c["slug"]
    path = SITE_PATH + slug
    url = (site.rstrip("/") + path) if site else path
    ship_by = _d(c.get("ship_by") or (t + timedelta(days=SHIP_DAYS)))
    kind = {"engine": "Wave 1 · seller aisle (engine-backed)", "search": "Steady search" if c.get("kind") == "top" else "Rising search",
            "ask": "Asked by users"}[c["type"]]
    nb = [n for n in (neighbours or []) if n != slug][:3]
    what_short = c.get("meta_what") or GROUP_WHAT[c["group"]]
    head = [f"{tid} · {kind} · priority {score_val:.0f}/100", c["title"],
            f"Build at: {path}", f'Target search: "{c["query"]}"',
            f"Ship by: {ship_by.isoformat()} · judged 8 weeks after it goes live"]
    if c["type"] == "engine":
        why = list(c["why"])
        for ev in (c.get("evidence") or [])[:2]:
            why.append(f"Google Trends India: \"{ev['query']}\" is a {'top' if ev['kind'] == 'top' else 'rising'} search for \"{ev['seed']}\" — "
                       "evidence that sellers look for this tool.")
        if c.get("shape"):
            why.append("Demand check: " + shape_note(c["shape"], ANCHOR))
            if c["shape"].get("label") in ("tiny", "unknown", "spike") or (c["shape"].get("rel") or 1) < 0.1:
                why.append("Expect few visitors from Google at first: this tool is found through the Telegram channel and the seller brief. "
                           "Judge it on tool_use as well as impressions.")
        who = c["who"]
        inputs, outputs, build = list(c["inputs"]), list(c["outputs"]), list(c["build"])
        formula, tests = list(c["formula"]), list(c["tests"])
    else:
        if c["type"] == "search" and c.get("kind") == "top":
            why = [f'Google Trends India (last 30 days): "{c["query"]}" is a top related search for "{(c.get("seeds") or ["the seller seeds"])[0]}" '
                   f'(index {c.get("top", 0):.0f}/100) — established, steady demand rather than a spike'
                   + (f"; seen in {c['weeks']} different weeks" if c["weeks"] >= 2 else "; first week seen") + ".",
                   "Steady ≠ easy to rank: judged by the 8-week rule."]
        elif c["type"] == "search":
            why = [f'Google Trends India (last 30 days): "{c["query"]}" is rising +{c["rise"]:,.0f}%'
                   + (f" — seen in {c['weeks']} different weeks" if c["weeks"] >= 2 else " — first week seen (early, unconfirmed)") + ".",
                   "Rising ≠ will keep rising: this is an early signal, judged by the 8-week rule."]
        else:
            why = [f"Asked by users: {c['requests']} asks (≈{c['votes']} votes) on {c['sources'] or 'public forums'}; market gap: {c['gap']}.",
                   f"Competition: {c['competition']}."]
            if c.get("reusable"):
                why.append(f"Reusable code (check licence): {c['reusable']}")
            why += [f"Ask: {u}" for u in c.get("links", [])]
        why.append("Demand check: " + shape_note(c.get("shape"), ANCHOR))
        why.append("Search-page competition: " + (f"{c['web_pages']} dedicated tool sites on page 1 ({c.get('web_examples', '')}) — differentiate: no signup, India defaults, explanation, speed."
                   if c.get("web_pages", -1) is not None and c.get("web_pages", -1) >= 0 else "not checked automatically — search the target query once; if 6+ dedicated tool sites rank, say how this will be better or REJECT."))
        who = "Anyone searching this phrase in India" + (" — mostly sellers/resellers (seller aisle)." if c.get("seller") else ".")
        inputs, outputs, build = [spec["inputs"]], [spec["outputs"]], [spec["build"]]
        formula, tests = [], [spec["tests"]]
    faqs = [f"How does the {c['noun'] or 'tool'} work?", "Is it free and do I need to sign up?", "Is my data stored?",
            FAQ_GROUP[c["group"]], c.get("faq_extra") or "What should I do with the result?"]
    page = [f"Title: {seo_title(c['title'])}", f"Meta description: {meta_desc(c['title'], what_short)}", f"H1: {c['title']}",
            "Questions to answer on the page (write real answers, 2–3 lines each): " + " · ".join(f"({i + 1}) {q}" for i, q in enumerate(faqs))]
    links = ([f"Link TO: " + ", ".join(SITE_PATH + n for n in nb) + " — and each of those links back here."] if nb else [])
    if c.get("next"):
        links.append(f"After the result, suggest: {c['next']}")
    track = ["Events: tool_view · tool_use (a result was shown) · tool_share · telegram_click · brief_optin"]
    judge = [f"KEEP if, 8 weeks after going live, Search Console shows ≥{BAR_IMPRESSIONS_WEEK} impressions a week (or ≥{BAR_CLICKS_28D} clicks in 28 days). "
             "Otherwise it is merged or removed — nothing personal. We detect the page going live ourselves; you do not need to report."]
    sections = [("WHY NOW", why, 4), ("WHO USES IT", [who], 5),
                ("WHAT IT DOES", ["Inputs: " + inputs[0]] + [("        " + x) for x in inputs[1:]] + ["Output: " + outputs[0]] + build, 0)]
    if formula:
        sections.append(("EXACT FORMULA (use as given)", formula, 0))
    sections.append(("TEST CASES — the page must reproduce these" if c["type"] == "engine" else "ACCEPTANCE CHECKS", tests, 0))
    if c.get("pages"):
        sections.append(("EVENT PAGES TO PUBLISH", c["pages"], 3))
    sections += [("PAGE", page, 2), ("LINKS", links, 6), ("TRACK", track, 8), ("KEEP / KILL", judge, 7),
                 ("SCORE", [parts], 9)]
    parts_box = ticket_parts([("", head, 0)] + sections, tid)
    md_lines = [f"## {tid} — {c['title']}", "", f"- {head[0]}"] + [f"- **{x.split(':', 1)[0]}**:{x.split(':', 1)[1]}" if ":" in x[:22] else f"- {x}" for x in head[2:]]
    for name, lines, _ in sections:
        md_lines += ["", f"**{name}**"] + [f"- {l.strip()}" for l in lines if l.strip()]
    md = "\n".join(md_lines)
    row = {"ticket_id": tid, "created": t.isoformat(), "key": c["key"], "type": c["type"], "title": c["title"],
           "query": c["query"], "slug": slug, "score": f"{score_val:.1f}", "status": "NEW", "ship_by": ship_by.isoformat(),
           "live_since": "", "judge_on": "", "impressions_28d": "", "clicks_28d": "", "decision": "", "notes": "",
           "text": md[:45000], "box": PART_MARK.join(parts_box)}
    return {"id": tid, "box": parts_box[0], "parts": parts_box, "md": md, "row": row, "url": url}


# ══════════════════════════════════════════════════════════════════════
# 6. Standing rules (sent once)
# ══════════════════════════════════════════════════════════════════════
def standing_rules(site=""):
    where = (site.rstrip("/") + SITE_PATH + "<slug>") if site else SITE_PATH + "<slug> on the bizopstool site"
    return "\n".join([
        f"BIZOPSTOOL — STANDING RULES FOR EVERY TICKET (v{STANDING_VERSION})",
        "",
        f"1. One tool = one page at {where} (the slug is in each ticket). No login; works on a 360px phone; usable in under 5 seconds.",
        "2. Compute in the browser wherever possible. Nothing a visitor types is stored or sent. No accounts.",
        "3. Every page has: the tool above the fold · a short 'how this works / how it is calculated' text (150+ words, written for this tool, nothing copied) · "
        "a worked example · 4–5 FAQs with real answers · 3 'related tools' links (given in the ticket) · Share (copy link + WhatsApp) · "
        "a 'Get the weekly trend brief' button to t.me/onezerodeals.",
        "4. Track 5 events: tool_view, tool_use (a result was shown), tool_share, telegram_click, brief_optin.",
        "5. Every page in sitemap.xml, indexable, with a canonical URL and no text duplicated between pages.",
        "6. Calculators must reproduce the TEST CASES in their ticket to ±₹0.01. If a test case looks wrong, tell us — do not silently change a formula.",
        "7. Never build or host: tax, legal, medical, loan, insurance, trading or betting tools; anything asking for Aadhaar/PAN; anything that scrapes a site against its terms. "
        "Under every calculator result show: 'Estimate — check your own numbers.'",
        "8. A page must be useful on its own, not a keyword page. If a ticket cannot be done well, reply REJECT + the reason. Rejecting is fine and fast.",
        "9. Judging: 8 weeks after a page goes live it is checked on Search Console impressions and real uses. Below the bar → merged or removed. "
        "We detect live pages automatically; you do not need to report back.",
        "10. Priority: a ticket's 'Ship by' date is a target, not a promise. Tell us early if it will slip.",
    ])


# ══════════════════════════════════════════════════════════════════════
# 7. Tracker: live detection, search stats, keep/kill, portfolio rules
# ══════════════════════════════════════════════════════════════════════
TICKET_COLS = ["ticket_id", "created", "key", "type", "title", "query", "slug", "score", "status", "ship_by",
               "live_since", "judge_on", "impressions_28d", "clicks_28d", "decision", "notes", "text", "box"]


def update_tracker(rows, today, live_fn=None, stats=None):
    """rows: TOOL_TICKETS records. live_fn(slug)->bool detects a published page; stats: {slug: (impressions28, clicks28)}.
    → (rows, events). Statuses: NEW → LIVE → KEEP | KILL (automatic)."""
    t = _d(today)
    events = []
    for r in rows:
        if r.get("type") == "rules" or not r.get("slug"):
            continue
        if r.get("status") == "NEW" and live_fn is not None:
            try:
                if live_fn(r["slug"]):
                    r["status"], r["live_since"], r["judge_on"] = "LIVE", t.isoformat(), (t + timedelta(days=JUDGE_AFTER_DAYS)).isoformat()
                    events.append(f"✅ live: {r['title']}")
            except Exception:
                pass
        if r.get("status") == "NEW" and r.get("type") == "search":
            why = classify(r.get("query", ""))            # a ticket from an older version that today's rules would refuse
            if why and why not in ("not a tool", "too short or long"):      # structural reasons are data quirks, not policy
                r["status"], r["decision"] = "WITHDRAWN", f"{t.isoformat()}: withdrawn — {why}"
                events.append(f"🗑 WITHDRAWN {r.get('ticket_id', '')} {r['title']} — {why}. Tell the team: do not build it (stop if already started).")
                continue
        if r.get("status") in ("LIVE", "KEEP", "KILL") and stats is not None:
            imp, clk = stats.get(r["slug"], (0, 0))
            r["impressions_28d"], r["clicks_28d"] = str(int(imp)), str(int(clk))
        if r.get("status") == "LIVE" and r.get("live_since") and stats is not None:
            age = (t - _d(r["live_since"])).days
            imp, clk = _n(r.get("impressions_28d")), _n(r.get("clicks_28d"))
            if age >= JUDGE_AFTER_DAYS:
                ok = imp / 4 >= BAR_IMPRESSIONS_WEEK or clk >= BAR_CLICKS_28D
                r["status"] = "KEEP" if ok else "KILL"
                r["decision"] = (f"{t.isoformat()}: {'meets' if ok else 'below'} the bar after {age} days "
                                 f"({imp:.0f} impressions / {clk:.0f} clicks in 28 days)")
                events.append(f"{'🟢 KEEP' if ok else '🔴 KILL (merge or remove — first ask the team for its tool_use count)'}: {r['title']} — {imp:.0f} impressions, {clk:.0f} clicks (28 days)")
            elif age >= 28 and imp == 0:
                r["decision"] = f"{t.isoformat()}: no impressions after {age} days — check sitemap, internal links, indexing"
                events.append(f"⚠️ no impressions after {age} days: {r['title']} — ask the team to check sitemap/indexing")
    return rows, events


def portfolio(rows, today, have_site=False, have_gsc=False):
    t = _d(today)
    allr = [r for r in rows if r.get("type") != "rules" and r.get("slug")]
    tk = [r for r in allr if r.get("status") != "WITHDRAWN"]
    by = {s: [r for r in tk if r.get("status") == s] for s in ("NEW", "LIVE", "KEEP", "KILL")}
    wd = len(allr) - len(tk)
    lines = [f"Tickets {len(tk)} · waiting on the team {len(by['NEW'])} · live {len(by['LIVE'])} · kept {len(by['KEEP'])} · removed {len(by['KILL'])}"
             + (f" · withdrawn {wd}" if wd else "")]
    late = [r for r in by["NEW"] if _d(r["ship_by"]) + timedelta(days=7) < t]
    if late:
        lines.append(f"⏰ {len(late)} past their ship-by date: " + ", ".join(r["slug"] for r in late[:5]))
    ages = [(t - _d(r["live_since"])).days for r in tk if r.get("live_since")]
    judged = [r for r in tk if r.get("live_since") and (t - _d(r["live_since"])).days >= JUDGE_AFTER_DAYS]
    if len(judged) >= 3:
        met = [r for r in judged if _n(r.get("impressions_28d")) / 4 >= BAR_IMPRESSIONS_WEEK or _n(r.get("clicks_28d")) >= BAR_CLICKS_28D]
        if len(met) < 3:
            lines.append(f"⚠️ Week-8 rule: only {len(met)} of {len(judged)} judged tools meet the bar — change the category mix.")
    if ages and max(ages) >= 112:
        clicks = sum(_n(r.get("clicks_28d")) for r in tk if r.get("status") in ("LIVE", "KEEP"))
        if clicks < 1000:
            lines.append(f"🛑 Week-16 rule: {clicks:.0f} search clicks in 28 days (< 1,000) — park the supermarket lane; put the time into Amazon affiliate + seller brief.")
    setup = []
    if not have_site:
        setup.append("BIZOPS_SITE not set → I cannot see which pages are live")
    if not have_gsc:
        setup.append("GSC_SITE not set → no automatic impressions (weekly number = 0 until set)")
    if setup:
        lines.append("Setup: " + " · ".join(setup))
    return lines


def nudge_text(rows, today):
    t = _d(today)
    late = [r for r in rows if r.get("status") == "NEW" and r.get("slug") and _d(r["ship_by"]) + timedelta(days=7) < t]
    if not late:
        return ""
    return ("Status check: these tickets are past their ship-by date. For each, please reply LIVE (with the link), "
            "ETA (with a date) or REJECT (with the reason):\n" + "\n".join(f"- {r['ticket_id']} {r['title']} (ship-by {r['ship_by']})" for r in late))


# ══════════════════════════════════════════════════════════════════════
# 8. Selection
# ══════════════════════════════════════════════════════════════════════
def pick(scored, existing_rows, today, n=5, max_open=8, min_score=25.0, reserve=2):
    """scored: [(score, parts, cand)] best first → picks not already ticketed (or killed in the last 90 days), limited by n
    per week and by the team's open backlog. Non-engine candidates must clear `min_score` (better no ticket than a junk
    one); a rising search also needs a demand-curve check (never ticketed blind); at least `reserve` slots go to them
    when they exist, so Wave 1 never crowds out everything else."""
    t = _d(today)
    have = {r.get("key") for r in existing_rows}
    killed_recent = {r.get("key") for r in existing_rows if r.get("status") == "KILL"
                     and r.get("decision") and str(r["decision"])[:10] >= (t - timedelta(days=90)).isoformat()}
    open_n = sum(1 for r in existing_rows if r.get("status") == "NEW" and r.get("type") != "rules")
    this_week = sum(1 for r in existing_rows if r.get("type") != "rules" and r.get("status") != "WITHDRAWN"
                    and str(r.get("created", "")) >= week_start(t))
    room = max(0, min(n - this_week, max_open - open_n))       # weekly allowance AND the team's backlog cap
    fresh = [x for x in scored if x[2]["key"] not in have and x[2]["key"] not in killed_recent]
    verified = lambda c: c["type"] != "search" or (c.get("shape") or {}).get("label") in ("ok", "tiny", "spike")
    eng = [x for x in fresh if x[2]["type"] == "engine"]
    oth = [x for x in fresh if x[2]["type"] != "engine" and verified(x[2]) and x[0] >= min_score]
    below = sum(1 for x in fresh if x[2]["type"] != "engine" and verified(x[2]) and x[0] < min_score)
    unverified = sum(1 for x in fresh if x[2]["type"] == "search" and not verified(x[2]))
    k_oth = min(len(oth), max(reserve, room - len(eng)), room)
    k_eng = min(len(eng), room - k_oth)
    out = sorted(eng[:k_eng] + oth[:k_oth], key=lambda x: -x[0])
    return out, {"room": room, "open": open_n, "this_week": this_week, "below_bar": below, "unverified": unverified, "min_score": min_score,
                 "n": n, "max_open": max_open}


def next_ids(existing_rows, today, count):
    pref = f"TW-{week_id(today)}-"
    used = [int(r["ticket_id"].split("-")[-1]) for r in existing_rows if str(r.get("ticket_id", "")).startswith(pref)]
    start = (max(used) if used else 0) + 1
    return [f"{pref}{start + i:02d}" for i in range(count)]


def neighbours_for(c, all_slugs, seller_slugs):
    """Three related pages: prefer the seller aisle for seller tools, otherwise anything else this run."""
    pool = [s for s in (seller_slugs if c.get("seller") else all_slugs) if s != c["slug"]]
    pool += [s for s in all_slugs if s not in pool and s != c["slug"]]
    return pool[:3]


def build_digest(week, picks_made, stats, portfolio_lines, events, nudge, first_run, filtered=None, considered=None, evidence=None):
    """→ header message (HTML) for the admin chat."""
    e = html.escape
    L = [f"🧰 <b>Tool Watch</b> — week of {week} (admin only · copy-paste tickets for the bizopstool team)"]
    kinds = {}
    for _, _, c in picks_made:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    names = {"engine": "engine-backed seller tools", "search": "rising searches", "ask": "asked-for tools"}
    L.append(f"New tickets: {len(picks_made)}" + (" (" + ", ".join(f"{v} {names[k]}" for k, v in kinds.items()) + ")" if kinds else "")
             + f" · open backlog {stats['open'] + len(picks_made)} of {stats.get('cap', '?')}")
    L.append(f"Feeds: calendar {stats.get('events', 0)} upcoming events · Google Trends {stats.get('trend_raw', 0)} rising searches → "
             f"{stats.get('trend_kept', 0)} tool-shaped · asks {stats.get('asks', 0)} · page-1 crowding "
             + ("checked" if stats.get("web_checked") else "not checked (no BRAVE_API_KEY)"))
    if picks_made:
        L.append("\n<b>How to use:</b> tap a grey box below to copy it and paste it to the team. That is all — live pages and results are tracked automatically.")
        L.append("The same tickets are in the attached file and in the TOOL_TICKETS tab.")
    else:
        if stats["room"] > 0:
            why0 = " — nothing new cleared the filters."
        elif stats.get("this_week", 0) >= stats.get("n", 5):
            why0 = (f": this week's allowance of {stats.get('n', 5)} tickets is already used (they were sent earlier this week — "
                    "run the workflow with resend = 1 to send them again). New ones are made next Monday.")
        else:
            why0 = f": the team already has {stats.get('open', 0)} unfinished tickets (limit {stats.get('max_open', 8)}) — ship those first."
        L.append("\nNo new tickets this week" + why0)
    if first_run:
        L.append("A one-time <b>standing rules</b> box comes first — send it to the team once.")
    if stats.get("below_bar"):
        L.append(f"{stats['below_bar']} candidate{'s' if stats['below_bar'] != 1 else ''} below the quality bar ({stats.get('min_score', 25):.0f}/100) — "
                 "no ticket is better than a junk ticket.")
    if stats.get("skipped"):
        L.append(f"{stats['skipped']} search{'es' if stats['skipped'] != 1 else ''} not curve-checked this week (limit {stats.get('shape_cap', 10)} a week "
                 "to stay inside Google Trends limits) — the strongest unchecked ones go first next week; never ticketed unchecked.")
    if stats.get("refused"):
        L.append(f"{stats['refused']} search{'es' if stats['refused'] != 1 else ''} could not be checked because Google Trends refused — never ticketed blind; retried next week.")
    if filtered:
        groups = {}
        for r in filtered:
            groups.setdefault(r["reason"], []).append(r)
        parts = []
        for reason, rs in sorted(groups.items(), key=lambda kv: -max(x["rise"] for x in kv[1])):
            rs = sorted(rs, key=lambda x: -x["rise"])
            ex = ", ".join(f"{x['query'][:38]}" + (f" +{min(x['rise'], 99999):,.0f}%" if x.get("kind", "rising") == "rising" else " (top)") for x in rs[:2])
            parts.append(f"{reason} ×{len(rs)} ({ex})")
        L.append("🚫 <b>Filtered (and why):</b> " + e(" · ".join(parts[:6])))
    if evidence:
        L.append("📊 <b>Demand evidence for the Wave-1 tools</b> (Google Trends, seller seeds): " + e(" · ".join(
            f"{x['title']} ← “{x['query']}” ({'top search' if x['kind'] == 'top' else 'rising'} for “{x['seed']}”)" for x in evidence[:4])))
    elif evidence is not None:
        L.append("📊 No Google Trends evidence for any Wave-1 tool this week (top + rising lists of the seller seeds) — their demand is still "
                 "unmeasured; Search Console tells after they go live.")
    if considered:
        L.append("🤔 <b>Considered, not ticketed:</b> " + e(" · ".join(f"{c['query'][:40]} {sc:.0f}/100" for sc, _p, c in considered[:5])))
    for ev in events:
        L.append(e(ev))
    L += [e(x) for x in portfolio_lines]
    if nudge:
        L.append("\n⏰ <b>Copy this to the team:</b>\n<pre>" + e(nudge) + "</pre>")
    L.append("\n<i>Honest limits: rising ≠ will rise (early signal only) · a new site takes weeks to rank · most tools will get little — "
             "the queue is a portfolio with kill rules, not a promise.</i>")
    return "\n".join(L)


def stats_by_slug(gsc_rows, slugs):
    """Search Console page rows → {slug: (impressions, clicks)}; an event page under /tools/<slug>/… counts for <slug>."""
    out = {s: [0, 0] for s in slugs}
    for r in gsc_rows or []:
        url = str((r.get("keys") or [""])[0])
        path = re.sub(r"^https?://[^/]+", "", url).split("?")[0].split("#")[0].rstrip("/")
        for s in slugs:
            base = SITE_PATH + s
            if path == base or path.startswith(base + "/"):
                out[s][0] += int(r.get("impressions", 0))
                out[s][1] += int(r.get("clicks", 0))
    return {k: tuple(v) for k, v in out.items()}
