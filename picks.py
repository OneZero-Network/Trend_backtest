"""
Affiliate picks + forward test.

Every published pick is a dated prediction. Alongside each pick we record BASELINE products
drawn at random from the same category's bestseller list on the same day. Both are tracked
for 7 / 14 / 28 days. If picks don't beat the baseline, the trend score isn't working —
whatever the affiliate clicks say.

Pre-registered hit definition (do not change after seeing results):
  HIT at horizon h  =  product is still in its category's top-100 at pick_date + h days
                       AND its best rank that day is ≤ its rank on the pick date.
  Not observed (we have no history for that day yet) → pending, not a miss.

Posts deliberately contain NO prices (Amazon Associates rules restrict how prices may be shown
without the official API) and always end with the Associates disclosure line.
"""
import random
from datetime import datetime, timedelta
import pandas as pd

HORIZONS = (7, 14, 28)
BASELINE_PER_PICK = 3
MAX_PER_CATEGORY = 2
DISCLOSURE = "#ad · As an Amazon Associate I earn from qualifying purchases."


def affiliate_link(asin, tag):
    return f"https://www.amazon.in/dp/{asin}" + (f"?tag={tag}" if tag else "")


def select_picks(signals, n=8, max_per_cat=MAX_PER_CATEGORY):
    """Top actionable, organic picks with category diversity. Seasonal is allowed (no stock risk
    for affiliates) but labelled."""
    s = signals.copy()
    for col, bad in (("actionable", False), ("discount_driven", True), ("short_dip", True)):
        if col in s:
            s = s[s[col].astype(str).str.lower() != str(bad).lower()]
    s = s[s["title"].astype(str).str.len() > 3]
    s = s.sort_values("trend_score", key=lambda x: pd.to_numeric(x, errors="coerce"), ascending=False)
    out, per = [], {}
    for _, r in s.iterrows():
        cat = r.get("category_slug") or r.get("category")
        if per.get(cat, 0) >= max_per_cat:
            continue
        per[cat] = per.get(cat, 0) + 1
        out.append(r)
        if len(out) >= n:
            break
    return pd.DataFrame(out)


def best_ranks(hist):
    """(asin, date) → best bestseller rank that day, plus the set of (category, date) fetched."""
    bs = hist[hist["list"] == "bestsellers"].copy()
    bs["rank"] = pd.to_numeric(bs["rank"], errors="coerce")
    ranks = bs.groupby(["asin", "date"])["rank"].min().to_dict()
    fetched = set(zip(bs["category_slug"], bs["date"]))
    return ranks, fetched


def make_rows(picks, hist, today, seed=None):
    """Pick rows + same-category random baseline rows (deterministic per day)."""
    ranks, _ = best_ranks(hist)
    bs = hist[(hist["list"] == "bestsellers") & (hist["date"] == today)]
    rng = random.Random(seed if seed is not None else today)
    chosen = set(picks["asin"])
    rows = []
    for _, p in picks.iterrows():
        cat = p.get("category_slug") or p.get("category")
        r0 = ranks.get((p["asin"], today))
        rows.append({"pick_date": today, "role": "pick", "asin": p["asin"], "category_slug": cat,
                     "title": str(p["title"])[:120], "trend_score": p.get("trend_score"), "rank_at_pick": r0})
        pool = sorted(set(bs.loc[bs["category_slug"] == cat, "asin"]) - chosen)
        for b in rng.sample(pool, min(BASELINE_PER_PICK, len(pool))):
            chosen.add(b)
            rows.append({"pick_date": today, "role": "baseline", "asin": b, "category_slug": cat,
                         "title": "", "trend_score": "", "rank_at_pick": ranks.get((b, today))})
    return pd.DataFrame(rows)


def update_tracking(tracker, hist):
    """Fill rank_d7/d14/d28 and hit_d7/... where the horizon date has been observed."""
    if tracker.empty:
        return tracker
    t = tracker.copy()
    ranks, fetched = best_ranks(hist)
    for h in HORIZONS:
        rk, hit = [], []
        for _, r in t.iterrows():
            d = (datetime.strptime(str(r["pick_date"])[:10], "%Y-%m-%d") + timedelta(days=h)).strftime("%Y-%m-%d")
            if (r["category_slug"], d) not in fetched:           # day not observed (yet) → pending
                rk.append(r.get(f"rank_d{h}", "")); hit.append(r.get(f"hit_d{h}", ""))
                continue
            now = ranks.get((r["asin"], d))
            r0 = pd.to_numeric(r["rank_at_pick"], errors="coerce")
            rk.append(now if now is not None else "out")
            hit.append(int(now is not None and pd.notna(r0) and now <= r0))
        t[f"rank_d{h}"], t[f"hit_d{h}"] = rk, hit
    return t


