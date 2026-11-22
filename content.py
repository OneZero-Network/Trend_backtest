"""
Content agent — v1.60. Turns the day's picks into channel-specific posts that people can forward, and keeps every
number in them provable.

  telegram_footer   forward line + named join link (tg-forward) under every Telegram post
  wa_footer         WhatsApp channel link + its own Telegram join link (wa-channel)
  x_linkfree        an X post with NO link, from rotating templates (X forbids near-duplicate posts)
  roundup           Sunday "lasting bestsellers" post: the week's most durable, in-stock, not-falling products
  poll              a weekly Telegram poll built from today's pool (polls get answered and forwarded)
  CopyAgent         OPTIONAL Claude rewrite of an X hook for variety. Off unless AFF_COPY_AGENT=claude and
                    ANTHROPIC_API_KEY are set. Every draft passes `validate`: no number that is not in our facts,
                    no link in link-free mode, no hype words, within X's length. A rejected draft falls back to the
                    template, so the agent can only ever make a post more varied, never less true.
"""
import hashlib
import json
import os
import re

import pandas as pd

BANNED = re.compile(r"\b(best|cheapest|lowest price|guaranteed?|viral|hurry|last chance|limited time|steal|"
                    r"must[- ]buy|will (?:sell out|go up)|price drop|deal of the day|\d+% off)\b", re.I)
NUM_RX = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _h(x):
    import html
    return html.escape(str(x), quote=False)


def _pick(seed, n):
    """Deterministic rotation: the same day, slot and product always get the same variant; different ones differ."""
    return int(hashlib.sha256(str(seed).encode()).hexdigest(), 16) % max(n, 1)


# ── footers: the share loop ──────────────────────────────────────────
def telegram_footer(links):
    """links: {name: url} from GROWTH_LINKS. The named link counts joins that came from forwards."""
    if (os.environ.get("AFF_SHARE_FOOTER") or "1") == "0":         # one switch if you want the posts shorter again
        return ""
    url = (links or {}).get("tg-forward", "")
    line = "📣 Know someone shopping this week? Forward this."
    return line + (f"\nJoin: {_h(url)}" if url else "")


def wa_footer(links, wa_channel_url=""):
    parts = ["📣 Forward to a friend who is shopping."]
    if wa_channel_url:
        parts.append(f"Follow: {wa_channel_url}")
    if (links or {}).get("wa-channel"):
        parts.append(f"Daily list on Telegram: {links['wa-channel']}")
    return "\n".join(parts)


# ── X: link-free posts ───────────────────────────────────────────────
# "Amazon India", never "Amazon.in": X turns a bare domain into a link, and a post with a link costs ~13x more.
_X_DURABLE = [
    "{title}\n\n🏆 #{now} in {cat} · {days} on Amazon India's bestseller list.\n\nA daily list of products that last: link in bio.",
    "Still there after {window} days: {title}.\n\n🏆 #{now} in {cat} · {days} on the bestseller list.\n\nMore like this every day, link in bio.",
    "{title}\n\nOn Amazon India's {cat} bestseller list {days_short}. Now #{now}.\n\nWe track these daily: link in bio.",
    "Not a one-day spike: {title}.\n\n#{now} in {cat}, {days} on the list.\n\nThe full daily list is linked in our bio.",
]
_X_MOMENTUM = [
    "{title}\n\n📈 #{then} → #{now} in {cat} this week on Amazon India's bestseller list.\n\nMore climbers daily: link in bio.",
    "Climbing: {title}.\n\n📈 From #{then} to #{now} in {cat} in a week.\n\nWe track the lists every day, link in bio.",
    "{title} moved from #{then} to #{now} in {cat} this week.\n\nDaily movers on our channel: link in bio.",
]
_X_NEW = [
    "New on Amazon India's {cat} bestseller list: {title}, at #{now}.\n\nDaily list: link in bio.",
]


def _facts_vars(f, cname, title):
    now, then = f.get("rank_now"), f.get("rank_then")
    v = {"title": title, "cat": cname, "now": "", "then": ""}
    if now is not None and not pd.isna(now):
        v["now"] = int(now)
    if then is not None and not pd.isna(then):
        v["then"] = int(then)
    if f.get("arm") == "durable" and f.get("dur_window"):
        d, w = int(f["dur_days"]), int(f["dur_window"])
        v["days"] = f"{d} days" if d == w else f"{d} of {w} days"
        v["days_short"] = f"for {d} days running" if d == w else f"on {d} of the last {w} days"
        v["window"] = w
    return v


def x_linkfree(p, f, cname, seed, weight=None, limit=280):
    """Link-free X text for one pick. → (text, variant_id). Returns ('', '') when the facts cannot support a claim."""
    weight = weight or (lambda s: len(s))
    title_full = re.split(r"\s[|–]\s|,\s|\s\(", str(p.get("title", "")))[0].strip()
    v = _facts_vars(f, cname, title_full)
    if f.get("arm") == "durable" and "days" in v and v["now"] != "":
        pool, kind = _X_DURABLE, "d"
    elif v["now"] != "" and v["then"] != "" and v["then"] > v["now"]:
        pool, kind = _X_MOMENTUM, "m"
    elif v["now"] != "" and v["then"] == "":
        pool, kind = _X_NEW, "n"
    else:
        return "", ""
    i = _pick(seed, len(pool))
    for cut in range(len(title_full), 8, -4):
        v["title"] = title_full[:cut].rstrip(" ,|-") + ("…" if cut < len(title_full) else "")
        text = pool[i].format(**v)
        if weight(text) <= limit:
            return text, f"{kind}{i}"
    return "", ""


