"""
Growth + measurement agent — v1.60. Counts what the plan's kill lines need, every day, and judges them weekly.

Daily (BR_MODE=growth, ~22:00 IST):
  members      Telegram getChatMemberCount for the public channel
  links        one named invite link per source (tg-forward, wa-channel, x-bio, bot, site, swap-1, swap-2), created once
               with createChatInviteLink — the bot must be a channel admin with "Invite users via link"
  joins        who joined through which link: Telegram 'chat_member' updates carry the invite link used.
               If the shopper bot's webhook is set (worker.js), the Worker counts them and serves /stats instead,
               because getUpdates stops working while a webhook is set.
  x            posts and spend from X_POST_LOG
  associates   the Associates report (sent to the bot as a CSV from v1.70, or pasted into ASSOC_REPORT)

Weekly (Mondays, or BR_GROWTH_REPORT=1): the kill lines from the plan, each with its value and status, plus the goal
worked backwards with MEASURED rates where they exist (assumptions are labelled as such until then).

Everything is written to GROWTH (one row per date · metric · key · value), so new metrics never change the layout.
"""
import math
import re
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

import pandas as pd

LINK_NAMES = ("tg-forward", "wa-channel", "x-bio", "bot", "site", "swap-1", "swap-2")
GOAL_INR = 50000
ASSUMED = {"inr_per_order": 15.0, "conversion": 0.04, "daily_click_rate": 0.02}   # v1.70: ₹15 = median fee on our picks (was ₹40)

# The plan's kill lines, decided before any data (OneZero Growth Machine — Strategic Plan). Edit here, not later.
KILL_LINES = [
    {"bet": "X link-free funnel", "metric": "joins:x-bio", "window": "since_start", "pass": 50, "fail": 50,
     "check_days": 30, "start": "x_first_post", "fail_action": "switch X off (AFF_X_MODE=off)"},
    {"bet": "Share loop", "metric": "forward_joins_per_100_members_week", "window": "last_7", "pass": 1.0, "fail": 0.2,
     "check_days": 28, "start": "first_day", "fail_action": "rework post formats"},
    {"bet": "Telegram growth", "metric": "member_growth_week_pct", "window": "last_7", "pass": 5.0, "fail": 2.0,
     "check_days": 28, "start": "first_day", "fail_action": "add swaps, change formats"},
    {"bet": "WhatsApp (assisted)", "metric": "clicks:whatsapp_month", "window": "assoc", "pass": 100, "fail": 30,
     "check_days": 56, "start": "first_day", "fail_action": "stop posting to WhatsApp"},
    {"bet": "Shopper bot", "metric": "bot_users_week", "window": "last_7", "pass": 100, "fail": 20,
     "check_days": 70, "start": "first_day", "fail_action": "keep it, stop investing"},
    {"bet": "Radar for sellers", "metric": "paying_sellers", "window": "input", "pass": 10, "fail": 3,
     "check_days": 60, "start": "pro_first", "fail_action": "stop the seller product"},
]


# ── Telegram Bot API (injectable for tests) ───────────────────────────
class TG:
    def __init__(self, token, http_post=None):
        self.token = token
        self.http_post = http_post

    def call(self, method, **params):
        post = self.http_post
        if post is None:
            import requests
            post = requests.post
        r = post(f"https://api.telegram.org/bot{self.token}/{method}", json=params, timeout=30)
        body = r.json() if hasattr(r, "json") else r
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method}: {body.get('description', body)}")
        return body.get("result")


def member_count(tg, chat):
    return int(tg.call("getChatMemberCount", chat_id=chat))


def ensure_links(tg, chat, existing, names=LINK_NAMES, today=""):
    """existing: DataFrame name, link, created (may be empty). Creates the missing named links. → (DataFrame, created)."""
    have = {} if existing is None or existing.empty else dict(zip(existing["name"].astype(str), existing["link"].astype(str)))
    rows = [{"name": n, "link": have[n], "created": ""} for n in names if n in have]
    if existing is not None and not existing.empty and "created" in existing:
        made = dict(zip(existing["name"].astype(str), existing["created"].astype(str)))
        for r in rows:
            r["created"] = made.get(r["name"], "")
    created = []
    for n in names:
        if n in have:
            continue
        res = tg.call("createChatInviteLink", chat_id=chat, name=n[:32])
        rows.append({"name": n, "link": res["invite_link"], "created": today})
        created.append(n)
    return pd.DataFrame(rows, columns=["name", "link", "created"]), created


def _is_join(old, new):
    return old in ("left", "kicked") and new in ("member", "administrator", "creator", "restricted")


