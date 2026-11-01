#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  BAZAAR RADAR — India E-commerce Trend Fetcher v1.0                ║
║  Same architecture as Fortress Sniper: GitHub Actions → Sheets →   ║
║  Telegram. Runs headless, all config via env / GitHub Secrets.      ║
║                                                                      ║
║  Secrets (all optional — without Google ones it writes CSVs to     ║
║  ./output so you can test locally):                                ║
║    GOOGLE_CREDS_JSON   — full contents of credentials.json         ║
║    GOOGLE_SHEET_ID     — your spreadsheet ID                       ║
║    TELEGRAM_BOT_TOKEN  — from @BotFather                           ║
║    TELEGRAM_CHAT_ID    — your chat/group ID                        ║
║    BR_CATEGORIES       — comma list overriding DEFAULT_CATEGORIES  ║
║    BR_SEED_KEYWORDS    — comma list for Google Trends rising query ║
║                                                                      ║
║  SOURCES                                                            ║
║    1. Amazon.in Best Sellers     (what sells NOW, hourly-updated)  ║
║    2. Amazon.in Movers & Shakers (biggest 24h rank jumps — leading)║
║    3. Amazon.in New Releases     (new launches gaining traction)   ║
║    4. Google Trends India        (demand BEFORE it hits carts)     ║
║                                                                      ║
║  WHY HISTORY MATTERS                                                ║
║    A single bestseller snapshot is NOT a trend — it's a ranking.   ║
║    Every run appends to the HISTORY tab; trend signals come from   ║
║    rank velocity, persistence and review-count growth over days.   ║
║    Expect weak signals for the first ~7 days of running.           ║
║                                                                      ║
║  OUTPUT TABS                                                        ║
║    AMZ_BESTSELLERS · AMZ_MOVERS · AMZ_NEW_RELEASES · GTRENDS       ║
║    HISTORY (rolling 60d) · TREND_SIGNALS (ranked) · RUN_LOG        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, re, sys, json, math, time, random, traceback, html as _html
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discount_rule import discount_status            # shared with backtest — one rule
import calculator, compliance, youtube_check, sourcing, picks, brief
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

try:
    import requests
    import pandas as pd
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"[FATAL] Missing dependency: {e}", flush=True)
    print("Run: pip install requests pandas beautifulsoup4 gspread google-auth", flush=True)
    sys.exit(1)

# curl_cffi = real-browser TLS fingerprint. Amazon blocks plain `requests`
# far more aggressively than NSE does; this is close to mandatory.
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CURL_CFFI = False

IST = timezone(timedelta(hours=5, minutes=30))
NOW_IST = datetime.now(IST)
TODAY = NOW_IST.strftime("%Y-%m-%d")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════

GOOGLE_CREDS_JSON  = os.environ.get("GOOGLE_CREDS_JSON", "")
GOOGLE_SHEET_ID    = os.environ.get("GOOGLE_SHEET_ID", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GH_REPO            = os.environ.get("GITHUB_REPOSITORY", "")
GH_RUN_ID          = os.environ.get("GITHUB_RUN_ID", "")
LOCAL_OUT_DIR      = os.environ.get("BR_OUTPUT_DIR", "output")

# Amazon.in browse slugs  →  friendly name
DEFAULT_CATEGORIES = {
    "electronics":      "Electronics",
    "computers":        "Computers & Accessories",
    "kitchen":          "Home & Kitchen",
    "beauty":           "Beauty",
    "hpc":              "Health & Personal Care",
    "apparel":          "Clothing & Accessories",
    "shoes":            "Shoes & Handbags",
    "sports":           "Sports, Fitness & Outdoors",
    "toys":             "Toys & Games",
    "baby":             "Baby",
    "grocery":          "Grocery & Gourmet",
    "home-improvement": "Home Improvement",
    "garden":           "Garden & Outdoors",
    "pet-supplies":     "Pet Supplies",
    "automotive":       "Car & Motorbike",
    "watches":          "Watches",
    "jewelry":          "Jewellery",
    "luggage":          "Bags & Luggage",
    "books":            "Books",
}
_cat_env = (os.environ.get("BR_CATEGORIES") or "").strip()
CATEGORIES = ({c.strip(): DEFAULT_CATEGORIES.get(c.strip(), c.strip())
               for c in _cat_env.split(",") if c.strip()} if _cat_env else DEFAULT_CATEGORIES)

LISTS = {
    "bestsellers":       "https://www.amazon.in/gp/bestsellers/{slug}/",
    "movers":            "https://www.amazon.in/gp/movers-and-shakers/{slug}/",
    "new_releases":      "https://www.amazon.in/gp/new-releases/{slug}/",
}

_DEFAULT_SEEDS = ("smartwatch,air fryer,sunscreen,protein powder,earbuds,saree,kurti,sneakers,"
                  "skincare,robot vacuum")
# `or` (not a get() default): an EMPTY repo variable must also fall back to the defaults
SEED_KEYWORDS = [k.strip() for k in (os.environ.get("BR_SEED_KEYWORDS") or _DEFAULT_SEEDS).split(",") if k.strip()]

BR_DETAIL_TOP_N = int(os.environ.get("BR_DETAIL_TOP_N") or "30")   # product pages fetched per run
PRODUCT_EXTRA_GAP_SEC = 4.0     # added on top of the normal throttle for /dp/ pages
DETAIL_BLOCK_STREAK = 3         # consecutive captcha'd product pages → stop product pages for this run
DETAIL_STATS = {}
BR_CALC_TOP_N   = int(os.environ.get("BR_CALC_TOP_N") or "25")     # actionable signals pushed to CALCULATOR
# Day-1 scores are ties (no history) → don't ADD rows until enough history exists (rows already there still refresh)
BR_CALC_MIN_DAYS = int(os.environ.get("BR_CALC_MIN_DAYS") or "7")
BR_CALC_EXCLUDE = {c.strip() for c in (os.environ.get("BR_CALC_EXCLUDE") or "books").split(",") if c.strip()}
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
BR_YT_TOP_N     = int(os.environ.get("BR_YT_TOP_N", "15"))       # ≈101 quota units each; free quota 10,000/day
# State trends run in their own weekly workflow (BR_MODE=states), not in the daily run:
# ~20 rate-limited Google calls would slow the daily run and risk blocking rising-queries.
BR_MODE         = os.environ.get("BR_MODE") or "daily"          # daily | states | sourcing | affiliate | brief
BR_SOURCING_TOP_N   = int(os.environ.get("BR_SOURCING_TOP_N") or "20")     # products searched per run
AFFILIATE_TAG     = os.environ.get("AFFILIATE_TAG") or ""         # your Amazon Associates tag, e.g. name-21
AFFILIATE_CHAT_ID = os.environ.get("AFFILIATE_CHAT_ID") or ""     # PUBLIC channel; drafts go to your private chat if unset
BR_AFF_TOP_N      = int(os.environ.get("BR_AFF_TOP_N") or "8")
BR_AFF_MIN_DAYS   = int(os.environ.get("BR_AFF_MIN_DAYS") or "7")
AFFILIATE_POST_GAP_SEC = 3.0
# Optional X auto-posting (paid, pay-per-use). Leave unset → you get one-tap "Post on X" buttons instead.
X_CREDS = {k: os.environ.get(v) or "" for k, v in (("api_key", "X_API_KEY"), ("api_secret", "X_API_SECRET"),
           ("access_token", "X_ACCESS_TOKEN"), ("access_secret", "X_ACCESS_SECRET"))}
X_MAX_POSTS_PER_RUN = int(os.environ.get("X_MAX_POSTS_PER_RUN") or "8")    # your spend cap per week
BR_SOURCING_RECHECK = int(os.environ.get("BR_SOURCING_RECHECK_DAYS") or "7")
BR_STATE_TRENDS = os.environ.get("BR_STATE_TRENDS", "0") == "1" or BR_MODE == "states"
# Discount penalty — MUST match backtest/config.py (DISCOUNT_*); test_fetcher.py enforces it.
PRICE_DROP_PCT         = 15     # price at today vs max of prior LOOKBACK days, while climbing
DISCOUNT_LOOKBACK_DAYS = 7
DISCOUNT_PENALTY       = 0.5    # momentum & mover multiplied by this when triggered
DISCOUNT_SUSTAIN_HOURS = 48     # needs today + 2 previous daily snapshots below threshold
DISCOUNT_FRESH_HOURS   = 36     # daily snapshots (backtest uses 72h for Keepa's sparser sampling)

HISTORY_DAYS = 60
MIN_GAP_SEC  = 4.0          # polite + less likely to trip Amazon's bot wall
SHEET_HISTORY = "HISTORY"

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
           "https://www.googleapis.com/auth/drive"]

RUN_LOG = []   # (ts, level, msg) — pushed to RUN_LOG tab

# ══════════════════════════════════════════════════════════════════════
# LOGGING / TELEGRAM  (same conventions as fortress_fetcher)
# ══════════════════════════════════════════════════════════════════════

def log(msg, level="INFO"):
    ts = datetime.now(IST).strftime("%H:%M:%S")
    print(f"[{ts}] [{level:4}] {msg}", flush=True)
    RUN_LOG.append((ts, level, str(msg)[:500]))

def log_tb(label, exc):
    log(f"{label}: {type(exc).__name__}: {exc}", "ERR")
    for line in traceback.format_exc().rstrip().splitlines():
        print(f"         {line}", flush=True)

def _actions_url():
    return f"https://github.com/{GH_REPO}/actions/runs/{GH_RUN_ID}" if GH_REPO and GH_RUN_ID else ""

def _esc(s):
    return _html.escape(str(s), quote=False)

def send_telegram(text):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log("Telegram not configured — skipping alert", "DBG")
        return
    for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": "true"},
                timeout=20)
            if r.status_code == 400 and "parse entities" in r.text:
                # never lose the digest to one bad character: resend as plain text
                plain = re.sub(r"<[^>]+>", "", chunk).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                  data={"chat_id": TELEGRAM_CHAT_ID, "text": plain,
                                        "disable_web_page_preview": "true"}, timeout=20)
                log("Telegram: HTML rejected, sent as plain text", "WARN")
            if r.status_code != 200:
                log(f"Telegram HTTP {r.status_code}: {r.text[:300]}", "WARN")
        except Exception as exc:
            log_tb("Telegram send failed", exc)

