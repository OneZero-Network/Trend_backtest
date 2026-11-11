"""
Independent product discovery: finds products WITHOUT starting from Amazon's lists.

Two sources, each checked against the other:
  1. Google Trends: rising searches around SHOPPING INTENT (shop names, budget and gift phrases,
     plus seeds that grow: yesterday's strong rising product searches become seeds for 7 days).
  2. YouTube: this week's Indian shopping videos ("amazon finds", "flipkart haul"…), ranked by
     views/day. Product links in descriptions (amzn.to, fkrt.it, …) are followed only as far as the
     redirect address, never loading the shop page, to get the exact product ID. A product linked
     by several DIFFERENT channels is a signal; one creator is one opinion (often paid).

Confidence = number of independent sources that agree. Nothing here is posted until 2+ sources agree.
The proof test: does an item found here show up on Amazon's bestseller lists within 14 days of being
found? (lead_test). If yes, these sources give early warning; if not, we've learned it cheaply.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

# ── Google Trends seeds ───────────────────────────────────────────────
# v1.33: SPECIFIC nouns, rotated daily. Broad seeds ("flipkart", "course", "software") pulled in films,
# celebrity news and sale dates (10 Nov: "jailer 2 prime release date", "namjooning stanford").
# Specific seeds produced real products the same day ("hot lunch box for office" +144,800%).
PRODUCT_SEEDS = ["lunch box", "water bottle", "storage box", "air fryer", "electric kettle", "mixer grinder",
                 "cookware set", "bedsheet", "curtains", "wall decor", "table lamp", "mop", "vacuum cleaner",
                 "air purifier", "room heater", "yoga mat", "dumbbells", "cycle", "backpack", "trolley bag",
                 "dog food", "cat food", "plant pots", "garden tools", "power bank", "earbuds", "smart watch",
                 "phone stand", "led bulb", "extension board", "kurti", "saree", "sneakers", "wallet", "perfume",
                 "face serum", "hair dryer", "trimmer", "toys", "school bag", "gift hamper", "organizer"]
SKILL_SEEDS = ["excel", "python", "data analytics", "digital marketing", "spoken english", "graphic design",
               "video editing", "artificial intelligence", "photography", "tally", "ui ux", "cyber security",
               "cloud computing", "sql", "power bi", "canva", "ielts", "web development", "java", "autocad",
               "content writing", "public speaking", "machine learning", "prompt engineering", "seo"]
APP_SEEDS = ["video editor", "photo editor", "pdf editor", "vpn", "password manager", "crm", "accounting software",
             "website builder", "ai image generator", "ai chatbot", "note taking", "project management",
             "antivirus", "screen recorder", "email marketing", "invoice", "ai voice", "presentation",
             "background remover", "resume builder", "online meeting", "cloud storage", "ai writing", "scheduling",
             "inventory software"]
SEEDS_PER_DAY = {"products": 10, "skills": 5, "apps": 5}
BASE_SEEDS = PRODUCT_SEEDS                      # kept for compatibility


def rotate(pool, n, today):
    """Different slice each day; the whole list is covered every len(pool)/n days."""
    if not pool:
        return []
    k = datetime.fromisoformat(str(today)[:10]).toordinal() * n
    return [pool[(k + i) % len(pool)] for i in range(min(n, len(pool)))]


def about_seed(query, seed):
    """The rising search must actually be about its seed (share a seed word ≥3 letters)."""
    q = set(re.findall(r"[a-z0-9]+", str(query).lower()))
    words = [w for w in re.findall(r"[a-z0-9]+", str(seed).lower()) if len(w) >= 3] or [str(seed).lower()]
    return any(w in q or any(t.startswith(w[:5]) for t in q if len(w) >= 6) for w in words)
SNOWBALL_DAYS = 7
MAX_SNOWBALL = 8
MIN_RISE = 250                      # % rise; "Breakout" counts. Huge % from ~0 is filtered by 2-day persistence

SHOP_WORDS = re.compile(r"\b(flipkart|amazon|meesho|myntra|ajio|nykaa|online|buy|shopping|sale|offer|offers|"
                        r"deal|deals|discount|coupon|price|prices|cost|rate|india|in india|near me|app|website|"
                        r"order|delivery|cod|cash on delivery|best|top|latest|new|\d{4})\b", re.I)
NOT_PRODUCT = re.compile(r"\b(movie|film|song|lyrics|meaning|trailer|series|episode|actor|actress|news|today|"
                         r"release date|release|prime video|ott|netflix|hotstar|box office|cast|review in hindi|"
                         r"result|results|match|score|cricket|ipl|share|stock|ipo|login|sign in|customer care|"
                         r"care number|contact|refund|return policy|tracking|track order|job|jobs|career|salary|"
                         r"how to|what is|why|kaise|kya|recipe|wallpaper|photo|images|status|quotes|wishes|"
                         r"big billion|great indian|festival sale|sale date|today offer|bank offer|emi|"
                         r"seller|registration|gst|invoice|apk|mod|game|download|pdf)\b", re.I)


# ── lanes: products (default) · skills & courses · apps & software ─────
# Never promoted in ANY lane: real-money gaming/betting (banned in India, 2025), lending/loan apps and
# trading/crypto (RBI/SEBI rules), pirated software, adult.
BANNED = re.compile(r"\b(bet|betting|casino|rummy|teen ?patti|poker|satta|matka|fantasy|dream ?11|aviator|"
                    r"lottery|loan|loans|instant cash|credit line|trading|intraday|options|forex|crypto|bitcoin|"
                    r"stock tips?|apk|mod|crack|cracked|hack|hacked|premium free|free fire diamond|adult|xxx|dating)\b", re.I)

SKILL_WORDS = re.compile(r"\b(course|courses|class|classes|coaching|certification|certificate|certified|learn|"
                         r"learning|online|offline|free|paid|training|tutorial|tutorials|institute|academy|near me|"
                         r"fees|fee|for beginners|beginners|in hindi|hindi|in english|full|complete|best|top|india|"
                         r"\d{4}|udemy|coursera|youtube)\b", re.I)
NOT_SKILL = re.compile(r"\b(result|results|admit card|answer key|cut ?off|exam date|syllabus pdf|login|recruitment|"
                       r"release date|movie|song|news|stanford|harvard|iit|university|college|school|"
                       r"vacancy|vacancies|salary|job|jobs|notification|form|apply online|scholarship|news|"
                       r"meaning|movie|song|game)\b", re.I)
APP_WORDS = re.compile(r"\b(app|apps|application|software|tool|tools|website|online|free|download|for pc|"
                       r"for android|for iphone|for windows|for mac|best|top|new|latest|india|\d{4}|alternative|"
                       r"alternatives|use|how)\b", re.I)
NOT_APP = re.compile(r"\b(login|sign in|customer care|contact|helpline|not working|down|update|uninstall|delete "
                     r"account|jobs?|hiring|engineer|salary|careers?|stock|share price|lawsuit|hack|breach|"
                     r"release date|movie|song|lyrics|status|wallpaper|photo|meaning|government|sarkari|aadhaar|pan "
                     r"card|ration|voter|epfo|upi|recharge|bill pay)\b", re.I)

LANES = {
    "products": {"seeds": PRODUCT_SEEDS, "strip": SHOP_WORDS, "block": NOT_PRODUCT, "key": "q", "yt_suffix": "review"},
    "skills":   {"seeds": SKILL_SEEDS, "strip": SKILL_WORDS, "block": NOT_SKILL, "key": "sk", "yt_suffix": "tutorial"},
    "apps":     {"seeds": APP_SEEDS, "strip": APP_WORDS, "block": NOT_APP, "key": "ap", "yt_suffix": "review"},
}


def lane_seeds(lane, today=None):
    """Today's rotating slice of this lane's seed list (whole list when no date given)."""
    pool = LANES[lane]["seeds"]
    return rotate(pool, SEEDS_PER_DAY.get(lane, 5), today) if today else list(pool)


def clean_phrase(q, lane="products"):
    """'air fryer flipkart under 3000' → 'air fryer' · 'excel course in hindi free' → 'excel'."""
    s = re.sub(r"\bunder\s*₹?\s*\d+\b", " ", str(q).lower())
    s = re.sub(r"\bfor (men|women|boys|girls|kids|him|her|husband|wife|mom|dad|sister|brother)\b", " ", s)
    s = LANES[lane]["strip"].sub(" ", s)
    s = re.sub(r"[^a-z0-9+# ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_product_phrase(q, lane="products"):
    if BANNED.search(str(q)) or LANES[lane]["block"].search(str(q)):
        return False
    c = clean_phrase(q, lane)
    words = [w for w in c.split() if not w.isdigit()]
    return 1 <= len(words) <= 5 and len(c) >= (2 if lane != "products" else 4)


def rise_value(v):
    s = str(v).lower()
    if "breakout" in s:
        return 5000.0
    try:
        return float(s.replace("%", "").replace(",", "").replace("+", ""))
    except ValueError:
        return 0.0


LEARN_WORDS = re.compile(r"\b(course|courses|class|classes|coaching|tutorial|tutorials|learn|learning|training|"
                         r"certification|certificate|for beginners|beginners|roadmap|interview questions|projects?|"
                         r"syllabus|notes|in hindi|full course|bootcamp|practice|exercises?)\b", re.I)


def seed_ok(seed, lane, today):
    """A growing seed must still pass today's filters (lane rules, bans, season window)."""
    if not is_product_phrase(seed, lane):
        return False
    if lane == "products":
        import compliance, season
        if not season.window(compliance.seasonal_flag(seed), today)["affiliate_ok"]:
            return False
    return True


