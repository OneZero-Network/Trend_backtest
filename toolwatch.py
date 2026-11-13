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
    return {"event": event, "date": d.isoformat(), "days_to": (d - t).days,
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
SELLER_SEEDS = ["amazon seller", "flipkart seller", "meesho seller", "dropshipping", "reselling", "online selling"]
TREND_SEEDS = NOUN_SEEDS + SELLER_SEEDS
NOT_TOOL = re.compile(r"\b(login|log in|sign ?in|download|apk|near me|status|result|results|today|live|news|"
                      r"release date|customer care|contact|number|app|movie|song|lyrics|cricket|ipl|job|jobs|"
                      r"vacancy|admit card|cut ?off)\b", re.I)
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


def tool_shaped(q):
    s = str(q)
    words = re.findall(r"[a-z0-9+#]+", s.lower())
    return (bool(tool_noun(s)) and 2 <= len(words) <= 6 and not discovery.BANNED.search(s)
            and not NOT_TOOL.search(s) and not automation.REGULATED.search(s))


def canon_key(q):
    words = [w for w in re.findall(r"[a-z0-9+#]+", str(q).lower()) if w not in NOISE]
    return "-".join(words)


def nice_title(q):
    words = [w for w in re.findall(r"[A-Za-z0-9+#&'-]+", str(q)) if w.lower() not in {"online", "free", "india", "2026", "2027"}]
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in words) or str(q).title()


def trend_candidates(raw, seen, today):
    """raw: [{'query','rise','seed'}] (Google Trends rising, India, 30 days) → candidates, one per canonical key.
    seen: {key: row} from TOOLWATCH_SEEN (weeks seen so far)."""
    best = {}
    for r in raw:
        q = str(r.get("query", ""))
        if not tool_shaped(q):
            continue
        rise = discovery.rise_value(r.get("rise"))
        if rise < 200:
            continue
        key = canon_key(q)
        if not key:
            continue
        c = best.get(key)
        if c is None:
            best[key] = {"key": "q:" + key, "type": "search", "query": q.lower().strip(), "rise": rise, "seeds": {r.get("seed")}}
        else:
            c["rise"] = max(c["rise"], rise)
            c["seeds"].add(r.get("seed"))
    out = []
    for key, c in best.items():
        s = seen.get(c["key"]) or {}
        noun = tool_noun(c["query"])
        out.append({**c, "title": nice_title(c["query"]), "slug": slugify(key), "noun": noun,
                    "group": NOUN_GROUP.get(noun, "calculator"), "weeks": int(_n(s.get("weeks"), 0)) + (0 if s.get("last_week") == week_start(today) else 1),
                    "seller": bool(SELLER_FIT.search(c["query"])), "trivial": bool(TRIVIAL.search(c["query"])),
                    "seeds": sorted(x for x in c["seeds"] if x)})
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
        if not name or discovery.BANNED.search(name):
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


def engine_candidates(today):
    t = _d(today)
    A = unit_econ(499, 150)
    B = unit_econ(299, 180)
    C = unit_econ(999, 400, itc=0, referral=0.15, rto=0.25, ship=80)
    def ue(price, cost, e, extra=""):
        return (f"price ₹{price}, cost ₹{cost}{extra} → fees {_money(e['fees'])}, GST payable {_money(e['gst_pay'])}, "
                f"RTO cost {_money(e['rto_cost'])}, profit {_money(e['profit'])}, margin {e['margin']:.1%}, "
                f"ROI {e['roi']:.1%} → {e['decision']}")
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
                     "verdict: >120 days → too early · 45–120 → stock now · 3–30 → promote only (too late to stock) · <3 → too late"],
         "tests": [f"1) Makar Sankranti/Lohri/Pongal 2027-01-14, checked on 2026-11-13 → stock between {fp['stock_from']} and {fp['stock_until']}, "
                   f"promote {fp['promote_from']} to {fp['promote_until']}, peak {fp['peak']}, {fp['days_to']} days to go"]
                  + [f"{i + 2}) {p['event']} {p['date']}: stock {p['stock_from']}→{p['stock_until']}, promote {p['promote_from']}→{p['promote_until']} — page live by {(_d(p['promote_from']) - timedelta(days=7)).isoformat()}"
                     for i, p in enumerate(events[:6])],
         "pages": [f"/tools/festival-stock-planner-india/{slugify(p['event'].split('/')[0])}-{p['date'][:4]}" for p in events],
         "faq_extra": "When should I order stock for Diwali / Rakhi / Holi?", "next": "Check the margin first in the profit calculator.",
         "urgent": bool(events and (_d(events[0]["promote_from"]) - t).days <= 35)},
    ]