# ══════════════════════════════════════════════════════════════════════
# HTTP — browser-fingerprinted session with throttle + block detection
# ══════════════════════════════════════════════════════════════════════

_PROFILES = ["chrome124", "chrome120", "chrome116", "safari17_0", "edge101"]
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
_last_req = [0.0]

def _throttle():
    gap = MIN_GAP_SEC + random.uniform(0, 2.0)
    wait = _last_req[0] + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_req[0] = time.time()

def new_session():
    if HAS_CURL_CFFI:
        prof = random.choice(_PROFILES)
        try:
            s = cffi_requests.Session(impersonate=prof)
            s.headers.update({"Accept-Language": "en-IN,en;q=0.9"})
            log(f"Session: curl_cffi impersonate={prof}", "DBG")
            return s
        except Exception as exc:
            log(f"curl_cffi profile {prof} failed ({exc}) — falling back", "WARN")
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    })
    log("Session: plain requests (expect more Amazon blocks — install curl_cffi)", "WARN")
    return s

_BLOCK_MARKERS = ("Enter the characters you see below", "api-services-support@amazon.com",
                  "To discuss automated access to Amazon data", "/errors/validateCaptcha")

def fetch_html(sess_box, url, retries=3):
    """sess_box is a 1-item list so we can swap the session after a block."""
    for attempt in range(1, retries + 1):
        _throttle()
        try:
            r = sess_box[0].get(url, timeout=30)
            body = r.text or ""
            if r.status_code == 200 and not any(m in body for m in _BLOCK_MARKERS):
                return body
            reason = "captcha/bot-wall" if any(m in body for m in _BLOCK_MARKERS) else f"HTTP {r.status_code}"
            log(f"  attempt {attempt}/{retries} {url} → {reason}", "WARN")
        except Exception as exc:
            log(f"  attempt {attempt}/{retries} {url} → {type(exc).__name__}: {exc}", "WARN")
        time.sleep(5 * attempt + random.uniform(0, 3))
        sess_box[0] = new_session()          # rotate fingerprint
    return None

# ══════════════════════════════════════════════════════════════════════
# AMAZON.IN PARSER
#   Amazon reshuffles CSS class names often, so we key on stable-ish
#   anchors: #gridItemRoot, data-asin, /dp/<ASIN>, ".zg-bdg-text", "₹".
# ══════════════════════════════════════════════════════════════════════

_RX_ASIN    = re.compile(r"/dp/([A-Z0-9]{10})")
_RX_PRICE   = re.compile(r"₹\s?([\d,]+(?:\.\d+)?)")
_RX_RATING  = re.compile(r"([0-5](?:\.\d)?) out of 5 stars")
_RX_INT     = re.compile(r"[\d,]+")
_RX_MOVER   = re.compile(r"Sales rank:\s*([\d,]+)\s*\((?:previously|was)\s*(?:unranked|([\d,]+))\)", re.I)
_RX_PCT     = re.compile(r"([\d,]+)\s*%")

def norm_asin(a):
    """ISBN-style ASINs are all digits; a spreadsheet may have stripped leading zeros."""
    a = str(a).strip().lstrip("'")
    return a.zfill(10) if a.isdigit() and len(a) < 10 else a


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None

_RX_MRP = re.compile(r"M\.?R\.?P\.?\s*:?\s*₹\s?([\d,]+(?:\.\d+)?)", re.I)
_RX_RANGE = re.compile(r"₹\s?([\d,]+(?:\.\d+)?)\s*[-–]\s*₹\s?([\d,]+(?:\.\d+)?)")

def parse_prices(text):
    """→ (price, price_max, mrp). price = lowest shown selling price; price_max set when the
    card shows a variant range (₹X - ₹Y); mrp only when explicitly labelled M.R.P."""
    mrp = None
    m = _RX_MRP.search(text)
    if m:
        mrp = _num(m.group(1))
        text = text[:m.start()] + text[m.end():]
    rg = _RX_RANGE.search(text)
    if rg:
        lo, hi = _num(rg.group(1)), _num(rg.group(2))
        return min(lo, hi), max(lo, hi), mrp
    pm = [_num(x) for x in _RX_PRICE.findall(text)]
    pm = [x for x in pm if x]
    if not pm:
        return None, None, mrp
    if mrp is None and len(pm) >= 2 and max(pm) > min(pm):
        mrp = max(pm)          # unlabelled strike-through price; treated as MRP
    return min(pm), None, mrp

_AMAZON_EMPTY = re.compile(r"there are no [\w\s&'-]{2,40} available in this category", re.I)


def own_movers(hist, today):
    """Movers computed from OUR bestseller history (Amazon's Movers & Shakers can be empty):
    1-day rank gain % = (rank_prev - rank_now) / rank_now × 100, same formula Amazon uses and the
    same approximation the backtest uses. A product absent yesterday is treated as rank 101
    (conservative: it was at least outside the top 100). Only categories fetched on BOTH days are
    compared — otherwise a category that failed yesterday would make every product a fake mover."""
    bs = hist[hist["list"] == "bestsellers"]
    if bs.empty:
        return pd.Series(dtype=float)
    dates = sorted(d for d in bs["date"].unique() if d < today)
    if not dates or (pd.Timestamp(today) - pd.Timestamp(dates[-1])).days > 2:
        return pd.Series(dtype=float)
    prev = dates[-1]
    r = bs.groupby(["category_slug", "date", "asin"])["rank"].min()
    cats_prev = set(bs.loc[bs["date"] == prev, "category_slug"])
    cats_now = set(bs.loc[bs["date"] == today, "category_slug"])
    SWAPS.clear()
    titles = bs[bs["title"].map(_has_title)].groupby("asin")["title"].last() if "title" in bs else pd.Series(dtype=str)
    out = {}
    for cat in cats_prev & cats_now:
        now = r.loc[(cat, today)]
        before = r.loc[(cat, prev)]
        fam_prev = {}
        for a, rk in before.items():                 # best rank yesterday per product family
            k = family_key(titles.get(a, ""))
            if k:
                fam_prev[k] = min(fam_prev.get(k, 101), rk)
        for asin, rn in now.items():
            if asin in before.index:
                rp = before[asin]
            else:
                # new to the list: if a size/flavour sibling was listed yesterday, Amazon swapped the
                # displayed variant (6 kg → 1 kg) — inherit the family's rank, not "from #101"
                rp = fam_prev.get(family_key(titles.get(asin, "")), 101)
                if rp < 101:
                    SWAPS[asin] = int(rp)
            if rn > 0 and rp > rn:
                out[asin] = max(out.get(asin, 0), (rp - rn) / rn * 100)
    return pd.Series(out, dtype=float)


SWAPS = {}   # asin → inherited family rank (filled by own_movers; shown as mover_note)


def family_key(title, words=4):
    """Product family = first words of the title before any '|' or '(' and without sizes/numbers.
    'Purepet Adult Dry Cat Food 1kg | Ocean Fish' and '… 6kg …' share 'purepet adult dry cat'."""
    if not _has_title(title):
        return ""
    head = re.split(r"[|(\[,–-]", str(title))[0].lower()
    toks = [w for w in re.findall(r"[a-z]+", head) if len(w) > 1]
    return " ".join(toks[:words]) if len(toks) >= 3 else ""


def parse_recs_json(soup):
    """Amazon list pages embed the full ranked list as JSON in data-client-recs-list
    (only ~30 items are rendered as cards; Movers & Shakers may render none server-side).
    → ordered list of {asin, rank, rank_now, rank_prev, pct_gain}."""
    out, seen = [], set()
    for div in soup.select("[data-client-recs-list]"):
        try:
            items = json.loads(div["data-client-recs-list"])
        except (ValueError, KeyError, TypeError):
            continue
        for it in items if isinstance(items, list) else []:
            asin = str(it.get("id") or "")
            if not re.fullmatch(r"[A-Z0-9]{10}", asin) or asin in seen:
                continue
            seen.add(asin)
            md = it.get("metadataMap") or {}
            num = lambda k: _num(str(md.get(k, "")).replace("%", "").strip("#")) if md.get(k) not in (None, "") else None
            out.append({"asin": asin, "rank": int(num("render.zg.rank") or len(out) + 1),
                        "rank_now": num("render.zg.bsms.currentSalesRank"),
                        "rank_prev": num("render.zg.bsms.twentyFourHourOldSalesRank"),
                        "pct_gain": num("render.zg.bsms.percentageChange")})
    return out


def _dump_debug(html, slug, list_type, pg):
    """Save pages that parse to 0 items (max 8/run) → uploaded with the run artifact."""
    try:
        d = os.path.join(LOCAL_OUT_DIR, "debug")
        os.makedirs(d, exist_ok=True)
        if len(os.listdir(d)) < 8:
            with open(os.path.join(d, f"{slug}_{list_type}_p{pg}.html"), "w", encoding="utf-8") as fh:
                fh.write(html)
    except Exception as exc:
        log(f"  debug dump failed: {exc}", "DBG")


