"""
YouTube Data API v3 check (official, free quota 10,000 units/day). Optional: runs only if
YOUTUBE_API_KEY is set (Google Cloud console → enable "YouTube Data API v3" → API key).

Cost per product: search.list = 100 units + videos.list = 1 unit → top 15 products ≈ 1,515 units/day.

What it measures (and why it is NOT buying intent on its own):
  * queries are "<product> review" and "<product> unboxing" only (intent-flavoured, not memes)
  * view VELOCITY = views / days since upload, for videos from the last 30 days
  * like rate = likes / views (YouTube hides dislikes since 2021, so no true sentiment ratio)
  * negative-title share: titles with "fake", "waste", "scam", "explod", "don't buy"… — a viral
    "this exploded" video inflates velocity while killing demand
  * top-3 video links: WATCH THEM before spending on stock. The number cannot tell you tone.
Keyword extraction from Amazon titles is crude (first few words, brand kept) — check the query
column and correct it in YT_HISTORY notes if it's off.
"""
import os, re, time
from datetime import datetime, timedelta, timezone

API = "https://www.googleapis.com/youtube/v3"
NEG = re.compile(r"\b(fake|waste|scam|explod\w*|fire|danger\w*|don'?t buy|worst|useless|fraud|"
                 r"broke|broken|not working|cheap quality|bekaar|bakwas|mat lena|dhoka)\b", re.I)
STOP = re.compile(r"\b(for|with|and|the|of|in|to|by|men|women|pack|set|combo|new|latest|"
                  r"\d+\s*(ml|g|gm|kg|l|pcs|pieces|inch|cm|mm|w|mah))\b", re.I)


def keyword(title, words=4):
    t = re.split(r"[|,(\[\-–:]", str(title))[0]
    t = STOP.sub(" ", t)
    toks = [w for w in re.findall(r"[A-Za-z0-9]+", t) if len(w) > 1]
    return " ".join(toks[:words])


def _get(session, path, params):
    for attempt in range(3):
        r = session.get(f"{API}/{path}", params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403 and "quota" in r.text.lower():
            raise RuntimeError("YouTube quota exceeded for today")
        time.sleep(30 * (attempt + 1) if r.status_code == 429 else 3 * (attempt + 1))   # 429 = slow down
    r.raise_for_status()


def check_products(products, api_key, session=None, max_products=15, now=None):
    """products: list of (asin, title). Returns list of dict rows."""
    import requests
    s = session or requests.Session()
    now = now or datetime.now(timezone.utc)
    after = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for asin, title in products[:max_products]:
        kw = keyword(title)
        if not kw:
            continue
        ids, titles = [], {}
        for suffix in ("review", "unboxing"):
            j = _get(s, "search", {"key": api_key, "part": "snippet", "q": f"{kw} {suffix}", "type": "video",
                                   "regionCode": "IN", "publishedAfter": after, "maxResults": 10,
                                   "order": "relevance"})
            for it in j.get("items", []):
                vid = it["id"].get("videoId")
                if vid and vid not in titles:
                    ids.append(vid); titles[vid] = it["snippet"]["title"]
            if suffix == "review" and len(ids) >= 8:       # save 100 units when review alone is enough
                break
        if not ids:
            rows.append({"asin": asin, "query": kw, "videos_30d": 0, "views_per_day": 0, "like_rate": None,
                         "negative_title_share": None, "top_videos": "", "verdict": "No recent review videos"})
            continue
        j = _get(s, "videos", {"key": api_key, "part": "statistics,snippet", "id": ",".join(ids[:50])})
        vids = []
        for it in j.get("items", []):
            st = it.get("statistics", {})
            pub = datetime.strptime(it["snippet"]["publishedAt"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            age = max((now - pub).total_seconds() / 86400, 1)
            views = int(st.get("viewCount", 0)); likes = int(st.get("likeCount", 0)) if "likeCount" in st else None
            vids.append({"id": it["id"], "title": it["snippet"]["title"], "views": views,
                         "vpd": views / age, "likes": likes})
        vids.sort(key=lambda v: v["vpd"], reverse=True)
        tot_views = sum(v["views"] for v in vids)
        liked = [v for v in vids if v["likes"] is not None]
        like_rate = (sum(v["likes"] for v in liked) / max(sum(v["views"] for v in liked), 1)) if liked else None
        # words that are part of the product's own name ("Fake Lizards") are not negative buzz
        own = {w.lower() for w in re.findall(r"[A-Za-z']+", f"{title} {kw}")}
        def _neg(t):
            return any(m.group(0).lower() not in own for m in NEG.finditer(t))
        neg = sum(_neg(v["title"]) for v in vids) / len(vids)
        vpd = sum(v["vpd"] for v in vids)
        verdict = ("⚠ Negative buzz — watch before buying" if neg >= 0.3 else
                   "Active review interest" if vpd >= 1000 else
                   "Some review interest" if vpd >= 100 else "Low video interest")
        rows.append({"asin": asin, "query": kw, "videos_30d": len(vids), "views_per_day": round(vpd),
                     "total_views": tot_views, "like_rate": round(like_rate, 4) if like_rate is not None else None,
                     "negative_title_share": round(neg, 2),
                     "top_videos": " | ".join(f"https://youtu.be/{v['id']}" for v in vids[:3]),
                     "verdict": verdict})
    return rows