# ══════════════════════════════════════════════════════════════════════
# 4. Scoring
# ══════════════════════════════════════════════════════════════════════
def openness(web_pages):
    """Share of the opportunity left after what page 1 already offers. None = not checked."""
    if web_pages is None or web_pages < 0:
        return 0.7, "page 1 not checked ×0.7"
    return 1 / (1 + 0.35 * web_pages), f"{web_pages} dedicated tool sites on page 1 ×{1 / (1 + 0.35 * web_pages):.2f}"


def score(c, web=None):
    """→ (score 0–100, explanation). Transparent: every multiplier is printed in the ticket."""
    parts = []
    if c["type"] == "engine":
        s = float(c["base_score"])
        parts.append(f"engine-backed (logic already built and tested) {c['base_score']}" + (" (includes +10 calendar urgency)" if c.get("urgent") else ""))
        return round(min(100.0, s), 1), "; ".join(parts)
    if c["type"] == "ask":
        s = min(100.0, c["base"]) * (1.0 if c["requests"] >= 2 else 0.7)
        parts.append(f"asks score {c['base']:.0f} ({c['requests']} asks, {c['votes']} votes)" + ("" if c["requests"] >= 2 else " ×0.7 single ask"))
        mult, txt = openness((web or {}).get("web_tool_pages") if web else None)
        s *= mult
        parts.append(txt)
        if c.get("seller"):
            s *= 1.25; parts.append("×1.25 seller aisle")
        return round(min(100.0, s), 1), "; ".join(parts)
    d = min(55.0, 15 * math.log10(1 + c["rise"])) + 5
    parts.append(f"rising +{c['rise']:,.0f}% → {d:.0f}")
    mult = 1.0
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


def fit(sections, limit=BOX_LIMIT):
    """sections: [(name, lines, droppable_rank)] → text ≤ limit once HTML-escaped. Rank 0 = never dropped; the
    highest rank goes first. If anything is dropped a pointer to the attached file is added."""
    def render(secs, note=False):
        out = []
        for name, lines, _ in secs:
            if name:
                out.append(name)
            out += lines
            out.append("")
        if note:
            out.append("… more in the attached file (and the TOOL_TICKETS tab)")
        return "\n".join(out).rstrip()
    secs, dropped = list(sections), False
    while len(html.escape(render(secs, dropped))) > limit:
        cand = [i for i, x in enumerate(secs) if x[2] > 0]
        if not cand:
            break
        secs.pop(max(cand, key=lambda i: secs[i][2]))
        dropped = True
    text = render(secs, dropped)
    if len(html.escape(text)) > limit:               # pathological: hard cut
        text = text[: limit - 60] + "\n… (full text in the attached file)"
    return text