def parse_list_page(html, list_type, slug):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div#gridItemRoot") or soup.select("div.zg-grid-general-faceout") \
            or soup.select("li.zg-item-immersion")
    if not cards:   # generic fallback: any container carrying a product ASIN
        cards = [t for t in soup.select("[data-asin]") if re.fullmatch(r"[A-Z0-9]{10}", t.get("data-asin", ""))]
    rows = []
    for idx, card in enumerate(cards, 1):
        text = card.get_text(" ", strip=True)

        asin = None
        tag = card.find(attrs={"data-asin": re.compile(r"^[A-Z0-9]{10}$")})
        if tag:
            asin = tag["data-asin"]
        if not asin:
            for a in card.find_all("a", href=True):
                m = _RX_ASIN.search(a["href"])
                if m:
                    asin = m.group(1); break
        if not asin:
            log(f"  [{slug}/{list_type}] card {idx}: no ASIN, skipped", "DBG")
            continue

        badge = card.select_one(".zg-bdg-text")
        rank = int(_num(badge.get_text().strip("#")) or idx) if badge else idx

        title = ""
        img = card.find("img", alt=True)
        if img and len(img["alt"]) > 3:
            title = img["alt"].strip()
        if not title:
            for a in card.find_all("a", href=True):
                t = a.get_text(" ", strip=True)
                if len(t) > len(title) and "out of 5" not in t and "₹" not in t:
                    title = t

        price, price_max, mrp = parse_prices(text)

        rating, reviews = None, None
        rm = _RX_RATING.search(text)
        if rm:
            rating = float(rm.group(1))
        rev_a = card.find("a", href=re.compile(r"product-reviews"))
        if rev_a:
            rtxt = re.sub(r"[0-5](?:\.\d)? out of 5 stars", " ", rev_a.get_text(" ", strip=True))
            ints = _RX_INT.findall(rtxt)
            if ints:
                reviews = int(_num(ints[-1]))

        row = {"date": TODAY, "list": list_type, "category_slug": slug,
               "category": CATEGORIES.get(slug, slug), "rank": rank, "asin": asin,
               "title": title[:200], "price_inr": price, "price_max_inr": price_max,
               "mrp_inr": mrp, "rating": rating,
               "reviews": reviews, "url": f"https://www.amazon.in/dp/{asin}"}

        if list_type == "movers":
            mm = _RX_MOVER.search(text)
            row["rank_now"]  = int(_num(mm.group(1))) if mm else None
            row["rank_prev"] = int(_num(mm.group(2))) if mm and mm.group(2) else None
            pc = _RX_PCT.search(text)
            row["pct_gain"] = _num(pc.group(1)) if pc else None
            row["was_unranked"] = bool(mm and not mm.group(2))
        rows.append(row)

    # merge the embedded JSON list: fills ranks/mover stats and adds items with no rendered card
    recs = parse_recs_json(soup)
    if recs:
        by = {r["asin"]: r for r in rows}
        alt = {}
        for img in soup.find_all("img", alt=True):
            a = img.find_parent("a", href=True)
            m = _RX_ASIN.search(a["href"]) if a else None
            if m and len(img["alt"]) > 3:
                alt.setdefault(m.group(1), img["alt"].strip())
        for it in recs:
            r = by.get(it["asin"])
            if r is None:
                r = {"date": TODAY, "list": list_type, "category_slug": slug,
                     "category": CATEGORIES.get(slug, slug), "rank": it["rank"], "asin": it["asin"],
                     "title": alt.get(it["asin"], "")[:200], "price_inr": None, "price_max_inr": None,
                     "mrp_inr": None, "rating": None, "reviews": None,
                     "url": f"https://www.amazon.in/dp/{it['asin']}"}
                rows.append(r); by[it["asin"]] = r
            else:
                r["rank"] = it["rank"]
            if list_type == "movers":
                for k in ("rank_now", "rank_prev", "pct_gain"):
                    if r.get(k) is None and it.get(k) is not None:
                        r[k] = int(it[k]) if k != "pct_gain" else it[k]
                if "was_unranked" not in r or r["was_unranked"] is None:
                    r["was_unranked"] = it.get("rank_prev") is None and it.get("rank_now") is not None
        rows.sort(key=lambda r: r["rank"])
    return rows


def fetch_amazon_lists():
    sess_box = [new_session()]
    # warm-up: homepage sets session cookies, cuts captcha rate
    fetch_html(sess_box, "https://www.amazon.in/", retries=1)

    out = {k: [] for k in LISTS}
    stats = {"ok": 0, "blocked": 0, "empty": 0, "amazon_empty": 0}
    for slug in CATEGORIES:
        for list_type, tmpl in LISTS.items():
            base = tmpl.format(slug=slug)
            got = []
            for pg in (1, 2):                       # pg=2 holds ranks 51-100 on most lists
                url = base if pg == 1 else f"{base}?pg=2"
                html = fetch_html(sess_box, url)
                if html is None:
                    stats["blocked"] += 1
                    break
                rows = parse_list_page(html, list_type, slug)
                if not rows:
                    if _AMAZON_EMPTY.search(html):
                        # Amazon itself says the list is empty — not a parser problem
                        stats["amazon_empty"] = stats.get("amazon_empty", 0) + 1
                        if pg == 1:
                            log(f"  [{slug}/{list_type}] Amazon shows no items for this list today (Amazon-side)")
                        break
                    _dump_debug(html, slug, list_type, pg)
                    stats["empty"] += 1
                    if pg == 1:
                        log(f"  [{slug}/{list_type}] page parsed 0 items — layout change?", "WARN")
                    break
                got.extend(rows)
            if got:
                stats["ok"] += 1
            # de-dup (same ASIN on pg1/pg2 overlap)
            seen = set()
            for r in got:
                if r["asin"] not in seen:
                    seen.add(r["asin"]); out[list_type].append(r)
            log(f"  {slug:18} {list_type:13} {len(seen):3} items")
    log(f"Amazon: {stats['ok']} lists OK · {stats['blocked']} blocked · {stats['empty']} unparsed pages · "
        f"{stats.get('amazon_empty', 0)} lists Amazon shows as empty")
    return {k: pd.DataFrame(v) for k, v in out.items()}, stats

# ══════════════════════════════════════════════════════════════════════
# GOOGLE TRENDS INDIA
#   (a) Daily trending searches RSS — stable, no API key, but mostly
#       news/cricket/films, so we keep it as context only.
#   (b) pytrends rising related queries for SEED_KEYWORDS — the useful
#       shopping signal, but pytrends is unofficial and 429s often.
# ══════════════════════════════════════════════════════════════════════

def fetch_trending_rss():
    url = "https://trends.google.com/trending/rss?geo=IN"
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": _UAS[0]})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"ht": "https://trends.google.com/trending/rss"}
        rows = []
        for it in root.iter("item"):
            traffic = it.findtext("ht:approx_traffic", default="", namespaces=ns)
            rows.append({"date": TODAY, "source": "daily_trending",
                         "query": it.findtext("title", default=""),
                         "seed": "", "value": traffic.replace("+", "").replace(",", ""),
                         "note": it.findtext("ht:news_item/ht:news_item_title", default="", namespaces=ns)[:150]})
        log(f"Google Trends RSS: {len(rows)} trending searches")
        return rows
    except Exception as exc:
        log_tb("Google Trends RSS failed", exc)
        return []

# Rising queries about films, songs, definitions etc. are not shopping demand ("lunch box movie").
_NOISE_QUERY = re.compile(r"\b(movie|film|song|songs|lyrics|meaning|in hindi meaning|cast|trailer|web ?series|"
                          r"wikipedia|wiki|full form|kya hai|story|review in hindi|ott|episode|actor|actress|how to|what is|why|"
                          r"how much|kaise|recipe)\b", re.I)


def fetch_rising_queries():
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log("pytrends not installed — skipping rising queries", "WARN")
        return []
    rows = []
    try:
        pt = TrendReq(hl="en-IN", tz=330, timeout=(10, 25))   # no retries/backoff: pytrends' Retry call breaks on urllib3 ≥2
    except Exception as exc:
        log_tb("pytrends init failed", exc); return []
    for kw in SEED_KEYWORDS:
        try:
            pt.build_payload([kw], timeframe="today 3-m", geo="IN", cat=18)  # 18 = Shopping
            rq = pt.related_queries().get(kw, {}).get("rising")
            if rq is not None and not rq.empty:
                rq = rq[~rq["query"].astype(str).str.contains(_NOISE_QUERY)]
                for _, r in rq.head(10).iterrows():
                    rows.append({"date": TODAY, "source": "rising_query", "query": r["query"],
                                 "seed": kw, "value": str(r["value"]),  # % growth, or 'Breakout'
                                 "note": "Breakout (>5000%)" if r["value"] >= 5000 else ""})
            time.sleep(6 + random.uniform(0, 4))
        except Exception as exc:
            log(f"  pytrends '{kw}' failed: {type(exc).__name__}: {exc}", "WARN")
            time.sleep(20)
    log(f"Google Trends rising queries: {len(rows)} rows from {len(SEED_KEYWORDS)} seeds")
    return rows


# ══════════════════════════════════════════════════════════════════════
# PRODUCT PAGE DETAILS (top-N scored ASINs only — each is one extra request)
# ══════════════════════════════════════════════════════════════════════

_RX_BOUGHT  = re.compile(r"([\d.,]+)\s*([KkLl]?)\+?\s*bought in past month")
_RX_SELLERS = re.compile(r"(?:New\s*\((\d+)\)\s*from|(\d+)\s+(?:new\s+)?offers?\s+from|Other Sellers on Amazon[^\d]{0,40}(\d+))", re.I)
_RX_DISC    = re.compile(r"-\s?(\d{1,2})\s?%")
_RX_COUPON  = re.compile(r"Apply\s+(₹\s?[\d,]+|\d{1,2}%)\s+coupon", re.I)

def _bought_to_int(num, unit):
    v = _num(num) or 0
    return int(v * {"k": 1_000, "l": 100_000}.get(unit.lower(), 1))

