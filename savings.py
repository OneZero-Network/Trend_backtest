"""
Savings & cards funnel — v1.80. Non-Amazon offers that pay more per conversion, posted beside the Amazon picks.

  /deal <link> <one line>                   a Myntra / Ajio / Flipkart offer as an EarnKaro (or other) affiliate link
  /card <link> <card name> | <one line>     a bank credit-card referral (EarnKaro lists ₹350–₹2,975 per activated card)

Why the limits (each is a rule, not a suggestion):
  * The channel is built on "what lasts on Amazon". Card posts can cost members, so: at most CARDS_PER_WEEK (default 2),
    never on consecutive days, and an arm that posted in the 3 days before members fell 3 nights running is paused
    for 7 days automatically.
  * Cards are judged per POST, not per conversion. A card pays on activation and EarnKaro confirms within 75 days of
    it; most clicks never become an activated card. The Operator compares earnings per post across arms.
  * ASCI: an unqualified creator may share generic information about a financial product, not technical advice, and
    the ad label must come first. So every post opens with the disclosure, card posts carry a fixed "the bank decides"
    line, and phrases that promise approval or rank cards are refused.
  * Only links on SAVINGS_DOMAINS are accepted (default EarnKaro's), so a mistyped or non-affiliate link never posts.
EarnKaro has no public link API: the owner makes the link in EarnKaro and sends it to the bot. That is the manual step.
"""
import hashlib
import html
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pandas as pd

DOMAINS = [d.strip().lower() for d in (os.environ.get("SAVINGS_DOMAINS") or "ekaro.in,earnkaro.com").split(",") if d.strip()]
SLOTS = [int(h) for h in (os.environ.get("SAVINGS_SLOTS") or "13,19").split(",") if h.strip()]
PER_DAY = int(os.environ.get("SAVINGS_PER_DAY") or 2)
CARDS_PER_WEEK = int(os.environ.get("CARDS_PER_WEEK") or 2)
PAUSE_DAYS = 7
COLS = ["id", "kind", "url", "card", "text", "added", "status", "posted_at", "key"]

BANNED = re.compile(r"\b(best|cheapest|guaranteed?|viral|hurry|last chance|must[- ]buy|steal|loot)\b", re.I)
CARD_BANNED = re.compile(r"\b(guaranteed?|instant approval|pre-?approved|no documents?|no income proof|without (any )?documents?|"
                         r"free money|100% approval|approval assured|best (credit )?card|lifetime free|no questions)\b", re.I)
DISCLOSE = {"savings": "#Ad · affiliate link: we earn a commission if you buy.",
            "cards": "#Ad · referral link: we earn if the bank issues and activates your card."}
CARD_NOTE = ("Approval, fees and eligibility are the bank's decision. Read the terms on the bank's page before you apply. "
             "This is not financial advice.")

H = lambda x: html.escape(str(x if x is not None else ""), quote=False)