def active_seeds(seed_rows, today, base=BASE_SEEDS, lane="products"):
    """seed_rows: [{'seed','added','last_rising','lane'}] → base + this lane's snowball seeds still active."""
    t = datetime.fromisoformat(str(today)[:10])
    live = []
    for r in seed_rows or []:
        if str(r.get("lane") or "products") != lane:
            continue
        last = max(str(r.get("added", "")), str(r.get("last_rising", "")))
        try:
            age = (t - datetime.fromisoformat(last[:10])).days
        except ValueError:
            continue
        if 0 <= age <= SNOWBALL_DAYS and r["seed"] not in base and r["seed"] not in live \
                and seed_ok(r["seed"], lane, today):          # re-checked daily: yesterday's noise can't come back
            live.append(r["seed"])
    # up to 3 growing seeds go FIRST (the daily per-lane cap would otherwise cut them), then today's slice
    grow = [s for s in live if s not in base][:3]
    return grow + [b for b in base if b not in grow]


def update_seeds(seed_rows, trend_rows, today):
    """Strong product phrases found today become seeds (or refresh their last_rising)."""
    by = {(str(r.get("lane") or "products"), r["seed"]): dict(r, lane=str(r.get("lane") or "products"))
          for r in (seed_rows or [])}
    for r in trend_rows:
        p, lane = r["phrase"], r.get("lane", "products")
        if r["rise"] >= 1000 and len(p.split()) >= 2:
            if (lane, p) in by:
                by[(lane, p)]["last_rising"] = today
            else:
                by[(lane, p)] = {"seed": p, "added": today, "last_rising": today, "lane": lane}
    cutoff = (datetime.fromisoformat(today) - timedelta(days=30)).strftime("%Y-%m-%d")
    return [r for r in by.values() if max(str(r.get("added")), str(r.get("last_rising"))) >= cutoff]


