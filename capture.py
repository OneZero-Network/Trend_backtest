"""
Capture gap (v1.50) — "no price" does not always mean "out of stock".

Amazon.in list pages render ~30 product cards and lazy-load the rest. In our HISTORY the cards at ranks
31-50 and 81-100 arrive as ASIN+rank PLACEHOLDERS: no price, no rating, no reviews (4,578 of 4,756 unpriced
bestseller rows in the first 17 days). Up to v1.49 "no price" was read as "probably out of stock", so about
19% of every day's scored items were thrown away and the tail ranks were scored blind.

A row is in one of three states:
  card + price          → in stock
  card, no price        → genuinely unavailable (Amazon rendered the card, there was nothing to buy)
  placeholder (no card) → UNKNOWN — we simply did not see it

For an unknown item we look back `carry_days` for a priced card (a climber usually sat at a rendered rank a
few days ago). Found → "carried" (price from that day). Not found → "unknown": not posted, but reported as
unverified, never as "out of stock".
"""
import pandas as pd

STOCK_CARRY_DAYS = 5


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _has(v):
    return v is not None and not pd.isna(v)


def is_placeholder(row):
    """List-page row with an ASIN and a rank but no card data at all (dict or Series)."""
    return not any(_has(row.get(k)) for k in ("price_inr", "rating", "reviews"))


def stock_states(hist, today, carry_days=STOCK_CARRY_DAYS):
    """→ DataFrame indexed by asin: stock_state (in_stock | carried | unavailable | unknown), price_best.
    Only ASINs seen on `today`'s lists are returned."""
    cols = ["asin", "date", "price_inr", "rating", "reviews"]
    if hist is None or len(hist) == 0 or not set(cols) <= set(hist.columns):
        return pd.DataFrame(columns=["stock_state", "price_best"])
    h = hist[cols].copy()
    h["price"], h["rating"], h["reviews"] = _num(h["price_inr"]), _num(h["rating"]), _num(h["reviews"])
    h["priced"] = h["price"].fillna(0) > 0
    h["card"] = h["priced"] | h["rating"].notna() | h["reviews"].notna()
    t = h[h["date"] == today]
    if t.empty:
        return pd.DataFrame(columns=["stock_state", "price_best"])
    g = t.groupby("asin").agg(priced=("priced", "max"), card=("card", "max"))
    g["price_today"] = t[t["priced"]].groupby("asin")["price"].min()
    cutoff = (pd.Timestamp(today) - pd.Timedelta(days=carry_days)).strftime("%Y-%m-%d")
    prior = h[(h["date"] < today) & (h["date"] >= cutoff) & h["priced"]].sort_values("date")
    carried = prior.groupby("asin")["price"].last()

    state, price = [], []
    for a, r in g.iterrows():
        if r["priced"]:
            state.append("in_stock"); price.append(r["price_today"])
        elif r["card"]:
            state.append("unavailable"); price.append(float("nan"))
        elif a in carried.index:
            state.append("carried"); price.append(carried[a])
        else:
            state.append("unknown"); price.append(float("nan"))
    return pd.DataFrame({"stock_state": state, "price_best": price}, index=g.index)


def summarize(states):
    """Counts for the digest line."""
    if states is None or len(states) == 0:
        return {}
    c = pd.Series(states).value_counts().to_dict()
    return {k: int(c.get(k, 0)) for k in ("in_stock", "carried", "unknown", "unavailable")}


def merge_lazy(rows, extra):
    """Fill placeholder rows (dicts) in place from `extra` (rows parsed from the lazily loaded half of the
    page). Only placeholders are touched, and only with data that is actually there. → number of rows priced."""
    got = {r["asin"]: r for r in extra if not is_placeholder(r)}
    filled = 0
    for r in rows:
        src = got.get(r["asin"])
        if src is None or not is_placeholder(r):
            continue
        for k in ("price_inr", "price_max_inr", "mrp_inr", "rating", "reviews"):
            if _has(src.get(k)):
                r[k] = src[k]
        if not r.get("title") and src.get("title"):
            r["title"] = src["title"]
        if _has(r.get("price_inr")):
            filled += 1
    return filled


def lazy_urls(url):
    """Candidate below-the-fold request forms (unverified against live Amazon — the probe decides)."""
    sep = "&" if "?" in url else "?"
    return [f"{url}{sep}isAboveTheFold=0&ajax=1", f"{url}{sep}isAboveTheFold=0"]
