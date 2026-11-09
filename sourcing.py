"""
IndiaMART price discovery for shortlisted products — TRIAGE, not quotes.

For each calculator row still waiting for a quote, search IndiaMART and estimate an
INDICATIVE per-piece wholesale cost. It never fills your "Sourcing cost" (quote) cell.

Safeguards (each one exists because the naive version gives confident wrong numbers):
  * brand removed from the query (wholesale sells generics); you can override the query
    per row in the calculator's "IndiaMART search words" column
  * only per-piece-comparable prices: piece/pc/nos/unit/pair kept, dozen ÷ 12,
    "pack/set/box of N" ÷ N when N is stated; kg / litre / metre / box-without-count dropped
  * relevance filter: a listing must share ≥ MIN_OVERLAP of the query's words
  * estimate = median of relevant per-piece prices, with P25–P75 range, supplier count and
    a confidence level; < MIN_MATCHES matches → no estimate
  * listed prices are often GST-unclear, stale or bait for enquiries → this is a reason to
    request a quote, never a substitute for one

IndiaMART has no public product-search API, and its terms restrict scraping: keep this small
(default 20 products/day), slow (≥6 s between requests) and for your own research.
Unrecognised pages are saved to output/debug/ for parser fixes.
"""
import re, json, statistics
from urllib.parse import quote_plus

SEARCH_URL = "https://dir.indiamart.com/search.mp?ss={q}"
MIN_OVERLAP = 0.5
MIN_MATCHES = 3

_STOP = re.compile(r"\b(for|with|and|the|of|in|to|by|men|women|girls|boys|kids|new|latest|stylish|premium|"
                   r"best|top|pack|set|combo|combo\s*pack|piece|pieces|pcs|pc|\d+\s*(ml|g|gm|kg|l|inch|cm|mm|w|mah|"
                   r"ft|feet|m))\b", re.I)
_UNIT = re.compile(r"/\s*(piece|pieces|pc|pcs|nos|no|unit|units|number|pair|pairs|dozen|doz|set|sets|pack|packs|"
                   r"box|boxes|kg|kilogram|gram|g|litre|liter|l|ml|meter|metre|m|feet|ft|roll|carton|bag|bundle)\b", re.I)
_PRICE = re.compile(r"(?:₹|Rs\.?|INR)\s*(\d[\d,]*(?:\.\d+)?)", re.I)   # must start with a digit ("₹ ," crashed v1.20)
_OF_N = re.compile(r"\b(?:pack|set|box|combo|bundle|carton)\s*(?:of)?\s*(\d{1,4})\b|\b(\d{1,4})\s*(?:pcs|pieces|pc)\b", re.I)
_MOQ = re.compile(r"(?:Minimum Order Quantity|MOQ)\s*[:\-]?\s*([\d,]+)\s*([A-Za-z]+)?", re.I)


def tokens(text):
    t = _STOP.sub(" ", str(text).lower())
    return [w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 1 and not w.isdigit()]


def make_query(title, override=""):
    """Brand-less search words from an Amazon title (or your override)."""
    if str(override or "").strip():
        return str(override).strip()
    head = re.split(r"[|,(\[\-–:]", str(title))[0]
    words = re.findall(r"[A-Za-z0-9]+", head)
    # Amazon titles almost always start with the brand: drop the first word when it is followed
    # by enough words to describe the product.
    if len(words) >= 3:
        words = words[1:]
    toks = tokens(" ".join(words))
    return " ".join(toks[:4])


def per_piece(price, unit, title):
    """→ (price per piece or None, note)."""
    u = (unit or "").lower()
    if u in ("piece", "pieces", "pc", "pcs", "nos", "no", "unit", "units", "number", ""):
        n = _pack_count(title)
        return (price / n, f"pack of {n}") if n > 1 else (price, "per piece")
    if u in ("pair", "pairs"):
        return price, "per pair"
    if u in ("dozen", "doz"):
        return price / 12, "dozen ÷ 12"
    if u in ("set", "sets", "pack", "packs", "box", "boxes", "carton", "bundle", "bag"):
        n = _pack_count(title)
        return (price / n, f"{u} of {n}") if n > 1 else (None, f"per {u}, count unknown")
    return None, f"per {u} — not comparable"


def _pack_count(title):
    m = _OF_N.search(str(title))
    if not m:
        return 1
    n = int(m.group(1) or m.group(2))
    return n if 1 < n <= 1000 else 1


def relevance(query, title):
    q = set(tokens(query))
    if not q:
        return 0.0
    return len(q & set(tokens(title))) / len(q)