# ── optional copy agent ─────────────────────────────────────────────
def validate(text, facts_text, linkfree=True, weight=None, limit=280):
    """→ '' when the draft is acceptable, else the reason it is not."""
    if not text or not text.strip():
        return "empty"
    weight = weight or (lambda s: len(s))
    if weight(text) > limit:
        return "too long"
    if linkfree and re.search(r"(https?://|www\.|t\.me/)", text, re.I):
        return "contains a link"
    if BANNED.search(text):
        return f"hype word: {BANNED.search(text).group(0)}"
    allowed = {n.replace(",", "") for n in NUM_RX.findall(facts_text)}
    extra = [n for n in NUM_RX.findall(text) if n.replace(",", "") not in allowed]
    if extra:
        return f"number not in our data: {extra[0]}"
    if "@" in text:
        return "mentions an account"
    if "#" in re.sub(r"#\d", "", text):
        return "hashtag"
    return ""


class CopyAgent:
    """Claude rewrite of a link-free X draft. Off by default; capped per month; every output validated."""
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, http_post=None):
        self.on = (os.environ.get("AFF_COPY_AGENT") or "").strip().lower() == "claude" and bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.model = os.environ.get("AFF_COPY_MODEL") or "claude-haiku-5-5"
        self.max_per_month = int(os.environ.get("AFF_COPY_MAX_PER_MONTH") or 300)
        self.http_post = http_post

    def rewrite(self, draft, facts, used_this_month=0, weight=None):
        """→ (text, source) where source is 'template' | 'claude' | 'claude-rejected: <reason>' | 'claude-error'."""
        if not self.on or used_this_month >= self.max_per_month or not draft:
            return draft, "template"
        post = self.http_post
        if post is None:
            import requests
            post = requests.post
        system = ("You rewrite one short social post for an Indian shopping channel. Keep every fact exactly; use only "
                  "numbers that appear in FACTS; no links, no hashtags, no @mentions, no prices, no discounts, no hype "
                  "(best, viral, hurry, must-buy), no forecasts. Plain friendly English, under 240 characters. "
                  "Reply with the post text only.")
        user = f"FACTS: {json.dumps(facts, ensure_ascii=False)}\nDRAFT: {draft}"
        try:
            r = post(self.URL, headers={"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                                        "anthropic-version": "2023-06-01", "content-type": "application/json"},
                     data=json.dumps({"model": self.model, "max_tokens": 200, "system": system,
                                      "messages": [{"role": "user", "content": user}]}), timeout=30)
            body = r.json()
            text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text").strip()
        except Exception:
            return draft, "claude-error"
        why = validate(text, json.dumps(facts, ensure_ascii=False) + " " + draft, linkfree=True, weight=weight)
        return (text, "claude") if not why else (draft, f"claude-rejected: {why}")


# ── weekly formats ──────────────────────────────────────────────────
def roundup(signals, tag_fn, link_fn, cat_names, title, links=None, n=5, disclosure=""):
    """Sunday post: the most durable products on the lists this week — one per category, in stock, not falling,
    a real product. Claim = '<N> days on the list', nothing else. → HTML text or ''."""
    s = signals.copy()
    if "durable" not in s:
        return ""
    s = s[s["durable"].astype(str).str.lower() == "true"]
    for col, bad in (("exit_flag", "falling"), ("stock_state", "unavailable"), ("stock_state", "unknown")):
        if col in s:
            s = s[s[col].fillna("").astype(str) != bad]
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    if "compliance" in s:
        s = s[~s["compliance"].astype(str).str.contains("EXPLOSIVES")]
    if s.empty:
        return ""
    s = s.assign(_d=pd.to_numeric(s.get("durable_score"), errors="coerce")).sort_values("_d", ascending=False)
    rows, seen = [], set()
    for _, r in s.iterrows():
        cat = r.get("category_slug") or r.get("category")
        if cat in seen:
            continue
        seen.add(cat)
        rows.append(r)
        if len(rows) >= n:
            break
    lines = [f"🏆 <b>{_h(title)}</b> — the products that stayed on Amazon India's bestseller lists all week", ""]
    for i, r in enumerate(rows, 1):
        cat = r.get("category_slug") or r.get("category")
        d, w = r.get("durable_days"), r.get("durable_window")
        try:
            d, w = int(float(d)), int(float(w))
            days = f"{d} days on the list" if d == w else f"{d} of {w} days on the list"
        except (TypeError, ValueError):
            continue
        short = re.split(r"\s[|–]\s|,\s|\s\(", str(r["title"]))[0].strip()[:60]
        lines.append(f"{i}. <a href=\"{_h(link_fn(r['asin'], tag_fn(r)))}\">{_h(short)}</a>\n"
                     f"    🏆 {_h((cat_names or {}).get(cat, cat))} · {days}")
    if len(lines) <= 2:
        return ""
    lines += ["", telegram_footer(links)]
    if disclosure:
        lines += ["", f"<i>{_h(disclosure)}</i>"]
    return "\n".join(lines)


def poll(pool_today, question="Which of today's picks would you actually buy?"):
    """A Telegram poll from today's pool: 2–4 short product names (≤100 chars each). → dict for sendPoll or None."""
    if pool_today is None or len(pool_today) < 2:
        return None
    opts, seen = [], set()
    for _, r in pool_today.iterrows():
        name = re.split(r"\s[|–]\s|,\s|\s\(", str(r.get("title", "")))[0].strip()[:90]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            opts.append(name)
        if len(opts) == 4:
            break
    if len(opts) < 2:
        return None
    return {"question": question[:300], "options": opts, "is_anonymous": True}