def parse_product_page(html, asin):
    soup = BeautifulSoup(html, "html.parser")
    core = (soup.select_one("#corePriceDisplay_desktop_feature_div") or soup.select_one("#corePrice_feature_div")
            or soup.select_one("#apex_desktop") or soup)
    center = soup.select_one("#centerCol") or soup.select_one("#ppd") or soup
    ctext = center.get_text(" ", strip=True)
    ptext = core.get_text(" ", strip=True)
    out = {"date": TODAY, "asin": asin}
    off = core.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
    out["price_inr"] = _num(_RX_PRICE.search(off.get_text()).group(1)) if off and _RX_PRICE.search(off.get_text()) else \
        parse_prices(ptext)[0]
    mrp_tag = core.select_one("span.a-price.a-text-price span.a-offscreen")
    out["mrp_inr"] = _num(_RX_PRICE.search(mrp_tag.get_text()).group(1)) if mrp_tag and _RX_PRICE.search(mrp_tag.get_text()) else \
        parse_prices(ptext)[2]
    sp = core.select_one(".savingsPercentage")
    dm = _RX_DISC.search(sp.get_text()) if sp else None
    out["discount_pct"] = float(dm.group(1)) if dm else (
        round((1 - out["price_inr"] / out["mrp_inr"]) * 100, 1)
        if out.get("price_inr") and out.get("mrp_inr") and out["mrp_inr"] > out["price_inr"] else None)
    bm = _RX_BOUGHT.search(soup.get_text(" ", strip=True))
    out["bought_past_month_min"] = _bought_to_int(bm.group(1), bm.group(2)) if bm else None
    full = soup.get_text(" ", strip=True)
    sm = _RX_SELLERS.search(full)        # "New (10) from" sits in the RIGHT column, not #centerCol
    has_merchant = bool(soup.find(id="merchantInfoFeature_feature_div") or soup.find(id="sellerProfileTriggerId")
                        or re.search(r"\bSold by\b", full))
    has_buybox = has_merchant or bool(re.search(r"\b(Buy Now|Add to Cart)\b", ctext)) and bool(out.get("price_inr"))
    if sm:
        out["seller_offers"], out["seller_source"] = int(next(g for g in sm.groups() if g)), "offer count"
    elif has_merchant and not re.search(r"Other sellers on Amazon", full, re.I):
        out["seller_offers"], out["seller_source"] = 1, "single 'Sold by', no other offers"
    else:
        out["seller_offers"] = None
        out["seller_source"] = "page without buy box (Amazon served a reduced page)" if not has_buybox else "not found"
    cm = _RX_COUPON.search(ctext)
    out["coupon"] = cm.group(1).replace(" ", "") if cm else ""
    out["limited_deal"] = "Limited time deal" in ctext
    out["bank_offer"] = "Bank Offer" in ctext
    low = ctext.lower()
    out["fulfilled_by_amazon"] = ("fulfilled" in low and "amazon" in low) or "ships from amazon" in low
    out["cod_available"] = "cash on delivery" in low or "pay on delivery" in low
    return out

def fetch_details(asins):
    if not asins:
        return pd.DataFrame()
    sess_box, rows, blocked, streak, skipped = [new_session()], [], 0, 0, 0
    for i, a in enumerate(asins):
        if streak >= DETAIL_BLOCK_STREAK:
            # Amazon is captcha-walling product pages: stop asking this run. Pushing on deepens the
            # block and risks the list pages (the core data) being blocked too.
            skipped = len(asins) - i
            log(f"Product details: {streak} blocked in a row — stopping for this run ({skipped} skipped)", "WARN")
            break
        html = fetch_html(sess_box, f"https://www.amazon.in/dp/{a}", retries=2)
        if html is None:
            blocked += 1; streak += 1
            continue
        streak = 0
        try:
            d = parse_product_page(html, a)
            rows.append(d)
            if d.get("seller_offers") is None and d.get("seller_source") == "not found":
                _dump_debug(html, f"product_{a}", "sellers_unparsed", 0)
            time.sleep(PRODUCT_EXTRA_GAP_SEC + random.uniform(0, 3))   # product pages trip captchas faster
        except Exception as exc:
            log_tb(f"parse product page {a}", exc)
    log(f"Product details: {len(rows)}/{len(asins)} parsed · {blocked} blocked · {skipped} skipped (circuit breaker)")
    DETAIL_STATS.update(parsed=len(rows), blocked=blocked, skipped=skipped)
    return pd.DataFrame(rows)

# ══════════════════════════════════════════════════════════════════════
# GOOGLE TRENDS BY STATE — search INTEREST, not sales. Values are 0-100 relative
# to the top state *within one query*, i.e. share of that state's searches, not
# volume. We compare ranks across windows, not raw values.
# ══════════════════════════════════════════════════════════════════════

def fetch_state_interest():
    if not BR_STATE_TRENDS:
        return []
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log("pytrends not installed — skipping state interest", "WARN"); return []
    try:
        pt = TrendReq(hl="en-IN", tz=330, timeout=(10, 25))   # no retries/backoff: pytrends' Retry call breaks on urllib3 ≥2
    except Exception as exc:
        log_tb("pytrends init failed", exc); return []
    rows = []
    for kw in SEED_KEYWORDS:
        frames = {}
        for label, tf in (("3m", "today 3-m"), ("1m", "today 1-m")):
            try:
                pt.build_payload([kw], timeframe=tf, geo="IN")   # all categories: Shopping-only is too sparse per state
                df = pt.interest_by_region(resolution="REGION", inc_low_vol=True, inc_geo_code=True)
                frames[label] = df
            except Exception as exc:
                log(f"  state interest '{kw}' {tf} failed: {type(exc).__name__}: {exc}", "WARN")
                time.sleep(20)
            time.sleep(6 + random.uniform(0, 4))
        if "3m" not in frames:
            continue
        a = frames["3m"].rename(columns={kw: "interest_3m"})
        if "1m" in frames:
            a = a.join(frames["1m"][[kw]].rename(columns={kw: "interest_1m"}), how="left")
        else:
            a["interest_1m"] = None
        a = a.reset_index().rename(columns={"geoName": "state", "geoCode": "geo_code"})
        a = a[a["interest_3m"].fillna(0) > 0]
        a["rank_3m"] = a["interest_3m"].rank(ascending=False, method="min")
        a["rank_1m"] = pd.to_numeric(a["interest_1m"], errors="coerce").rank(ascending=False, method="min")
        a["rank_change"] = a["rank_3m"] - a["rank_1m"]          # +ve = state rising recently
        a["low_signal"] = a["interest_3m"] < 10
        for _, r in a.iterrows():
            rows.append({"date": TODAY, "keyword": kw, "state": r["state"],
                         "geo_code": r.get("geo_code", ""), "interest_3m": r["interest_3m"],
                         "interest_1m": r["interest_1m"], "rank_3m": int(r["rank_3m"]),
                         "rank_1m": int(r["rank_1m"]) if pd.notna(r["rank_1m"]) else None,
                         "rank_change": r["rank_change"] if pd.notna(r["rank_change"]) else None,
                         "low_signal": bool(r["low_signal"])})
    log(f"Google Trends by state: {len(rows)} rows across {len(SEED_KEYWORDS)} keywords")
    return rows

def state_summary(st):
    """Per state: how many seed keywords it ranks top-5 for, and how many it is rising on."""
    if st.empty:
        return pd.DataFrame()
    st = st[~st["low_signal"]]
    g = st.groupby("state").agg(
        keywords=("keyword", "nunique"),
        top5_keywords=("rank_3m", lambda r: int((r <= 5).sum())),
        rising_keywords=("rank_change", lambda r: int((pd.to_numeric(r, errors="coerce") >= 3).sum())),
        median_interest_3m=("interest_3m", "median"),
    ).reset_index().sort_values(["top5_keywords", "rising_keywords"], ascending=False)
    g["note"] = "search share, not sales; small states can top share on low volume"
    return g

# ══════════════════════════════════════════════════════════════════════
# TREND ANALYSIS
# ══════════════════════════════════════════════════════════════════════

HIST_COLS = ["date", "list", "category_slug", "asin", "rank", "price_inr", "rating", "reviews", "title"]