def joins_from_updates(updates, chat):
    """Telegram updates → ({date: {link_name: n}}, next_offset). Only joins to `chat` count."""
    counts, offset = {}, None
    for u in updates or []:
        offset = max(offset or 0, int(u.get("update_id", 0)) + 1)
        cm = u.get("chat_member")
        if not cm or str(cm.get("chat", {}).get("id")) != str(chat) and str(cm.get("chat", {}).get("username", "")).lower() != str(chat).lstrip("@").lower():
            continue
        if not _is_join(cm.get("old_chat_member", {}).get("status"), cm.get("new_chat_member", {}).get("status")):
            continue
        d = datetime.fromtimestamp(int(cm["date"]), IST).date().isoformat() if cm.get("date") else ""
        name = (cm.get("invite_link") or {}).get("name") or "(public link)"
        counts.setdefault(d, {})
        counts[d][name] = counts[d].get(name, 0) + 1
    return counts, offset


def poll_joins(tg, chat, offset=None):
    """→ (counts, next_offset, source). source='webhook' when getUpdates cannot be used (the Worker counts instead)."""
    info = tg.call("getWebhookInfo")
    if info and info.get("url"):
        return {}, offset, "webhook"
    params = {"allowed_updates": ["chat_member"], "timeout": 0}
    if offset:
        params["offset"] = int(offset)
    ups = tg.call("getUpdates", **params)
    counts, nxt = joins_from_updates(ups, chat)
    if nxt:
        tg.call("getUpdates", offset=nxt, timeout=0, allowed_updates=["chat_member"])     # confirm, so they are not counted twice
    return counts, nxt or offset, "getUpdates"


def worker_stats(url, key, http_get=None):
    """The Worker's /stats → {'joins': {date: {name: n}}, 'bot': {date: {'requests': n, 'users': n, 'pro': n}}}."""
    get = http_get
    if get is None:
        import requests
        get = requests.get
    r = get(url, params={"key": key}, timeout=20)
    return r.json()


# ── the ledger ───────────────────────────────────────────────────────
def add_rows(ledger, rows):
    """Append (date, metric, key, value) rows; a later value for the same date·metric·key replaces the earlier one."""
    new = pd.DataFrame(rows, columns=["date", "metric", "key", "value"])
    if ledger is None or ledger.empty:
        out = new
    else:
        out = pd.concat([ledger[["date", "metric", "key", "value"]].astype(str), new.astype(str)], ignore_index=True)
    out = out.astype(str).drop_duplicates(["date", "metric", "key"], keep="last")
    return out.sort_values(["date", "metric", "key"]).reset_index(drop=True)


def series(ledger, metric, key=""):
    if ledger is None or ledger.empty:
        return pd.Series(dtype=float, index=pd.Index([], dtype=object))
    s = ledger[(ledger["metric"] == metric) & (ledger["key"].astype(str) == key)]
    return pd.to_numeric(s.set_index("date")["value"], errors="coerce").dropna().sort_index()


def total(ledger, metric, key, since="", until="9999"):
    s = series(ledger, metric, key)
    return float(s[(s.index >= since) & (s.index <= until)].sum()) if len(s) else 0.0


# ── Associates report (pasted into ASSOC_REPORT) ─────────────────────
def _num(v):
    s = re.sub(r"[^\d.\-]", "", str(v or ""))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def parse_assoc(df, tags):
    """Any Associates 'tracking ID summary'-style table → {channel: {'clicks','ordered','shipped','earnings'}}.
    Column names are matched loosely (the export format is not pinned: send one real export and it will be)."""
    if df is None or df.empty:
        return {}
    cols = {c: re.sub(r"[^a-z]", "", str(c).lower()) for c in df.columns}
    find = lambda *keys: next((c for c, n in cols.items() if any(k in n for k in keys)), None)
    c_tag = find("trackingid", "tracking", "tag")
    if not c_tag:
        return {}
    c_clicks, c_ord, c_ship = find("click"), find("itemsordered", "ordered"), find("itemsshipped", "shipped")
    c_earn = find("advertisingfee", "adfee", "earning", "commission", "fees", "bounty")
    out = {}
    for _, r in df.iterrows():
        t = str(r.get(c_tag, "")).strip()
        if not t or t.lower() in ("total", "totals", "nan"):
            continue
        ch = tags.channel_of(t)
        o = out.setdefault(ch, {"clicks": 0.0, "ordered": 0.0, "shipped": 0.0, "earnings": 0.0})
        for k, c in (("clicks", c_clicks), ("ordered", c_ord), ("shipped", c_ship), ("earnings", c_earn)):
            if c:
                o[k] += _num(r.get(c))
    return out


# ── weekly review ────────────────────────────────────────────────────
def _status(value, line, days_in):
    if value is None:
        return "no data"
    if days_in < line["check_days"]:
        return f"collecting (day {days_in} of {line['check_days']})"
    if value >= line["pass"]:
        return "PASS"
    if value < line["fail"]:
        return f"KILL LINE CROSSED → {line['fail_action']}"
    return "between the lines — keep going"