# ── parsing ───────────────────────────────────────────────────────────
def parse_search(html):
    """→ list of {title, price, unit, moq, supplier, city, url}. Tries embedded JSON first,
    then visible listing cards."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = _from_jsonld(soup)
    if out:
        return out
    cards, seen = [], set()
    # dir.indiamart.com uses /proddetail/ links; export.indiamart.com (served to non-Indian IPs such as
    # GitHub's US runners) uses /products/?id=… — both supported
    sel = 'a[href*="/proddetail/"], a[href*="/products/?id="]'
    for a in soup.select(sel):
        # climb to this listing's own card: stop BEFORE a container that holds other listings,
        # otherwise a "price on request" card borrows its neighbour's price
        card = a
        for _ in range(6):
            par = card.parent
            if par is None or len({x["href"] for x in par.select(sel)}) > 1:
                break
            card = par
            if _PRICE.search(card.get_text(" ", strip=True)):
                break
        key = id(card)
        if key in seen:
            continue
        seen.add(key)
        txt = card.get_text(" ", strip=True)
        pm = _PRICE.search(txt)
        if not pm:
            continue
        try:
            price = float(pm.group(1).replace(",", ""))
        except ValueError:
            continue                       # one malformed card must never stop the run
        if price <= 0:
            continue
        um = _UNIT.search(txt[pm.end():pm.end() + 25])
        mm = _MOQ.search(txt)
        # grid cards wrap the whole card (title + price) in the link → take the title element, not all text
        tnode = a.select_one('span[class*="line-clamp"], [class*="title"], h2, h3')
        title = (tnode.get_text(" ", strip=True) if tnode else a.get_text(" ", strip=True)) or a.get("title", "")
        title = _PRICE.split(title)[0].strip() if _PRICE.search(title) else title
        if len(title) < 4:
            continue
        sup = card.select_one('[class*="company"], [class*="cmp"], [class*="supplier"]')
        city = card.select_one('[class*="city"], [class*="location"], [class*="addr"]')
        cards.append({"title": title[:150], "price": price,
                      "unit": um.group(1).lower() if um else "",
                      "moq": f"{mm.group(1)} {mm.group(2) or ''}".strip() if mm else "",
                      "supplier": sup.get_text(" ", strip=True)[:80] if sup else "",
                      "city": city.get_text(" ", strip=True)[:40] if city else "",
                      "url": a["href"] if a["href"].startswith("http") else
                             ("https://export.indiamart.com" if "/products/?id=" in a["href"] else "https://www.indiamart.com") + a["href"]})
    return cards


def _from_jsonld(soup):
    out = []
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("itemListElement") or [data]
        for it in items:
            it = it.get("item", it) if isinstance(it, dict) else {}
            if not isinstance(it, dict) or it.get("@type") not in ("Product", None):
                continue
            off = it.get("offers") or {}
            off = off[0] if isinstance(off, list) and off else off
            price = off.get("price") or off.get("lowPrice") if isinstance(off, dict) else None
            if not price or not it.get("name"):
                continue
            unit = str((off.get("priceSpecification") or {}).get("unitText", "")).lower() if isinstance(off, dict) else ""
            seller = off.get("seller") or {}
            out.append({"title": str(it["name"])[:150], "price": float(str(price).replace(",", "")),
                        "unit": unit.replace("per ", "").strip(), "moq": "",
                        "supplier": seller.get("name", "") if isinstance(seller, dict) else "",
                        "city": "", "url": it.get("url", "")})
    return out


# ── estimate ──────────────────────────────────────────────────────────
def estimate(query, listings, amazon_price=None):
    kept = []
    for l in listings:
        rel = relevance(query, l["title"])
        pp, note = per_piece(l["price"], l["unit"], l["title"])
        l = {**l, "relevance": round(rel, 2), "per_piece": round(pp, 2) if pp else None, "unit_note": note,
             "kept": bool(pp and rel >= MIN_OVERLAP)}
        # a wholesale price above the Amazon retail price is almost always a different product
        if l["kept"] and amazon_price and pp > float(amazon_price):
            l["kept"], l["unit_note"] = False, note + "; above Amazon price → likely different product"
        kept.append(l)
    prices = sorted(l["per_piece"] for l in kept if l["kept"])
    sup = {l["supplier"] for l in kept if l["kept"] and l["supplier"]}
    res = {"query": query, "listings_seen": len(listings), "matches": len(prices),
           "suppliers": len(sup) if sup else len(prices), "detail": kept}
    if len(prices) < MIN_MATCHES:
        res.update(ind_cost=None, ind_low=None, ind_high=None,
                   confidence=f"none (<{MIN_MATCHES} comparable matches)")
        return res
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
    med = statistics.median(prices)
    spread = (q[2] / q[0]) if q[0] else 99
    conf = ("high" if len(prices) >= 6 and spread <= 2 else
            "medium" if len(prices) >= 4 and spread <= 3 else "low")
    if len(query.split()) < 2:             # 1-word search ('scotch') matches anything by that brand
        conf = "low (1-word search — type better words in 'IndiaMART search words')"
    res.update(ind_cost=round(med, 2), ind_low=round(q[0], 2), ind_high=round(q[2], 2), confidence=conf)
    return res


def fallback_query(query):
    """Shorter query when the full one finds <3 comparables (long Amazon-style titles over-filter)."""
    toks = query.split()
    return " ".join(toks[:2]) if len(toks) > 2 else ""


def search_url(query):
    return SEARCH_URL.format(q=quote_plus(query))