def relevant(q, seed, lane):
    """Is this rising search really about the lane?
    products: shares a seed word · skills: shares a seed word AND has a learning word ('python course', not
    'burmese python') · apps: shares a seed word, OR is a comparison ('capcut alternative'), OR is a short
    tool/brand name (≤3 words) — app searches are mostly brand names; YouTube must confirm them anyway."""
    if lane == "skills":
        return about_seed(q, seed) and bool(LEARN_WORDS.search(str(q)))
    if lane == "apps":
        return (about_seed(q, seed) or bool(re.search(r"\b(alternatives?|vs|versus|like)\b", str(q), re.I))
                or len(re.findall(r"[a-z0-9]+", str(q).lower())) <= 3)
    return about_seed(q, seed)


def trends_rows(related, seed, today, lane="products", stats=None):
    """related: (query, value) pairs (pytrends 'rising') → cleaned phrases for this lane.
    stats (dict, optional) counts why results were dropped — shown in the log/summary."""
    out = []
    st = stats if stats is not None else {}
    for q, v in related:
        st["raw"] = st.get("raw", 0) + 1
        if not is_product_phrase(q, lane):
            st["blocked"] = st.get("blocked", 0) + 1; continue
        if not relevant(q, seed, lane):
            st["off_topic"] = st.get("off_topic", 0) + 1; continue
        rise = rise_value(v)
        if rise < MIN_RISE:
            st["weak"] = st.get("weak", 0) + 1; continue
        if lane == "products":
            import compliance, season
            if not season.window(compliance.seasonal_flag(q), today)["affiliate_ok"]:
                st["out_of_season"] = st.get("out_of_season", 0) + 1; continue
        st["kept"] = st.get("kept", 0) + 1
        out.append({"date": today, "source": "gtrends", "phrase": clean_phrase(q, lane), "query": str(q),
                    "seed": seed, "rise": rise, "lane": lane})
    return out