def update_history(hist, today_frames):
    parts = [f[HIST_COLS] for f in today_frames.values() if not f.empty]
    if parts:
        today = pd.concat(parts, ignore_index=True)
        hist = hist[hist["date"] != TODAY] if not hist.empty else hist   # idempotent re-runs
        hist = pd.concat([hist, today], ignore_index=True) if not hist.empty else today
    if hist.empty:
        return hist
    cutoff = (NOW_IST - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    hist = hist[hist["date"] >= cutoff].copy()
    for c in ("rank", "price_inr", "rating", "reviews"):
        hist[c] = pd.to_numeric(hist[c], errors="coerce")
    return fill_titles(hist)


def _has_title(t):
    t = str(t).strip()
    return len(t) > 3 and t.lower() not in ("nan", "none")


ARCHIVE_DIR = os.path.join(LOCAL_OUT_DIR, "archive")


def archive_today(name, df):
    """Permanent archive: one gzipped CSV per day per dataset (output/archive/YYYY-MM-DD/name.csv.gz).
    The workflow commits these to the `data-archive` branch, so nothing is lost after the 60-day
    rolling window in the Sheet. ~40 KB/day."""
    try:
        if df is None or len(df) == 0:
            return
        d = os.path.join(ARCHIVE_DIR, TODAY)
        os.makedirs(d, exist_ok=True)
        df.to_csv(os.path.join(d, f"{name}.csv.gz"), index=False, compression="gzip")
    except Exception as exc:
        log(f"archive {name} failed: {exc}", "WARN")


def fill_titles(hist):
    """~40% of list items (ranks 31–50, 81–100) come only from Amazon's embedded JSON, without a title.
    Fill them from any other day/list where the same ASIN appeared with a title (latest wins)."""
    if hist.empty or "title" not in hist:
        return hist
    ok = hist["title"].map(_has_title)
    known = hist[ok].sort_values("date").groupby("asin")["title"].last()
    miss = ~ok & hist["asin"].isin(known.index)
    hist.loc[miss, "title"] = hist.loc[miss, "asin"].map(known)
    return hist

def compute_signals(hist, frames):
    """One row per ASIN with a composite trend score.

    Components (each roughly 0-1, then weighted):
      momentum    — best-seller rank improvement vs ~7 days ago
      mover       — appeared on Movers & Shakers (log of % gain)
      new_launch  — on New Releases AND Best Sellers (launch that's selling)
      review_vel  — reviews/day growth (closest public proxy for unit sales)
      persistence — days on any list in last 14 (filters one-day spikes)
      breadth     — present on >1 list type
    """
    if hist.empty:
        return pd.DataFrame()
    bs = hist[hist["list"] == "bestsellers"]
    recent = hist[hist["date"] >= (NOW_IST - timedelta(days=14)).strftime("%Y-%m-%d")]
    dates = sorted(hist["date"].unique())
    ndays = len(dates)

    latest = (hist.sort_values("date").groupby("asin").tail(1)
                  .set_index("asin")[["title", "category_slug", "price_inr", "rating"]])
    out = latest.copy()
    out["category"] = out["category_slug"].map(lambda s: CATEGORIES.get(s, s))

    # rank today vs ~7d ago on bestsellers (best rank per day per asin)
    daily_rank = bs.groupby(["asin", "date"])["rank"].min().unstack()
    if not daily_rank.empty:
        today_col = daily_rank.columns.max()
        past_dates = [d for d in daily_rank.columns
                      if d <= (NOW_IST - timedelta(days=6)).strftime("%Y-%m-%d")]
        ref_col = past_dates[-1] if past_dates else daily_rank.columns.min()
        out["bs_rank_today"] = daily_rank[today_col]
        out["bs_rank_ref"]   = daily_rank[ref_col]
        out["bs_ref_date"]   = ref_col
    else:
        out["bs_rank_today"] = out["bs_rank_ref"] = None
        out["bs_ref_date"] = ""
    rt = pd.to_numeric(out["bs_rank_today"], errors="coerce")
    rr = pd.to_numeric(out["bs_rank_ref"], errors="coerce")
    # entered top-100 since ref date counts as coming from rank 101
    rr_eff = rr.fillna(101).where(rt.notna())
    out["rank_change"] = rr_eff - rt                      # +ve = climbing
    out["momentum"] = (out["rank_change"] / 100).clip(lower=0, upper=1).fillna(0)

    mv = frames.get("movers", pd.DataFrame())
    if not mv.empty:
        mvi = mv.groupby("asin").agg(pct_gain=("pct_gain", "max"), was_unranked=("was_unranked", "max"))
        out = out.join(mvi, how="left")
    else:
        out["pct_gain"], out["was_unranked"] = None, False
    out["mover_source"] = pd.to_numeric(out["pct_gain"], errors="coerce").notna().map({True: "amazon", False: "none"})
    own = own_movers(hist, TODAY)
    fill = out["mover_source"].eq("none") & out.index.isin(own.index)
    out.loc[fill, "pct_gain"] = own.reindex(out.index)[fill]
    out.loc[fill, "mover_source"] = "own_1d"
    out["mover_note"] = [f"variant swap — family was #{SWAPS[a]} yesterday" if a in SWAPS else "" for a in out.index]
    pg = pd.to_numeric(out["pct_gain"], errors="coerce")
    out["mover"] = (pg.apply(lambda v: math.log10(v + 1) / 4 if pd.notna(v) else 0)).clip(0, 1)
    out.loc[out["was_unranked"].fillna(False).astype(bool), "mover"] = 1.0

    today_lists = hist[hist["date"] == TODAY].groupby("asin")["list"].apply(lambda s: sorted(set(s)))
    out["lists_today"] = today_lists.reindex(out.index).apply(
        lambda v: ",".join(v) if isinstance(v, list) else "")
    out["new_launch"] = out["lists_today"].apply(
        lambda s: 1.0 if "new_releases" in s and "bestsellers" in s else (0.4 if "new_releases" in s else 0))
    out["breadth"] = out["lists_today"].apply(lambda s: (len(s.split(",")) - 1) / 2 if s else 0)

    # review velocity: (reviews_latest - reviews_earliest) / days, normalised by log
    rv = hist.dropna(subset=["reviews"]).sort_values("date").groupby("asin").agg(
        r0=("reviews", "first"), r1=("reviews", "last"), d0=("date", "first"), d1=("date", "last"))
    if not rv.empty:
        span = (pd.to_datetime(rv["d1"]) - pd.to_datetime(rv["d0"])).dt.days.astype(float)
        span = span.where(span > 0)
        r0 = rv["r0"].astype(float).where(rv["r0"].astype(float) > 0)
        rv["reviews_per_day"] = (rv["r1"].astype(float) - rv["r0"].astype(float)) / span
        rv["review_growth_pct"] = (rv["r1"].astype(float) - rv["r0"].astype(float)) / r0 * 100
        out = out.join(rv[["reviews_per_day", "review_growth_pct"]], how="left")
        out["reviews"] = rv["r1"].reindex(out.index)
    else:
        out["reviews_per_day"] = out["review_growth_pct"] = out["reviews"] = None
    rpd = pd.to_numeric(out["reviews_per_day"], errors="coerce").clip(lower=0)
    out["review_vel"] = rpd.apply(lambda v: min(math.log10(v + 1) / 2.5, 1) if pd.notna(v) else 0)

    days_on = recent.groupby("asin")["date"].nunique()
    out["days_on_list_14d"] = days_on.reindex(out.index).fillna(0).astype(int)
    out["first_seen"] = hist.groupby("asin")["date"].min().reindex(out.index)
    denom = max(1, min(14, ndays))
    out["persistence"] = (out["days_on_list_14d"] / denom).clip(0, 1)

    # price change vs max of the prior 7 days (bestseller list price). A climb that
    # coincides with a big price cut is often bought demand that reverts when the cut ends.
    bp = bs.dropna(subset=["price_inr"]).copy()
    if not bp.empty:
        wk_ago = (pd.Timestamp(TODAY) - timedelta(days=DISCOUNT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        prior = bp[(bp["date"] >= wk_ago) & (bp["date"] < TODAY)].groupby("asin")["price_inr"].max()
        now_p = bp[bp["date"] == TODAY].groupby("asin")["price_inr"].min()
        chg = ((now_p - prior) / prior * 100).round(1)
        out["price_change_7d_pct"] = chg.reindex(out.index)
    else:
        out["price_change_7d_pct"] = None
    # DISCOUNT RULE (discount_rule.py, shared with the backtest): sustained >= 48h across
    # >= 2 daily snapshots → penalise. One-day dips (hijacker OR lightning deal) → label only.
    t_end = pd.Timestamp(TODAY) + timedelta(days=1) - timedelta(seconds=1)
    status = {}
    if not bp.empty:
        for asin, g in bp.groupby("asin"):
            ser = g.groupby("date")["price_inr"].min()
            ser.index = pd.to_datetime(ser.index) + timedelta(hours=12)   # mid-day snapshot time
            status[asin] = discount_status(ser, t_end, PRICE_DROP_PCT, DISCOUNT_LOOKBACK_DAYS,
                                           DISCOUNT_SUSTAIN_HOURS, fresh_hours=DISCOUNT_FRESH_HOURS)["status"]
    out["discount_status"] = pd.Series(status).reindex(out.index).fillna("no_data")
    climbing = (out["momentum"] > 0) | (out["mover"] > 0)
    out["discount_driven"] = (out["discount_status"] == "sustained") & climbing
    out["short_dip"] = (out["discount_status"] == "dip_unconfirmed") & climbing

    W = {"momentum": 0.25, "mover": 0.20, "review_vel": 0.20,
         "new_launch": 0.15, "persistence": 0.12, "breadth": 0.08}
    out["trend_score_unpenalized"] = sum(out[k].astype(float) * w for k, w in W.items()) * 100
    # DISCOUNT PENALTY — identical rule to backtest/config.py (checked by test_fetcher.py):
    # rank velocity bought with a price cut is not an organic trend → halve momentum & mover.
    pen = out["discount_driven"].fillna(False).astype(bool)
    out.loc[pen, "momentum"] = out.loc[pen, "momentum"] * DISCOUNT_PENALTY
    out.loc[pen, "mover"] = out.loc[pen, "mover"] * DISCOUNT_PENALTY
    out["trend_score"] = sum(out[k].astype(float) * w for k, w in W.items()) * 100
    # only score items seen today — stale items can't be "upcoming"
    out = out[out["lists_today"] != ""]

    def label(r):
        if r["discount_driven"]:                        return "💸 Discount-driven (verify)"
        if r["short_dip"]:                              return "⏸ Quarantined — price dip <48h (hijacker, lightning deal or new flash sale); resolves within 2 days"
        if r["new_launch"] >= 1 and r["mover"] > 0.3: return "🚀 Breakout launch"
        if r["mover"] >= 0.75:                         return "⚡ Sudden spike (verify)"
        if r["momentum"] > 0.3 and r["persistence"] > 0.5: return "📈 Sustained climber"
        if r["review_vel"] > 0.5:                      return "🛒 High volume seller"
        if r["new_launch"] > 0:                        return "🆕 New release"
        return "• Watch"
    out["signal"] = out.apply(label, axis=1)
    # QUARANTINE (mirrors backtest): an unconfirmed dip is withheld from recommendations. The daily
    # run re-evaluates it automatically: price recovers → organic score released; stays down for
    # 3 snapshots (48h) → penalty applied. Never assume innocence on the day it matters.
    out["actionable"] = ~out["short_dip"].fillna(False).astype(bool)
    out["confidence"] = "low (<7 days history)" if ndays < 7 else ("medium" if ndays < 21 else "high")

    cols = ["trend_score", "signal", "confidence", "category", "category_slug", "title", "price_inr", "rating",
            "reviews", "reviews_per_day", "review_growth_pct", "bs_rank_today", "bs_rank_ref",
            "bs_ref_date", "rank_change", "price_change_7d_pct", "discount_status", "discount_driven", "short_dip", "actionable",
            "trend_score_unpenalized", "pct_gain", "mover_source", "mover_note", "lists_today", "days_on_list_14d",
            "first_seen", "momentum", "mover", "review_vel", "new_launch", "persistence", "breadth"]
    out = out.reset_index().rename(columns={"index": "asin"})
    out["url"] = "https://www.amazon.in/dp/" + out["asin"]
    out = out[["asin"] + cols + ["url"]].sort_values("trend_score", ascending=False)
    for c in ("trend_score", "trend_score_unpenalized", "reviews_per_day", "review_growth_pct", "momentum", "mover",
              "review_vel", "new_launch", "persistence", "breadth"):
        out[c] = pd.to_numeric(out[c], errors="coerce").round(2)
    return out

def category_heat(signals):
    """Which categories are heating up — aggregates signals per category."""
    if signals.empty:
        return pd.DataFrame()
    g = signals.groupby("category").agg(
        items=("asin", "count"),
        avg_score=("trend_score", "mean"),
        top10_score=("trend_score", lambda s: s.nlargest(10).mean()),
        movers=("mover", lambda s: int((s > 0).sum())),
        new_launches_selling=("new_launch", lambda s: int((s >= 1).sum())),
        median_price=("price_inr", "median"),
    ).round(2).sort_values("top10_score", ascending=False).reset_index()
    return g

# ══════════════════════════════════════════════════════════════════════
# STORAGE — Google Sheets (same as fortress) or local CSV fallback
# ══════════════════════════════════════════════════════════════════════

SHEET_PROBLEM = []   # filled when Google Sheets is configured but unusable → run continues locally


def _sheet_fix_hint(exc, email):
    msg = str(exc)
    sid = f"{GOOGLE_SHEET_ID[:6]}…{GOOGLE_SHEET_ID[-4:]}" if len(GOOGLE_SHEET_ID) > 12 else GOOGLE_SHEET_ID
    if "403" in msg or "permission" in msg.lower() or isinstance(exc, PermissionError):
        return (f"Google Sheet {sid} refused access (403). Share the sheet with {email} as Editor, and check "
                f"GOOGLE_SHEET_ID is THIS sheet's ID (the part between /d/ and /edit in its URL).")
    if "404" in msg or "not found" in msg.lower():
        return f"Google Sheet {sid} not found (404). GOOGLE_SHEET_ID is wrong or the sheet was deleted."
    if "has not been used" in msg or "disabled" in msg.lower():
        return "Google Sheets API (and Drive API) must be enabled in the service account's Cloud project."
    return f"Google Sheets error: {type(exc).__name__}: {msg[:200]}"


class Store:
    def __init__(self):
        self.wb = None
        if GOOGLE_CREDS_JSON and GOOGLE_SHEET_ID:
            email = "(unknown — check client_email in GOOGLE_CREDS_JSON)"
            try:
                import gspread
                from google.oauth2.service_account import Credentials
                info = json.loads(GOOGLE_CREDS_JSON)
                email = info.get("client_email", email)
                creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
                self.wb = gspread.authorize(creds).open_by_key(GOOGLE_SHEET_ID)
                log(f"Storage: Google Sheets (as {email})")
                return
            except json.JSONDecodeError as exc:
                hint = f"GOOGLE_CREDS_JSON is not valid JSON ({exc}). Paste the whole credentials.json file."
            except Exception as exc:
                hint = _sheet_fix_hint(exc, email)
            SHEET_PROBLEM.append(hint)
            log(f"SHEETS UNAVAILABLE → {hint}", "ERR")
            log("Continuing with local files so the Amazon fetch still runs; they are uploaded as a "
                "workflow artifact named 'bazaar-radar-output'.", "WARN")
        os.makedirs(LOCAL_OUT_DIR, exist_ok=True)
        if not SHEET_PROBLEM:
            log(f"Storage: local files in ./{LOCAL_OUT_DIR} (Google secrets not set)")

    def read(self, tab):
        if self.wb is None:
            p = os.path.join(LOCAL_OUT_DIR, f"{tab}.csv")
            return pd.read_csv(p, dtype=str) if os.path.exists(p) else pd.DataFrame()
        import gspread
        try:
            vals = self.wb.worksheet(tab).get_all_values()
        except gspread.WorksheetNotFound:
            return pd.DataFrame()
        df = pd.DataFrame(vals[1:], columns=vals[0]) if len(vals) > 1 else pd.DataFrame()
        if "asin" in df:
            df["asin"] = df["asin"].map(norm_asin)
        return df

    def update_calculator(self, signals_records, all_records=None):
        if self.wb is None:
            path = os.path.join(LOCAL_OUT_DIR, "CALCULATOR.xlsx")
            return calculator.update_xlsx(path, signals_records, today=TODAY, refresh=all_records)
        return calculator.update_gsheet(self.wb, signals_records, today=TODAY, refresh=all_records)

    def write(self, tab, df):
        if df is None or df.empty:
            log(f"  {tab}: nothing to write", "WARN"); return 0
        if self.wb is None:
            df.to_csv(os.path.join(LOCAL_OUT_DIR, f"{tab}.csv"), index=False)
            return len(df)
        import gspread
        try:
            ws = self.wb.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = self.wb.add_worksheet(title=tab, rows=len(df) + 10, cols=len(df.columns) + 2)
        ws.clear()
        values = [df.columns.tolist()] + df.astype(object).where(df.notna(), "").astype(str).values.tolist()
        ws.update(range_name="A1", values=values, value_input_option="RAW")   # RAW: keeps 0008640378 as text
        return len(df)

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def enrich_signals(signals, det, dh):
    """Join product-page fields; add 'bought past month' growth from DETAIL_HISTORY."""
    keep = ["asin", "mrp_inr", "discount_pct", "bought_past_month_min", "seller_offers", "seller_source",
            "coupon", "limited_deal", "bank_offer", "fulfilled_by_amazon", "cod_available"]
    out = signals.drop(columns=[c for c in keep[1:] if c in signals.columns]).merge(
        det[[c for c in keep if c in det.columns]], on="asin", how="left")
    h = dh.copy()
    h["bought_past_month_min"] = pd.to_numeric(h.get("bought_past_month_min"), errors="coerce")
    h = h.dropna(subset=["bought_past_month_min"]).sort_values("date")
    if not h.empty:
        g = h.groupby("asin")["bought_past_month_min"].agg(["first", "last"])
        out = out.merge((g["last"] - g["first"]).rename("bought_growth").reset_index(), on="asin", how="left")
    # Saturated: many sellers on one listing → price war risk for a new entrant
    out["crowded_listing"] = pd.to_numeric(out.get("seller_offers"), errors="coerce") >= 10
    return out

def main_states():
    """Weekly job: state-level search interest only. Isolated so its failures and
    rate limits never touch the daily Amazon run."""
    log(f"📍 BAZAAR RADAR — state interest (weekly) · {TODAY}")
    store = Store()
    if SHEET_PROBLEM:
        send_telegram(f"📍 Bazaar Radar states — ⚠️ {_esc(SHEET_PROBLEM[0])}")
    st = pd.DataFrame(fetch_state_interest())
    if st.empty:
        log("No state data (Google rate-limited or pytrends missing)", "WARN")
        send_telegram(f"📍 Bazaar Radar states — no data this week. {_actions_url()}")
        return
    store.write("GTRENDS_STATES", st)
    summ = state_summary(st)
    store.write("STATE_SUMMARY", summ)
    lines = [f"📍 <b>States leading search interest</b> — week of {TODAY}",
             "<i>search share, not sales; use for ad geo-targeting, not inventory</i>"]
    for _, r in summ.head(8).iterrows():
        lines.append(f"• {_esc(r['state'])} — top-5 for {r['top5_keywords']}/{r['keywords']} keywords, "
                     f"rising on {r['rising_keywords']}")
    send_telegram("\n".join(lines))

def main_affiliate():
    """Weekly: choose picks, record same-category random baselines, draft the channel post."""
    global TELEGRAM_CHAT_ID
    log(f"🔍 BAZAAR RADAR — affiliate picks · {TODAY}")
    store = Store()
    hist = store.read(SHEET_HISTORY)
    n_days = hist["date"].nunique() if not hist.empty else 0
    if n_days < BR_AFF_MIN_DAYS:
        msg = f"🔍 Affiliate picks: {n_days}/{BR_AFF_MIN_DAYS} days of history — no picks yet (day-1 scores are ties)."
        log(msg); send_telegram(_esc(msg)); return
    for c in ("rank",):
        hist[c] = pd.to_numeric(hist[c], errors="coerce")
    sig = store.read("TREND_SIGNALS")
    last = hist["date"].max()
    chosen = picks.select_picks(sig, n=BR_AFF_TOP_N)
    if chosen.empty:
        send_telegram("🔍 Affiliate picks: no eligible products this week."); return
    trk = store.read("PICKS")
    if not trk.empty and (trk["pick_date"] == last).any():
        log("Picks already recorded for this date — not duplicating"); return
    rows = picks.make_rows(chosen, hist, last)
    trk = pd.concat([trk, rows.astype(str)], ignore_index=True) if not trk.empty else rows
    store.write("PICKS", trk)
    sc = picks.scorecard(picks.update_tracking(trk, hist)) if not trk.empty else pd.DataFrame()
    line = picks.verdict(sc) if not sc.empty else ""
    msgs = picks.post_messages(chosen, hist, AFFILIATE_TAG, last, cat_names=CATEGORIES,
                               week_label=f"week of {last}")
    store.write("AFFILIATE_POST", pd.DataFrame({"message": msgs}))
    public_ok = bool(AFFILIATE_CHAT_ID) and AFFILIATE_CHAT_ID.strip() != str(TELEGRAM_CHAT_ID).strip()
    if AFFILIATE_CHAT_ID and not public_ok:
        send_telegram("⚠️ AFFILIATE_CHAT_ID is the SAME as TELEGRAM_CHAT_ID. Nothing was posted publicly — "
                      "the admin chat receives errors, margins and sourcing data, so the public channel must be a "
                      "different chat. Showing a draft instead.")
    if public_ok:
        own, TELEGRAM_CHAT_ID = TELEGRAM_CHAT_ID, AFFILIATE_CHAT_ID
        try:
            for m in msgs:                      # one message per product, like deal channels
                send_telegram(m)
                time.sleep(AFFILIATE_POST_GAP_SEC)   # stay well under Telegram's channel rate limit
        finally:
            TELEGRAM_CHAT_ID = own
        send_telegram(f"🔍 Posted {len(msgs) - 1} picks to the channel. {_esc(line)}")
    else:
        send_telegram("🔍 <b>DRAFT</b> — set AFFILIATE_CHAT_ID to auto-post. Preview of the channel messages:")
        for m in msgs:
            send_telegram(m)
        if line:
            send_telegram(_esc(line))
    # ── share kit for X + WhatsApp (admin chat only) and optional paid X auto-posting
    kit = picks.share_kit(chosen, hist, AFFILIATE_TAG, last, cat_names=CATEGORIES)
    store.write("SHARE_KIT", pd.DataFrame(kit))
    x_auto = all(X_CREDS.values())
    send_telegram("📣 <b>Share kit</b> — tap to open X / WhatsApp with the post pre-written, then pick your "
                  "community or channel." + (" (X is auto-posted this week.)" if x_auto else ""))
    for i, k in enumerate(kit, 1):
        send_telegram(picks.kit_message(i, k))
    if x_auto:
        res = picks.post_to_x(kit, X_CREDS, X_MAX_POSTS_PER_RUN)
        ok_n = sum(1 for _, t in res if t and not t.startswith("ERROR"))
        errs = [t for _, t in res if t.startswith("ERROR")]
        send_telegram(f"𝕏 Auto-posted {ok_n}/{len(res)} (cap {X_MAX_POSTS_PER_RUN}/week)."
                      + (f"\n⚠️ {_esc(errs[0])}" if errs else ""))
    if not AFFILIATE_TAG:
        send_telegram("⚠️ AFFILIATE_TAG not set — links carry no Associates tag, so they earn nothing.")


def send_telegram_document(path, caption=""):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                              data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                              files={"document": (os.path.basename(path), fh, "text/html")}, timeout=60)
        if r.status_code != 200:
            log(f"Telegram document HTTP {r.status_code}: {r.text[:200]}", "WARN")
    except Exception as exc:
        log_tb("Telegram document", exc)


def main_brief():
    """Weekly B2B sample brief → HTML file to the ADMIN chat (you forward it to sellers yourself)."""
    log(f"📰 BAZAAR RADAR — weekly brief · {TODAY}")
    store = Store()
    hist = store.read(SHEET_HISTORY)
    if hist.empty or hist["date"].nunique() < BR_AFF_MIN_DAYS:
        send_telegram(f"📰 Brief: {hist['date'].nunique() if not hist.empty else 0}/{BR_AFF_MIN_DAYS} days of history — skipped.")
        return
    last = hist["date"].max()
    sig, heat, trk = store.read("TREND_SIGNALS"), store.read("CATEGORY_HEAT"), store.read("PICKS")
    line, weeks = "", 0
    if not trk.empty:
        trk = picks.update_tracking(trk, hist)
        line = picks.verdict(picks.scorecard(trk))
        weeks = trk["pick_date"].nunique()
    page, text = brief.build(sig, heat, line, hist, last, picks, CATEGORIES, weeks)
    os.makedirs(LOCAL_OUT_DIR, exist_ok=True)
    path = os.path.join(LOCAL_OUT_DIR, f"trend_brief_{last}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    send_telegram_document(path, "📰 Weekly brief (sample for sellers). Forward it to 20–30 sellers and "
                                 "note who replies 'yes' and at what price — that is the demand test.")
    log(f"Brief written → {path}")


def main_sourcing():
    """After the daily run: IndiaMART price discovery for calculator rows still waiting for a quote.
    Fills INDICATIVE columns only — never your quote cell."""
    log(f"🏭 BAZAAR RADAR — IndiaMART sourcing estimates · {TODAY}")
    store = Store()
    rows = (calculator.read_xlsx(os.path.join(LOCAL_OUT_DIR, "CALCULATOR.xlsx")) if store.wb is None
            else calculator.read_gsheet(store.wb))
    hist = store.read("SOURCING_HISTORY")
    recent = set()
    if not hist.empty and "date" in hist:
        cutoff = (pd.Timestamp(TODAY) - timedelta(days=BR_SOURCING_RECHECK)).strftime("%Y-%m-%d")
        recent = set(hist.loc[hist["date"] >= cutoff, "asin"])
    import workbook_tabs
    quoted = workbook_tabs.quoted_asins(store.wb, os.path.join(LOCAL_OUT_DIR, "CALCULATOR.xlsx"))
    todo = [r for r in rows if r.get("asin") and not calculator.has_quote(r, quoted) and r["asin"] not in recent]
    # seasonal and quarantined rows last — least likely to be bought
    todo.sort(key=lambda r: (str(r.get("seasonal", "")).startswith("SEASONAL"),
                             "Quarantined" in str(r.get("signal", "")), -calculator._num(r.get("trend_score"))))
    todo = todo[:BR_SOURCING_TOP_N]
    log(f"Calculator rows: {len(rows)} · waiting for a quote & not checked in {BR_SOURCING_RECHECK}d: {len(todo)}")
    if not todo:
        send_telegram(f"🏭 Bazaar Radar sourcing — nothing to check today ({len(rows)} calculator rows).")
        return
    sess_box, details, updates, summary, blocked = [new_session()], [], [], [], 0
    for r in todo:
        q = sourcing.make_query(r.get("product", ""), r.get("search_words", ""))
        if not q:
            continue
        url = sourcing.search_url(q)
        html = fetch_html(sess_box, url, retries=2)
        if html is None:
            blocked += 1
            continue
        listings = sourcing.parse_search(html)
        if not listings:
            _dump_debug(html, f"indiamart_{r['asin']}", "search", 1)
        est = sourcing.estimate(q, listings, amazon_price=calculator._num(r.get("price")) or None)
        for d in est["detail"]:
            details.append({"date": TODAY, "asin": r["asin"], "query": q, **{k: d.get(k, "") for k in
                            ("title", "price", "unit", "per_piece", "unit_note", "relevance", "kept",
                             "moq", "supplier", "city", "url")}})
        rng = f"{est['ind_low']:,.0f}–{est['ind_high']:,.0f}" if est["ind_cost"] else ""
        updates.append({"asin": r["asin"], "_sourcing_update": True, "ind_cost": est["ind_cost"] or "",
                        "ind_range": rng, "ind_matches": f"{est['matches']} / {est['suppliers']} (of {est['listings_seen']} seen)",
                        "ind_conf": est["confidence"], "ind_url": url})
        summary.append((r, est))
        log(f"  {r['asin']} '{q}': {est['matches']} comparable of {est['listings_seen']} → "
            f"{est['ind_cost'] if est['ind_cost'] else '—'} ({est['confidence']})")
    if blocked:
        log(f"IndiaMART: {blocked}/{len(todo)} searches blocked", "WARN")
    if details:
        store.write("SOURCING", pd.DataFrame(details))
    sh = pd.DataFrame([{"date": TODAY, "asin": u["asin"], "ind_cost": u["ind_cost"], "conf": u["ind_conf"]}
                       for u in updates])
    if not sh.empty:
        sh = pd.concat([hist.astype(str), sh.astype(str)], ignore_index=True) if not hist.empty else sh
        store.write("SOURCING_HISTORY", sh)
    if updates:
        store.update_calculator([], updates)
    found = [(r, e) for r, e in summary if e["ind_cost"]]
    lines = [f"🏭 <b>IndiaMART estimates</b> — {TODAY}",
             f"Searched {len(summary)} · estimates {len(found)} · blocked {blocked}",
             "<i>Listed prices, not quotes. Open the search, confirm product & unit, then request quotes.</i>"]
    for r, e in sorted(found, key=lambda x: calculator._num(x[0].get("price")) / max(x[1]["ind_cost"], 1),
                       reverse=True)[:8]:
        price = calculator._num(r.get("price"))
        lines.append(f"• {_esc(str(r.get('product', ''))[:55])}\n  Amazon ₹{price:,.0f} · IndiaMART ~₹{e['ind_cost']:,.0f} "
                     f"({_esc(e['confidence'])}) · <a href=\"{sourcing.search_url(e['query'])}\">search</a>")
    send_telegram("\n".join(lines))
    if SHEET_PROBLEM:
        sys.exit(3)


def main():
    if BR_MODE == "sourcing":
        return main_sourcing()
    if BR_MODE == "affiliate":
        return main_affiliate()
    if BR_MODE == "brief":
        return main_brief()
    if BR_MODE == "states":
        return main_states()
    log("=" * 55)
    log(f"📡 BAZAAR RADAR v1.15 — {TODAY} IST · curl_cffi={HAS_CURL_CFFI}")
    log(f"Categories: {len(CATEGORIES)} · lists: {', '.join(LISTS)}")
    log("=" * 55)
    t0 = time.time()
    store = Store()
    written = {}

    frames, amz_stats = fetch_amazon_lists()
    tab_map = {"bestsellers": "AMZ_BESTSELLERS", "movers": "AMZ_MOVERS", "new_releases": "AMZ_NEW_RELEASES"}
    for k, tab in tab_map.items():
        try: written[tab] = store.write(tab, frames[k])
        except Exception as exc: log_tb(f"write {tab}", exc)

    gt = pd.DataFrame(fetch_trending_rss() + fetch_rising_queries())
    try:
        written["GTRENDS"] = store.write("GTRENDS", gt)
        if not gt.empty:                                   # keep rising searches across days
            gh = store.read("GTRENDS_HISTORY")
            gh = gh[gh["date"] != TODAY] if not gh.empty else gh
            gh = pd.concat([gh, gt.astype(str)], ignore_index=True) if not gh.empty else gt.astype(str)
            cutoff = (NOW_IST - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
            written["GTRENDS_HISTORY"] = store.write("GTRENDS_HISTORY", gh[gh["date"] >= cutoff])
            archive_today("gtrends", gt)
    except Exception as exc: log_tb("write GTRENDS", exc)

    signals, heat = pd.DataFrame(), pd.DataFrame()
    try:
        hist = update_history(store.read(SHEET_HISTORY), frames)
        written[SHEET_HISTORY] = store.write(SHEET_HISTORY, hist)
        try:                                     # forward test: resolve pick/baseline ranks daily
            trk = store.read("PICKS")
            if not trk.empty:
                trk = picks.update_tracking(trk, hist)
                written["PICKS"] = store.write("PICKS", trk)
                written["PICK_SCORECARD"] = store.write("PICK_SCORECARD", picks.scorecard(trk))
        except Exception as exc:
            log_tb("pick tracking", exc)
        signals = compute_signals(hist, frames)
        heat = category_heat(signals)
        written["TREND_SIGNALS"] = store.write("TREND_SIGNALS", signals)
        written["CATEGORY_HEAT"] = store.write("CATEGORY_HEAT", heat)
        archive_today("history", hist[hist["date"] == TODAY])
        archive_today("signals", signals)
        log(f"History: {hist['date'].nunique() if not hist.empty else 0} days · {len(signals)} scored ASINs")
    except Exception as exc:
        log_tb("analysis", exc)

    # ── Product-page details for the top-N signals (MRP, discount, bought/month, sellers)
    try:
        if not signals.empty and BR_DETAIL_TOP_N > 0:
            det = fetch_details(signals["asin"].head(BR_DETAIL_TOP_N).tolist())
            if not det.empty:
                written["AMZ_DETAILS"] = store.write("AMZ_DETAILS", det)
                dh = store.read("DETAIL_HISTORY")
                dh = dh[dh["date"] != TODAY] if not dh.empty else dh
                dh = pd.concat([dh, det.astype(str)], ignore_index=True) if not dh.empty else det.astype(str)
                cutoff = (NOW_IST - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
                dh = dh[dh["date"] >= cutoff]
                written["DETAIL_HISTORY"] = store.write("DETAIL_HISTORY", dh)
                signals = enrich_signals(signals, det, dh)
                written["TREND_SIGNALS"] = store.write("TREND_SIGNALS", signals)
    except Exception as exc:
        log_tb("product details", exc)

    # ── Compliance flags (REVIEW REQUIRED, never "cleared") + competition note
    try:
        if not signals.empty:
            signals["compliance"] = [compliance.compliance_flags(t, c) for t, c in
                                     zip(signals["title"], signals.get("category_slug", signals["category"]))]
            signals["seasonal"] = [compliance.seasonal_flag(t) for t in signals["title"]]
            signals["competition"] = [compliance.competition_note(s_, b_) for s_, b_ in
                                      zip(signals.get("seller_offers", [None] * len(signals)),
                                          signals.get("bought_past_month_min", [None] * len(signals)))]
            written["TREND_SIGNALS"] = store.write("TREND_SIGNALS", signals)
    except Exception as exc:
        log_tb("compliance flags", exc)

    # ── YouTube review-video check (optional, needs YOUTUBE_API_KEY)
    try:
        if YOUTUBE_API_KEY and not signals.empty:
            act = signals[signals["actionable"].astype(bool)] if "actionable" in signals else signals
            yt = pd.DataFrame(youtube_check.check_products(list(zip(act["asin"], act["title"])),
                                                           YOUTUBE_API_KEY, max_products=BR_YT_TOP_N))
            if not yt.empty:
                yt.insert(0, "date", TODAY)
                written["YT_CHECK"] = store.write("YT_CHECK", yt)
                yh = store.read("YT_HISTORY")
                yh = yh[yh["date"] != TODAY] if not yh.empty else yh
                yh = pd.concat([yh, yt.astype(str)], ignore_index=True) if not yh.empty else yt.astype(str)
                written["YT_HISTORY"] = store.write("YT_HISTORY", yh)
                signals = signals.drop(columns=[c for c in ("yt_verdict", "yt_views_per_day") if c in signals]) \
                    .merge(yt[["asin", "verdict", "views_per_day"]].rename(
                        columns={"verdict": "yt_verdict", "views_per_day": "yt_views_per_day"}), on="asin", how="left")
                written["TREND_SIGNALS"] = store.write("TREND_SIGNALS", signals)
                log(f"YouTube: {len(yt)} products checked")
        elif not YOUTUBE_API_KEY:
            log("YOUTUBE_API_KEY not set — skipping YouTube check", "DBG")
    except Exception as exc:
        log_tb("youtube check", exc)

    # ── Unit-economics calculator (keeps your sourcing quotes & overrides)
    try:
        if not signals.empty:
            act = signals[signals["actionable"].astype(bool)] if "actionable" in signals else signals
            act = act[~act.get("category_slug", act["category"]).isin(BR_CALC_EXCLUDE)]
            act = act[act["title"].map(_has_title)]          # no title → can't search/quote it yet
            n_days = hist["date"].nunique() if "hist" in locals() and not hist.empty else 0
            if n_days < BR_CALC_MIN_DAYS:
                log(f"Calculator: {n_days}/{BR_CALC_MIN_DAYS} days of history — not adding new rows yet "
                    f"(day-1 scores are ties); existing rows still refresh")
                act = act.iloc[0:0]
            written["CALCULATOR"] = store.update_calculator(
                act.where(act.notna(), None).to_dict("records")[:BR_CALC_TOP_N],
                signals.where(signals.notna(), None).to_dict("records"))
    except Exception as exc:
        log_tb("calculator", exc)

    # ── State-level search interest
    states = pd.DataFrame()
    try:
        st = pd.DataFrame(fetch_state_interest() if BR_STATE_TRENDS else [])
        if not st.empty:
            written["GTRENDS_STATES"] = store.write("GTRENDS_STATES", st)
            states = state_summary(st)
            written["STATE_SUMMARY"] = store.write("STATE_SUMMARY", states)
    except Exception as exc:
        log_tb("state interest", exc)

    try: store.write("RUN_LOG", pd.DataFrame(RUN_LOG, columns=["time", "level", "message"]))
    except Exception as exc: log_tb("write RUN_LOG", exc)

    # ── Telegram digest
    lines = [f"📡 <b>Bazaar Radar</b> v1.15 — {TODAY}",
             f"Amazon lists: {amz_stats['ok']} ok · {amz_stats['blocked']} blocked"]
    if DETAIL_STATS.get("skipped"):
        lines.append(f"⚠️ Amazon captcha'd product pages — stopped after {DETAIL_STATS['blocked']} blocks "
                     f"({DETAIL_STATS['skipped']} skipped). Avoid extra manual runs today.")
    if SHEET_PROBLEM:
        lines.append(f"⚠️ <b>Sheet not updated:</b> {_esc(SHEET_PROBLEM[0])}\nResults saved as run artifact "
                     f"'bazaar-radar-output'. {_actions_url()}")
    quarantined = pd.DataFrame()
    if not signals.empty and "actionable" in signals:
        quarantined = signals[~signals["actionable"].astype(bool)]
        signals_act = signals[signals["actionable"].astype(bool)]
    else:
        signals_act = signals
    if not signals_act.empty:     # untitled rows keep their place in the ranking but show as a link
        signals_act = signals_act.copy()
        signals_act["title"] = [t if _has_title(t) else f"(title not loaded yet — open {a})"
                                for t, a in zip(signals_act["title"], signals_act["asin"])]
    n_hist = hist["date"].nunique() if "hist" in locals() and not hist.empty else 0
    if not signals_act.empty and n_hist < BR_CALC_MIN_DAYS:
        lines.append(f"\n⏳ <b>Learning: day {n_hist} of {BR_CALC_MIN_DAYS}.</b> Scores are ties until rank history "
                     f"builds, so no ranking is shown yet — don't act on today's list.")
        lines.append("<i>Sample of products being tracked:</i>")
        for _, r in signals_act[~signals_act["title"].str.startswith("(title not loaded")].head(5).iterrows():
            lines.append(f"• {_esc(str(r['title'])[:70])} · {_esc(r['category'])}")
    elif not signals_act.empty:
        lines.append(f"Confidence: {_esc(signals_act['confidence'].iloc[0])}\n\n<b>Top trend signals</b>")
        for _, r in signals_act.head(10).iterrows():
            price = f"₹{int(r['price_inr']):,}" if pd.notna(r["price_inr"]) else ""
            extra = []
            if "discount_pct" in r and pd.notna(r.get("discount_pct")):
                extra.append(f"-{r['discount_pct']:.0f}%")
            if "bought_past_month_min" in r and pd.notna(r.get("bought_past_month_min")):
                extra.append(f"{int(r['bought_past_month_min']):,}+/mo")
            if "seller_offers" in r and pd.notna(r.get("seller_offers")):
                extra.append(f"{int(r['seller_offers'])} sellers")
            if str(r.get("compliance", "")).startswith("REVIEW"):
                extra.append("⚖ certification check")
            if str(r.get("seasonal", "")).startswith("SEASONAL"):
                extra.append("📅 " + str(r["seasonal"]).replace("SEASONAL: ", "") + " — seasonal")
            if pd.notna(r.get("yt_verdict")) and r.get("yt_verdict"):
                extra.append(f"▶ {r['yt_verdict']}")
            lines.append(f"{_esc(r['signal'])} · <b>{r['trend_score']:.0f}</b> · {_esc(r['category'])}\n"
                         f"  <a href=\"{r['url']}\">{_esc(str(r['title'])[:70])}</a> {price} "
                         f"{_esc(' · '.join(extra))}")
    if not quarantined.empty:
        lines.append(f"\n⏸ <b>Quarantined</b> ({len(quarantined)}): price dip &lt;48h, decision in ≤2 days")
        for _, r in quarantined.head(3).iterrows():
            lines.append(f"• {_esc(str(r['title'])[:60])} · score {r['trend_score']:.0f} (held)")
    if not heat.empty and n_hist >= BR_CALC_MIN_DAYS:
        lines.append("\n<b>Hottest categories</b>")
        for _, r in heat.head(5).iterrows():
            lines.append(f"• {_esc(r['category'])} — {r['top10_score']:.0f} "
                         f"({r['movers']} movers, {r['new_launches_selling']} launches)")
    if not states.empty:
        lines.append("\n<b>States leading search interest</b> <i>(search share, not sales)</i>")
        for _, r in states.head(5).iterrows():
            lines.append(f"• {_esc(r['state'])} — top-5 for {r['top5_keywords']}/{r['keywords']} keywords, "
                         f"rising on {r['rising_keywords']}")
    rising = gt[gt["source"] == "rising_query"] if not gt.empty else gt
    if not rising.empty:
        lines.append("\n<b>Rising searches (Google, IN)</b>")
        rising = rising.groupby("seed", sort=False).head(2)          # variety: max 2 per seed keyword
        for _, r in rising.head(8).iterrows():
            lines.append(f"• {_esc(r['query'])} <i>(via {_esc(r['seed'])}, +{r['value']}%)</i>")
    if amz_stats["ok"] == 0:
        lines.append(f"\n⚠️ All Amazon lists blocked. {_actions_url()}")
    lines.append(f"\n⏱ {time.time() - t0:.0f}s")
    send_telegram("\n".join(lines))

    log(f"Done in {time.time() - t0:.0f}s · rows written: {written}")
    if amz_stats["ok"] == 0:
        log("No Amazon list fetched successfully — exiting non-zero", "ERR")
        sys.exit(2)
    if SHEET_PROBLEM:
        log("Run finished but the Google Sheet was not updated — exiting non-zero so it shows red", "ERR")
        sys.exit(3)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_tb("FATAL", exc)
        send_telegram(f"❌ <b>Bazaar Radar crashed</b>\n<code>{_esc(exc)}</code>\n{_actions_url()}")
        sys.exit(1)