def metric_values(ledger, today, assoc, inputs):
    """The numbers the kill lines read. → dict metric → value (None when there is nothing to measure yet)."""
    t = date.fromisoformat(today)
    wk = (t - timedelta(days=6)).isoformat()
    prev = (t - timedelta(days=7)).isoformat()
    mem = series(ledger, "members", "telegram")
    m_now = float(mem.iloc[-1]) if len(mem) else None
    m_prev = float(mem[mem.index <= prev].iloc[-1]) if len(mem[mem.index <= prev]) else None
    v = {
        "joins:x-bio": total(ledger, "joins", "x-bio") if len(series(ledger, "joins", "x-bio")) or len(series(ledger, "x_posts", "posted")) else None,
        "forward_joins_per_100_members_week": (100 * total(ledger, "joins", "tg-forward", since=wk) / m_now) if m_now else None,
        "member_growth_week_pct": (100 * (m_now - m_prev) / m_prev) if (m_now and m_prev) else None,
        "clicks:whatsapp_month": (assoc.get("whatsapp", {}).get("clicks", 0) * 30 / 7) if assoc else None,
        "bot_users_week": total(ledger, "bot", "users", since=wk) if len(series(ledger, "bot", "users")) else None,
        "paying_sellers": _num(inputs.get("paying_sellers")) if inputs.get("paying_sellers") not in (None, "") else None,
    }
    return v, m_now


def start_dates(ledger, inputs):
    first = ledger["date"].min() if ledger is not None and not ledger.empty else None
    xp = series(ledger, "x_posts", "posted")
    xp = xp[xp > 0]
    pro = series(ledger, "bot", "pro")
    pro = pro[pro > 0]
    return {"first_day": first, "x_first_post": xp.index.min() if len(xp) else None,
            "pro_first": inputs.get("waitlist_opened") or (pro.index.min() if len(pro) else None)}


def review(ledger, today, assoc, inputs, goal=GOAL_INR):
    """→ (rows for GROWTH_REPORT, admin message lines)."""
    vals, m_now = metric_values(ledger, today, assoc, inputs)
    starts = start_dates(ledger, inputs)
    rows, lines = [], []
    for line in KILL_LINES:
        st = starts.get(line["start"])
        days_in = (date.fromisoformat(today) - date.fromisoformat(st)).days if st else -1
        val = vals.get(line["metric"])
        status = "not started" if st is None else _status(val, line, days_in)
        shown = "–" if val is None else (f"{val:.1f}" if isinstance(val, float) and not float(val).is_integer() else f"{int(val)}")
        rows.append({"date": today, "bet": line["bet"], "metric": line["metric"], "value": shown,
                     "pass": line["pass"], "fail": line["fail"], "status": status})
        lines.append(f"• {line['bet']}: {shown} (pass ≥ {line['pass']}, fail < {line['fail']}) — {status}")
    # goal worked backwards with measured numbers where we have them
    earn_week = sum(v.get("earnings", 0) for v in (assoc or {}).values())
    clicks_week = sum(v.get("clicks", 0) for v in (assoc or {}).values())
    orders_week = sum(v.get("ordered", 0) for v in (assoc or {}).values())
    measured = bool(assoc) and m_now and earn_week > 0
    if measured:
        per_member_month = earn_week * 30 / 7 / m_now
        need = goal / per_member_month if per_member_month > 0 else None
        basis = (f"measured: ₹{earn_week:,.0f} earned last week from {clicks_week:,.0f} clicks and {orders_week:,.0f} "
                 f"orders → ₹{per_member_month:.2f} per member per month")
    else:
        per_member_month = ASSUMED["daily_click_rate"] * 30 * ASSUMED["conversion"] * ASSUMED["inr_per_order"]
        need = goal / per_member_month
        basis = (f"ASSUMED (no Associates report yet): 2% of members click a day, 4% of clicks buy, ₹15 an order "
                 f"→ ₹{per_member_month:.2f} per member per month")
    g = vals.get("member_growth_week_pct")
    eta = ""
    if need and m_now and g and g > 0 and need > m_now:
        weeks = math.log(need / m_now) / math.log(1 + g / 100)
        eta = f" At this week's growth ({g:.1f}%/week) that is about {weeks:.0f} weeks away."
    elif need and m_now and need <= m_now:
        eta = " Already there on members; the gap is clicks or conversion."
    lines += ["", f"🎯 Goal ₹{goal:,.0f}/month needs about {need:,.0f} members ({basis})."
              + (f" Now: {int(m_now):,}." if m_now else "") + eta if need else "🎯 Goal: not enough data."]
    return rows, lines