def yt_phrase_check(videos_by_phrase, now=None, min_vpd=100):
    """Skills/apps second source: {phrase: [video dicts]} (recent '<phrase> tutorial/review' videos) →
    {phrase: (channels with ≥ min_vpd views/day, total views/day)}. 3+ channels = real interest."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for ph, vids in videos_by_phrase.items():
        chans, tot = set(), 0.0
        for v in vids:
            try:
                pub = datetime.strptime(v["published"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            vpd = int(v.get("views", 0)) / max((now - pub).total_seconds() / 86400, 1)
            if vpd >= min_vpd:
                chans.add(v["channel"]); tot += vpd
        out[ph] = (len(chans), round(tot))
    return out


# ── YouTube ───────────────────────────────────────────────────────────
YT_QUERIES = ["amazon finds india", "flipkart haul", "meesho haul", "gadgets under 500",
              "kitchen gadgets india", "unboxing india"]
MIN_CREATORS = 3

LINK_RX = re.compile(r"https?://(?:www\.)?(?:amzn\.to|amzn\.in|amazon\.in|a\.co|fkrt\.it|fkrt\.cc|fkrt\.co|"
                     r"dl\.flipkart\.com|flipkart\.com)/[^\s)\]>\"']+", re.I)
ASIN_RX = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})")
FK_PID_RX = re.compile(r"[?&]pid=([A-Z0-9]{16})")
FK_ITM_RX = re.compile(r"/p/(itm[a-z0-9]+)", re.I)
SHORT_HOSTS = ("amzn.to", "amzn.in", "a.co", "fkrt.it", "fkrt.cc", "fkrt.co", "dl.flipkart.com")


def find_links(text):
    return list(dict.fromkeys(m.group(0).rstrip(".,") for m in LINK_RX.finditer(text or "")))


def is_short(url):
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
    return host in SHORT_HOSTS


def product_from_url(url):
    """→ (kind, id, name_from_slug) or None. Uses only the URL text — the shop page is never loaded."""
    u = unquote(url)
    m = ASIN_RX.search(u)
    if m and "amazon." in u:
        slug = u.split("amazon.in/")[-1].split("/dp/")[0].split("/gp/")[0] if "/dp/" in u or "/gp/" in u else ""
        name = re.sub(r"[-_]+", " ", slug).strip() if slug and "/" not in slug else ""
        return ("amazon", m.group(1), name)
    if "flipkart.com" in u:
        pid = FK_PID_RX.search(u)
        itm = FK_ITM_RX.search(u)
        if pid or itm:
            slug = u.split("flipkart.com/")[-1].split("/p/")[0]
            name = re.sub(r"[-_]+", " ", slug).strip() if slug and "/" not in slug else ""
            return ("flipkart", (pid.group(1) if pid else itm.group(1)), name)
    return None


def resolve(url, head, max_hops=3):
    """Follow redirect *addresses* only. head(url) → Location header or None (caller does the request
    with allow_redirects=False). Stops as soon as the address names a product."""
    cur = url
    for _ in range(max_hops):
        p = product_from_url(cur)
        if p:
            return p
        if not is_short(cur):
            return None
        nxt = head(cur)
        if not nxt:
            return None
        cur = nxt if nxt.startswith("http") else "https://" + re.sub(r"^https?://", "", cur).split("/")[0] + nxt
    return product_from_url(cur)


def youtube_rows(videos, head, today, now=None):
    """videos: [{'id','channel','title','description','views','published'}] → product rows aggregated
    across creators: one row per product with creators, views_per_day, sample video."""
    now = now or datetime.now(timezone.utc)
    agg = defaultdict(lambda: {"channels": set(), "vpd": 0.0, "videos": [], "name": ""})
    cache = {}
    for v in videos:
        try:
            pub = datetime.strptime(v["published"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        vpd = int(v.get("views", 0)) / max((now - pub).total_seconds() / 86400, 1)
        for link in find_links(v.get("description", ""))[:40]:
            if link not in cache:
                try:
                    cache[link] = resolve(link, head)
                except Exception:
                    cache[link] = None
            p = cache[link]
            if not p:
                continue
            a = agg[(p[0], p[1])]
            if v["channel"] not in a["channels"]:
                a["channels"].add(v["channel"])
                a["vpd"] += vpd
            a["videos"].append(v["id"])
            if p[2] and len(p[2]) > len(a["name"]):
                a["name"] = p[2]
            if not a.get("label"):
                a["label"] = str(v.get("title", ""))[:70]
    out = []
    for (kind, pid), a in agg.items():
        name = a["name"] or (f"(unnamed — from video: {a.get('label', '')})" if a.get("label") else "")
        out.append({"date": today, "source": "youtube", "kind": kind, "product_id": pid, "name": name,
                    "creators": len(a["channels"]), "views_per_day": round(a["vpd"]),
                    "videos": " ".join(f"https://youtu.be/{x}" for x in list(dict.fromkeys(a["videos"]))[:3])})
    return sorted(out, key=lambda r: (-r["creators"], -r["views_per_day"]))


# ── combine ───────────────────────────────────────────────────────────
def _tokens(s):
    return {w for w in re.findall(r"[a-z]{3,}", str(s).lower())}


def combine(trend_rows, yt_rows, amazon_titles_today, history_rows, today, yt_phrase=None):
    """→ DISCOVERY rows for today. Agreement = independent sources naming the same thing:
       phrase ↔ YouTube product name (all phrase words in the name), phrase/product ↔ Amazon list title.
       history_rows: earlier DISCOVERY rows (for first_seen / days_seen)."""
    first, days = {}, defaultdict(set)
    for r in history_rows or []:
        k = r["item_key"]
        first[k] = min(first.get(k, r["date"]), r["date"])
        days[k].add(r["date"])
    amz = [(_tokens(t), t) for t in amazon_titles_today]
    out = {}
    yt_phrase = yt_phrase or {}
    for t in trend_rows:
        lane = t.get("lane", "products")
        k = f"{LANES[lane]['key']}:" + t["phrase"]
        cur = out.get(k)
        if cur is None or t["rise"] > cur["trend_rise"]:
            ch, vpd = yt_phrase.get((lane, t["phrase"]), (0, 0))
            out[k] = {"date": today, "item_key": k, "lane": lane, "kind": "phrase", "name": t["phrase"],
                      "product_id": "", "trend_rise": t["rise"], "trend_seed": t["seed"], "creators": ch,
                      "views_per_day": vpd, "videos": ""}
    for y in yt_rows:
        if y["creators"] < 1 or BANNED.search(str(y.get("name", ""))):
            continue
        k = f"{y['kind'][:2]}:{y['product_id']}"
        out[k] = {"date": today, "item_key": k, "lane": "products", "kind": y["kind"], "name": y["name"],
                  "product_id": y["product_id"],
                  "trend_rise": 0, "trend_seed": "", "creators": y["creators"], "views_per_day": y["views_per_day"],
                  "videos": y["videos"]}
    yt_named = [(r, _tokens(r["name"])) for r in out.values()
                if r["kind"] != "phrase" and r["name"] and not str(r["name"]).startswith("(unnamed")]
    for r in out.values():
        src = set()
        if r["trend_rise"]:
            src.add("gtrends")
        if r["creators"] >= MIN_CREATORS and r["kind"] != "phrase":
            src.add("youtube")
        toks = set() if str(r["name"]).startswith("(unnamed") else _tokens(r["name"])
        if r["kind"] == "phrase" and r["creators"] >= MIN_CREATORS:
            src.add("youtube")                                  # recent tutorials/reviews from 3+ channels
        if r["kind"] == "phrase" and r["lane"] == "products" and len(toks) >= 1:
            if any(toks <= yt for o, yt in yt_named if o["creators"] >= MIN_CREATORS):
                src.add("youtube")
        # a ONE-word search ("toys", "organizer") matches some Amazon title by chance → never counts as agreement
        if r["lane"] == "products" and toks and len(toks) >= 2:
            need = toks if r["kind"] == "phrase" else set(list(toks)[:3])
            if any(need <= a for a, _ in amz):
                src.add("amazon_list")
        k = r["item_key"]
        seen = days[k] | {today}
        r["sources"] = ",".join(sorted(src))
        r["n_sources"] = len(src)
        r["first_seen"] = min(first.get(k, today), today)
        r["days_seen"] = len(seen)
        # 2+ independent sources; a Trends-only phrase must also persist 2+ days (one-day spikes from ~0 are noise)
        persistent = r["kind"] != "phrase" or r["days_seen"] >= 2 or "youtube" in src
        r["status"] = "2+ sources" if len(src) >= 2 and persistent else "watch"
    return sorted(out.values(), key=lambda r: (-r["n_sources"], -r["creators"], -r["trend_rise"]))


def lead_test(disc_rows, bestseller_rows, window=14):
    """For items first found by Trends/YouTube while NOT on Amazon's lists: did they appear on an Amazon
    bestseller list within `window` days? → dict(found, eligible, appeared, rate, median_lead_days).
    bestseller_rows: [{'date','asin','title'}]."""
    first = {}
    for r in disc_rows:
        k = r["item_key"]
        if k not in first or r["date"] < first[k]["date"]:
            first[k] = r
    by_day = defaultdict(list)
    for b in bestseller_rows:
        by_day[b["date"]].append((b["asin"], _tokens(b["title"])))
    days = sorted(by_day)
    eligible = appeared = 0
    leads = []
    for k, r in first.items():
        if "amazon_list" in str(r.get("sources", "")) or str(r.get("lane") or "products") != "products":
            continue                                       # already on Amazon when found / not a product lane
        d0 = r["date"]
        later = [d for d in days if d > d0 and (datetime.fromisoformat(d) - datetime.fromisoformat(d0)).days <= window]
        if not later or (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(d0)).days < window:
            continue                                       # window not over yet
        toks = set() if str(r["name"]).startswith("(unnamed") else _tokens(r["name"])
        if r["kind"] == "phrase" and len(toks) < 2:
            continue                                       # one-word phrases match Amazon titles by chance
        def _match(day):
            return any((r["kind"] == "amazon" and asin == r["product_id"]) or
                       (r["kind"] == "phrase" and toks and toks <= t) for asin, t in by_day[day])
        # "Found BEFORE Amazon ranked it" must be true: skip anything already on the lists in the 14 days
        # before (or on) the discovery date — otherwise a product that was there all along counts as a "lead".
        prior = [d for d in days if d <= d0 and (datetime.fromisoformat(d0) - datetime.fromisoformat(d)).days <= window]
        if any(_match(d) for d in prior):
            continue
        eligible += 1
        hit = None
        for d in later:
            if _match(d):
                hit = d; break
        if hit:
            appeared += 1
            leads.append((datetime.fromisoformat(hit) - datetime.fromisoformat(d0)).days)
    leads.sort()
    return {"eligible": eligible, "appeared": appeared,
            "rate": round(appeared / eligible, 3) if eligible else None,
            "median_lead_days": leads[len(leads) // 2] if leads else None}


# ── release gates ─────────────────────────────────────────────────────
GATE_MIN_ITEMS = 20          # skills/apps: items where 2 sources agreed
GATE_PERSIST = 0.5           # …of which ≥ 50% rise again 10+ days after first seen
GATE_LEAD_MIN = 30           # products discovery: eligible items in the lead test
GATE_LEAD_RATE = 0.3         # …of which ≥ 30% reach an Amazon list within 14 days
GATE_B2B_SENT, GATE_B2B_YES = 10, 3


def lane_persistence(disc_rows, lane, today, after_days=10):
    """For items that reached 2+ sources: share seen rising again ≥ after_days after first seen.
    Only items whose window is over are counted."""
    rows = [r for r in disc_rows if str(r.get("lane") or "products") == lane]
    first, agreed, dates = {}, set(), defaultdict(set)
    for r in rows:
        k = r["item_key"]
        first[k] = min(first.get(k, r["date"]), r["date"])
        dates[k].add(r["date"])
        if str(r.get("status")) == "2+ sources":
            agreed.add(k)
    t = datetime.fromisoformat(str(today)[:10])
    done = [k for k in agreed if (t - datetime.fromisoformat(first[k])).days >= after_days + 4]
    again = [k for k in done if any((datetime.fromisoformat(d) - datetime.fromisoformat(first[k])).days >= after_days
                                    for d in dates[k])]
    return {"agreed": len(agreed), "judged": len(done), "persisted": len(again),
            "rate": round(len(again) / len(done), 2) if done else None}


def readiness(disc_rows, lead, leads_b2b, today, b2b_min_price=299):
    """→ [(lane, ready: bool, line)] — the release gates, in plain words."""
    out = []
    n, r = lead.get("eligible", 0), lead.get("rate")
    ok = n >= GATE_LEAD_MIN and r is not None and r >= GATE_LEAD_RATE
    out.append(("Product discovery (Flipkart lane)", ok,
                f"lead test {lead.get('appeared', 0)}/{n} reached Amazon lists in 14 days"
                + (f" ({r:.0%})" if r is not None else "") + f" · need ≥{GATE_LEAD_MIN} items and ≥{GATE_LEAD_RATE:.0%}"))
    for lane, label in (("skills", "Skills & courses"), ("apps", "Apps & software")):
        p = lane_persistence(disc_rows, lane, today)
        ok = p["agreed"] >= GATE_MIN_ITEMS and p["rate"] is not None and p["rate"] >= GATE_PERSIST
        out.append((label, ok, f"{p['agreed']} items with 2 sources (need ≥{GATE_MIN_ITEMS}); "
                               f"still rising after 10+ days: {p['persisted']}/{p['judged']}"
                               + (f" ({p['rate']:.0%})" if p["rate"] is not None else "") + f" · need ≥{GATE_PERSIST:.0%}"))
    sent = [l for l in leads_b2b if str(l.get("date_sent", "")).strip() and "example" not in str(l.get("seller", "")).lower()]
    yes = [l for l in sent if str(l.get("replied", "")).strip().lower() in ("yes", "y")]
    prices = sorted(float(str(l.get("price_inr_month", "")).replace(",", "") or 0) for l in yes
                    if str(l.get("price_inr_month", "")).replace(",", "").replace(".", "").isdigit())
    med = prices[len(prices) // 2] if prices else 0
    ok = len(sent) >= GATE_B2B_SENT and len(yes) >= GATE_B2B_YES and med >= b2b_min_price
    out.append(("Seller data (B2B brief)", ok, f"sent {len(sent)} (need ≥{GATE_B2B_SENT}) · yes {len(yes)} (need ≥{GATE_B2B_YES}) · "
                                               f"median price ₹{med:.0f}/month (need ≥₹{b2b_min_price})"))
    return out