def scorecard(tracker):
    """Hit rate of picks vs baseline per horizon (only resolved rows)."""
    rows = []
    for h in HORIZONS:
        col = f"hit_d{h}"
        if col not in tracker:
            continue
        d = tracker[pd.to_numeric(tracker[col], errors="coerce").notna()]
        for role in ("pick", "baseline"):
            x = pd.to_numeric(d.loc[d["role"] == role, col], errors="coerce")
            rows.append({"horizon_days": h, "role": role, "resolved": int(len(x)),
                         "hits": int(x.sum()) if len(x) else 0,
                         "hit_rate": round(float(x.mean()), 3) if len(x) else None})
    sc = pd.DataFrame(rows)
    return sc


def verdict(sc, min_resolved=40):
    """Plain-language reading of the scorecard (no p-hacking: one horizon, decided in advance = 14d)."""
    d = sc[sc["horizon_days"] == 14]
    if d.empty:
        return "Forward test: no 14-day results yet."
    p = d[d["role"] == "pick"].iloc[0]
    b = d[d["role"] == "baseline"].iloc[0]
    if p["resolved"] < min_resolved:
        return (f"Forward test (14d): {p['resolved']}/{min_resolved} picks resolved — too early to judge "
                f"(picks {p['hit_rate'] if p['hit_rate'] is not None else '–'} vs random {b['hit_rate'] if b['hit_rate'] is not None else '–'}).")
    lift = (p["hit_rate"] / b["hit_rate"]) if b["hit_rate"] else None
    if lift is None:
        return "Forward test (14d): baseline has no hits — cannot compute lift yet."
    tone = ("picks beat random" if lift >= 1.3 else "no clear edge over random" if lift >= 0.9 else
            "picks do WORSE than random")
    return f"Forward test (14d): picks {p['hit_rate']:.0%} vs random {b['hit_rate']:.0%} → {lift:.2f}× ({tone})."


def post_text(picks, tag, week_label, scorecard_line=""):
    lines = [f"🔍 Hidden Amazon Finds — {week_label}", "Products climbing Amazon.in's bestseller lists this week:", ""]
    for i, (_, p) in enumerate(picks.iterrows(), 1):
        seas = f" ({p['seasonal'].replace('SEASONAL: ', '')})" if str(p.get("seasonal", "")).startswith("SEASONAL") else ""
        lines.append(f"{i}. {str(p['title'])[:90]}{seas}\n   {affiliate_link(p['asin'], tag)}")
    lines += ["", DISCLOSURE]
    if scorecard_line:
        lines += ["", scorecard_line]
    return "\n".join(lines)


# ── one message per product (v1.8) ───────────────────────────────────
SHORT_AD = "#ad · Amazon Associate link"


def trend_facts(asin, cat, hist, today, lookback=7):
    """Why this product is in the post — from OUR rank history only (no prices, no scraped claims).
    → dict(rank_now, rank_then, days_listed, window)"""
    bs = hist[(hist["list"] == "bestsellers") & (hist["category_slug"] == cat)].copy()
    bs["rank"] = pd.to_numeric(bs["rank"], errors="coerce")
    days = sorted(d for d in bs["date"].unique() if d <= today)
    window = days[-(lookback + 1):]
    mine = bs[bs["asin"] == asin].groupby("date")["rank"].min()
    rank_now = mine.get(today)
    rank_then = mine.get(window[0]) if window and window[0] != today else None
    listed = int(sum(d in mine.index for d in window[1:])) if len(window) > 1 else int(today in mine.index)
    return {"rank_now": rank_now, "rank_then": rank_then, "days_listed": listed,
            "window": max(len(window) - 1, 1)}


def trend_line(f, cat_name):
    now, then = f["rank_now"], f["rank_then"]
    if now is None or pd.isna(now):
        head = f"Trending in {cat_name}"
    elif then is None or pd.isna(then):
        head = f"New in the {cat_name} top 100 — now <b>#{int(now)}</b>"
    elif then > now:
        head = f"Climbed <b>#{int(then)} → #{int(now)}</b> in {cat_name} this week"
    else:
        head = f"Holding <b>#{int(now)}</b> in {cat_name}"
    return f"📈 {head} · on the list {f['days_listed']} of the last {f['window']} days"


def _h(x):
    import html
    return html.escape(str(x), quote=False)