def frame(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.copy().astype(object)
    for c in COLS:
        if c not in df:
            df[c] = ""
    return df[COLS].fillna("").astype(str)


def _domain_ok(url):
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return False
    return url.lower().startswith("https://") and any(host == d or host.endswith("." + d) for d in DOMAINS)


def parse(kind, args):
    """kind 'deal' | 'card', args after the command. → (item dict, '') or (None, why)."""
    if not args:
        return None, ("Use: /deal <EarnKaro link> <one line about the offer>" if kind == "deal"
                      else "Use: /card <EarnKaro link> <card name> | <one line from the bank's page>")
    url, rest = args[0], " ".join(args[1:]).strip()
    if not _domain_ok(url):
        return None, (f"Only https links on {', '.join(DOMAINS)} are posted. If this is your affiliate link on another "
                      f"domain, add the domain to SAVINGS_DOMAINS.")
    card, text = "", rest
    if kind == "card":
        if "|" not in rest:
            return None, "Use: /card <link> <card name> | <one line from the bank's page>"
        card, text = (x.strip() for x in rest.split("|", 1))
        if not card:
            return None, "Name the card before the |"
        bad = CARD_BANNED.search(card + " " + text)
        if bad:
            return None, f"Not posted: '{bad.group(0)}' promises approval or ranks cards. State what the bank's page says."
    if not text or len(text) < 8:
        return None, "Add one line about the offer (what it is, from the store's or bank's page)."
    if len(text) > 280:
        return None, "Keep the line under 280 characters."
    bad = BANNED.search(text)
    if bad:
        return None, f"Not posted: hype word '{bad.group(0)}'. Say what the offer is."
    if re.search(r"https?://", text):
        return None, "Put only one link — the first word."
    k = "cards" if kind == "card" else "savings"
    iid = "S" + hashlib.sha1(url.encode()).hexdigest()[:5]
    return {"id": iid, "kind": k, "url": url, "card": card[:60], "text": text, "status": "queued"}, ""


def enqueue(queue, item, today):
    """Same link within 7 days → refused (repeats look like spam). → (queue, reply)."""
    q = frame(queue)
    recent = q[(q["url"] == item["url"]) & (q["added"] >= (pd.Timestamp(str(today)) - pd.Timedelta(days=7)).strftime("%Y-%m-%d"))]
    if len(recent):
        return q, f"That link was already queued on {recent.iloc[-1]['added']}."
    row = {**{c: "" for c in COLS}, **item, "added": str(today)}
    q = pd.concat([q, pd.DataFrame([row])], ignore_index=True)
    waiting = int((q["status"] == "queued").sum())
    return q, (f"Queued {item['kind']} {item['id']} ({waiting} waiting). Posts go out at "
               + ", ".join(f"{h}:00" for h in SLOTS) + (f"; cards at most {CARDS_PER_WEEK} a week." if item["kind"] == "cards" else "."))


def paused(state, arm, now):
    try:
        until = (state or {}).get(f"pause_{arm}", "")
        return bool(until) and str(now)[:10] <= until
    except Exception:
        return False


def due(queue, post_log, now, state=None):
    """→ the queued item to post at this run, or None. One non-Amazon post per slot, PER_DAY a day."""
    q = frame(queue)
    if now.hour not in SLOTS or q.empty:
        return None
    pl = post_log if post_log is not None and len(post_log) else pd.DataFrame(columns=["posted_at", "arm"])
    arm = pl.get("arm", pd.Series("", index=pl.index)).astype(str)
    day = now.strftime("%Y-%m-%d")
    mine = pl[arm.isin(["savings", "cards"])]
    if (mine["posted_at"].astype(str).str[:10] == day).sum() >= PER_DAY:
        return None
    if (mine["posted_at"].astype(str).str[:13] == now.strftime("%Y-%m-%d %H")).any():
        return None
    cards = pl[arm == "cards"]["posted_at"].astype(str)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    card_ok = ((cards.str[:10] > week_ago).sum() < CARDS_PER_WEEK and not (cards.str[:10] >= yday).any()
               and not paused(state, "cards", day) and now.hour == max(SLOTS))
    waiting = q[q["status"] == "queued"].sort_values("added", kind="stable")
    c = waiting[waiting["kind"] == "cards"]
    if card_ok and len(c):
        return c.iloc[0].to_dict()
    d = waiting[waiting["kind"] == "savings"]
    if len(d) and not paused(state, "savings", day):
        return d.iloc[0].to_dict()
    return None


def message(item, footer=""):
    lines = [f"<i>{H(DISCLOSE[item['kind']])}</i>", ""]
    if item["kind"] == "cards":
        lines += [f"💳 <b>{H(item['card'])}</b>", H(item["text"]), "", f"<a href=\"{H(item['url'])}\">Apply on the bank's page</a>",
                  "", f"<i>{H(CARD_NOTE)}</i>"]
    else:
        lines += [f"🛍 {H(item['text'])}", "", f"<a href=\"{H(item['url'])}\">See the offer</a>"]
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def mark_posted(queue, iid, stamp):
    q = frame(queue)
    q.loc[q["id"] == iid, ["status", "posted_at"]] = ["posted", stamp]
    return q


# ── the arms, per post ───────────────────────────────────────────────
ARM_OF_CHANNEL = {"telegram.durable": "durable", "telegram": "momentum", "ek-deals": "savings", "earnkaro": "savings",
                  "ek-cards": "cards"}
MIN_POSTS = {"durable": 20, "momentum": 20, "savings": 10, "cards": 6}


def arms(money, post_log):
    """Per arm: posts in the money rows' own date ranges, clicks, orders, earnings, earnings per post.
    money rows: from / to / channel / clicks / ordered / earnings (operator_agent.MONEY_COLS)."""
    if money is None or not len(money):
        return {}
    pl = post_log if post_log is not None and len(post_log) else pd.DataFrame(columns=["posted_at", "arm", "channel"])
    out = {}
    m = money.copy()
    m["arm"] = m["channel"].astype(str).map(ARM_OF_CHANNEL)
    m = m[m["arm"].notna()]
    for arm_, g in m.groupby("arm"):
        latest = g.sort_values(["to", "added"]).drop_duplicates(["from", "to"], keep="last")
        last = latest.iloc[-1]
        rows = g[(g["from"] == last["from"]) & (g["to"] == last["to"])]
        d = pl[(pl["posted_at"].astype(str).str[:10] >= last["from"]) & (pl["posted_at"].astype(str).str[:10] <= last["to"])]
        parm = d.get("arm", pd.Series("", index=d.index)).astype(str)
        if arm_ in ("durable", "momentum"):
            d = d[d.get("channel", pd.Series("", index=d.index)).astype(str) == "telegram"]
            parm = d.get("arm", pd.Series("", index=d.index)).astype(str)
            n = int((parm == "durable").sum()) if arm_ == "durable" else int((~parm.isin(["durable", "savings", "cards"])).sum())
        else:
            n = int((parm == arm_).sum())
        num = lambda k: float(pd.to_numeric(rows[k], errors="coerce").fillna(0).sum())
        e = num("earnings")
        out[arm_] = {"from": last["from"], "to": last["to"], "posts": n, "clicks": num("clicks"), "orders": num("ordered"),
                     "earnings": e, "per_post": (e / n if n else None)}
    return out


def arms_verdict(table):
    """Each non-Amazon arm against the Amazon durable arm, per post. Rules fixed in advance."""
    if not table:
        return []
    base = table.get("durable") or table.get("momentum")
    lines = []
    for arm_ in ("savings", "cards"):
        t = table.get(arm_)
        if not t:
            continue
        if t["posts"] < MIN_POSTS[arm_]:
            lines.append(f"{arm_}: collecting ({t['posts']} of {MIN_POSTS[arm_]} posts in {t['from']}–{t['to']})")
            continue
        if not base or not base["per_post"]:
            lines.append(f"{arm_}: ₹{t['per_post']:,.0f} per post; no Amazon report for the same dates to compare")
            continue
        ratio = t["per_post"] / base["per_post"] if base["per_post"] else None
        tag = ("earns more per post than Amazon — keep" if ratio >= 1.5 else
               "about the same as Amazon — keep only if members hold" if ratio >= 0.67 else "earns less per post than Amazon — cut it")
        lines.append(f"{arm_}: ₹{t['per_post']:,.0f} per post vs Amazon ₹{base['per_post']:,.0f} ({ratio:.1f}×) — {tag}"
                     + (" (card earnings are pending until EarnKaro confirms, within 75 days of activation)" if arm_ == "cards" else ""))
    return lines


def pause_check(audience, post_log, today, state):
    """Members fell 3 nights running → pause each non-Amazon arm that posted in the 3 days before. → (state, notes)."""
    state = dict(state or {})
    notes = []
    if not (audience or {}).get("falling3"):
        return state, notes
    pl = post_log if post_log is not None and len(post_log) else pd.DataFrame(columns=["posted_at", "arm"])
    since = (pd.Timestamp(str(today)) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    recent = pl[pl["posted_at"].astype(str).str[:10] >= since]
    arms_ = set(recent.get("arm", pd.Series(dtype=str)).astype(str)) & {"savings", "cards"}
    until = (pd.Timestamp(str(today)) + pd.Timedelta(days=PAUSE_DAYS)).strftime("%Y-%m-%d")
    for a in sorted(arms_):
        if not paused(state, a, str(today)):
            state[f"pause_{a}"] = until
            notes.append(f"⏸ {a} posts paused until {until}: members fell 3 nights running after them")
    return state, notes