def make_ticket(c, tid, today, site="", neighbours=None, score_val=0.0, parts=""):
    t = _d(today)
    spec = SPECS[c["group"]]
    slug = c["slug"]
    path = SITE_PATH + slug
    url = (site.rstrip("/") + path) if site else path
    ship_by = _d(c.get("ship_by") or (t + timedelta(days=SHIP_DAYS)))
    kind = {"engine": "Wave 1 · seller aisle (engine-backed)", "search": "Rising search", "ask": "Asked by users"}[c["type"]]
    nb = [n for n in (neighbours or []) if n != slug][:3]
    what_short = c.get("meta_what") or GROUP_WHAT[c["group"]]
    head = [f"{tid} · {kind} · priority {score_val:.0f}/100", c["title"],
            f"Build at: {path}", f'Target search: "{c["query"]}"',
            f"Ship by: {ship_by.isoformat()} · judged 8 weeks after it goes live"]
    if c["type"] == "engine":
        why = list(c["why"])
        who = c["who"]
        inputs, outputs, build = list(c["inputs"]), list(c["outputs"]), list(c["build"])
        formula, tests = list(c["formula"]), list(c["tests"])
    else:
        if c["type"] == "search":
            why = [f'Google Trends India (last 30 days): "{c["query"]}" is rising +{c["rise"]:,.0f}%'
                   + (f" — seen in {c['weeks']} different weeks" if c["weeks"] >= 2 else " — first week seen (early, unconfirmed)") + ".",
                   "Rising ≠ will keep rising: this is an early signal, judged by the 8-week rule."]
        else:
            why = [f"Asked by users: {c['requests']} asks (≈{c['votes']} votes) on {c['sources'] or 'public forums'}; market gap: {c['gap']}.",
                   f"Competition: {c['competition']}."]
            if c.get("reusable"):
                why.append(f"Reusable code (check licence): {c['reusable']}")
            why += [f"Ask: {u}" for u in c.get("links", [])]
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
    box = fit([("", head, 0)] + sections)
    md_lines = [f"## {tid} — {c['title']}", "", f"- {head[0]}"] + [f"- **{x.split(':', 1)[0]}**:{x.split(':', 1)[1]}" if ":" in x[:22] else f"- {x}" for x in head[2:]]
    for name, lines, _ in sections:
        md_lines += ["", f"**{name}**"] + [f"- {l.strip()}" for l in lines if l.strip()]
    md = "\n".join(md_lines)
    row = {"ticket_id": tid, "created": t.isoformat(), "key": c["key"], "type": c["type"], "title": c["title"],
           "query": c["query"], "slug": slug, "score": f"{score_val:.1f}", "status": "NEW", "ship_by": ship_by.isoformat(),
           "live_since": "", "judge_on": "", "impressions_28d": "", "clicks_28d": "", "decision": "", "notes": "",
           "text": md[:45000]}
    return {"id": tid, "box": box, "md": md, "row": row, "url": url}


# ══════════════════════════════════════════════════════════════════════
# 6. Standing rules (sent once)
# ══════════════════════════════════════════════════════════════════════
def standing_rules(site=""):
    where = (site.rstrip("/") + SITE_PATH + "<slug>") if site else "<site>" + SITE_PATH + "<slug>"
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
               "live_since", "judge_on", "impressions_28d", "clicks_28d", "decision", "notes", "text"]


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
                events.append(f"{'🟢 KEEP' if ok else '🔴 KILL (merge or remove)'}: {r['title']} — {imp:.0f} impressions, {clk:.0f} clicks (28 days)")
            elif age >= 28 and imp == 0:
                r["decision"] = f"{t.isoformat()}: no impressions after {age} days — check sitemap, internal links, indexing"
                events.append(f"⚠️ no impressions after {age} days: {r['title']} — ask the team to check sitemap/indexing")
    return rows, events


def portfolio(rows, today, have_site=False, have_gsc=False):
    t = _d(today)
    tk = [r for r in rows if r.get("type") != "rules" and r.get("slug")]
    by = {s: [r for r in tk if r.get("status") == s] for s in ("NEW", "LIVE", "KEEP", "KILL")}
    lines = [f"Tickets {len(tk)} · waiting on the team {len(by['NEW'])} · live {len(by['LIVE'])} · kept {len(by['KEEP'])} · removed {len(by['KILL'])}"]
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
def pick(scored, existing_rows, today, n=5, max_open=8):
    """scored: [(score, parts, cand)] best first → picks not already ticketed (or killed in the last 90 days),
    limited by n per week and by the team's open backlog."""
    t = _d(today)
    have = {r.get("key") for r in existing_rows}
    killed_recent = {r.get("key") for r in existing_rows if r.get("status") == "KILL"
                     and r.get("decision") and str(r["decision"])[:10] >= (t - timedelta(days=90)).isoformat()}
    open_n = sum(1 for r in existing_rows if r.get("status") == "NEW" and r.get("type") != "rules")
    this_week = sum(1 for r in existing_rows if r.get("type") != "rules" and str(r.get("created", "")) >= week_start(t))
    room = max(0, min(n - this_week, max_open - open_n))       # weekly allowance AND the team's backlog cap
    out = []
    for s, parts, c in scored:
        if len(out) >= room:
            break
        if c["key"] in have or c["key"] in killed_recent:
            continue
        out.append((s, parts, c))
    return out, {"room": room, "open": open_n, "this_week": this_week}


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


def build_digest(week, picks_made, stats, portfolio_lines, events, nudge, first_run):
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
        L.append("\nNo new tickets this week" + (": the team's backlog is full — ship those first." if stats["room"] == 0 else " — nothing new cleared the filters."))
    if first_run:
        L.append("A one-time <b>standing rules</b> box comes first — send it to the team once.")
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