def post_messages(picks_df, hist, tag, today, cat_names=None, week_label=""):
    """→ [header, msg1, msg2, ...] in Telegram HTML. Biggest climbers first."""
    cat_names = cat_names or {}
    items = []
    for _, p in picks_df.iterrows():
        cat = p.get("category_slug") or p.get("category")
        f = trend_facts(p["asin"], cat, hist, today)
        climb = (f["rank_then"] - f["rank_now"]) if f["rank_then"] is not None and f["rank_now"] is not None \
            and not pd.isna(f["rank_then"]) and not pd.isna(f["rank_now"]) else -1
        items.append((climb, p, f, cat_names.get(cat, cat)))
    items.sort(key=lambda x: x[0], reverse=True)
    header = (f"🔍 <b>Hidden Amazon Finds</b> — {_h(week_label)}\n"
              f"{len(items)} products climbing Amazon.in bestseller lists, picked from daily rank tracking.\n\n"
              f"<i>{_h(DISCLOSURE)}</i>")
    msgs = [header]
    for _, p, f, cname in items:
        seas = str(p.get("seasonal", ""))
        seas_line = f"\n📅 Seasonal: {_h(seas.replace('SEASONAL: ', ''))}" if seas.startswith("SEASONAL") else ""
        msgs.append(f"<b>{_h(str(p['title'])[:110])}</b>\n\n{trend_line(f, _h(cname))}{seas_line}\n\n"
                    f"{affiliate_link(p['asin'], tag)}\n\n<i>{SHORT_AD}</i>")
    return msgs


# ── v1.11: X + WhatsApp share kit ─────────────────────────────────────
from urllib.parse import quote as _q
X_URL_LEN = 23          # X counts every link as 23 characters
X_LIMIT = 280


def _plain_line(f, cname):
    now, then = f["rank_now"], f["rank_then"]
    if now is None or pd.isna(now):
        return f"Trending in {cname}"
    if then is None or pd.isna(then):
        return f"New in the {cname} top 100 (now #{int(now)})"
    if then > now:
        return f"Climbed #{int(then)} → #{int(now)} in {cname} this week"
    return f"Holding #{int(now)} in {cname}"


def x_weight(text):
    """Approximate X length: links count 23; characters outside basic Latin count 2 (emoji, ₹, →)."""
    import re
    n = 0
    for part in re.split(r"(https?://\S+)", text):
        if part.startswith("http"):
            n += X_URL_LEN
        else:
            n += sum(1 if ord(ch) < 0x1100 else 2 for ch in part)
    return n


def x_text(p, f, cname, tag):
    link = affiliate_link(p["asin"], tag)
    line = _plain_line(f, cname)
    title = str(p["title"])
    for cut in range(len(title), 10, -5):              # shorten the title until it fits
        t = title[:cut].rstrip(" ,|-") + ("…" if cut < len(title) else "")
        txt = f"{t}\n\n📈 {line}\n\n{link}\n\n#ad #AmazonFinds"
        if x_weight(txt) <= X_LIMIT:
            return txt
    return f"📈 {line}\n\n{link}\n\n#ad"


def wa_text(p, f, cname, tag):
    """WhatsApp formatting: *bold*, plain link (WhatsApp shows the preview)."""
    seas = str(p.get("seasonal", ""))
    s = f"\n📅 Seasonal: {seas.replace('SEASONAL: ', '')}" if seas.startswith("SEASONAL") else ""
    return (f"*{str(p['title'])[:110]}*\n\n📈 {_plain_line(f, cname)}{s}\n\n{affiliate_link(p['asin'], tag)}\n\n"
            f"_#ad · Amazon Associate link_")


def share_kit(picks_df, hist, tag, today, cat_names=None):
    """→ list of dicts per pick: x_text, wa_text, x_intent (one-tap tweet), wa_share (one-tap WhatsApp)."""
    out = []
    for _, p in picks_df.iterrows():
        cat = p.get("category_slug") or p.get("category")
        f = trend_facts(p["asin"], cat, hist, today)
        cname = (cat_names or {}).get(cat, cat)
        xt, wt = x_text(p, f, cname, tag), wa_text(p, f, cname, tag)
        out.append({"asin": p["asin"], "x_text": xt, "wa_text": wt,
                    "x_intent": "https://twitter.com/intent/tweet?text=" + _q(xt),
                    "wa_share": "https://wa.me/?text=" + _q(wt)})
    return out


def kit_message(i, k):
    """Admin-chat message with one-tap buttons (Telegram HTML)."""
    return (f"<b>{i}.</b> {_h(k['x_text'].split(chr(10))[0][:80])}\n"
            f"<a href=\"{_h(k['x_intent'])}\">𝕏 Post on X</a>  ·  <a href=\"{_h(k['wa_share'])}\">🟢 Share on WhatsApp</a>")


def post_to_x(kit, creds, max_posts, client=None):
    """Optional paid auto-posting (X API pay-per-use). Returns [(asin, tweet_id or error)].
    Posts at most max_posts — your spend cap."""
    if client is None:
        import tweepy
        client = tweepy.Client(consumer_key=creds["api_key"], consumer_secret=creds["api_secret"],
                               access_token=creds["access_token"], access_token_secret=creds["access_secret"])
    done = []
    for k in kit[:max_posts]:
        try:
            r = client.create_tweet(text=k["x_text"])
            done.append((k["asin"], str((r.data or {}).get("id", ""))))
        except Exception as exc:
            done.append((k["asin"], f"ERROR {type(exc).__name__}: {str(exc)[:120]}"))
            if "402" in str(exc) or "403" in str(exc):     # billing / permission problem → stop, don't burn calls
                break
    return done
