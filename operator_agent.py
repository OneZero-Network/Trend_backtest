"""
Operator agent — v1.70. Runs the business the way the owner asked: on the live floor, with no spreadsheet to fill.

Every night (BR_MODE=operator, ~22:00 IST, or on demand with /refresh in the bot) it:
  1. takes the owner's bot commands and uploads since the last run (the Worker queues them: /lead, /pilot, /order,
     /cancel, /return, /money, a CSV report sent as a file)
  2. counts the channels itself (members, joins per invite link, posts per arm, bot use, X spend — growth.py)
  3. moves money: Razorpay links for sellers who had a call, renewals, paid / lapsed (vendor.py)
  4. judges pilots and tests by rules fixed in code before any data (dropship.RULES, vendor.tier, ARM_RULES)
  5. writes ONE Live Floor report to the owner's Telegram (and to the bot's /floor), ending with today's to-dos

The owner does three things: sends the messages it drafts, tells the bot what happened (/lead L1a2b call), and drops the
weekly Associates report into the bot. Everything else is counted, not typed.

Pure functions here; I/O lives in trend_fetcher.main_operator.
"""
import io
import re
from datetime import date, timedelta

import pandas as pd

ARM_RULES = {"min_posts": 20, "min_clicks": 30, "min_orders": 10, "lose_ratio": 0.5}
COMMANDS = ("lead", "pilot", "order", "cancel", "return", "money", "audit", "quote", "deal", "card")
MONEY_COLS = ["from", "to", "source", "channel", "clicks", "ordered", "shipped", "earnings", "added"]


def _f(v):
    try:
        x = float(str(v).replace(",", "").replace("₹", ""))
        return None if x != x else x
    except (TypeError, ValueError):
        return None


# ── the owner's queue (from the Worker) ──────────────────────────────
def new_items(feed, done):
    """feed: {'events': [...], 'uploads': [...]} from the Worker's /events. → (events, uploads) not yet processed,
    oldest first. `done` = set of keys already handled (idempotent: a run that dies halfway is safe to repeat)."""
    done = set(done or ())
    ev = sorted((e for e in (feed or {}).get("events", []) if e.get("k") not in done), key=lambda e: e["k"])
    up = sorted((u for u in (feed or {}).get("uploads", []) if u.get("k") not in done), key=lambda u: u["k"])
    return ev, up


def parse_command(text):
    """'/lead L1a2b call' → ('lead', ['L1a2b', 'call']). Anything else → (None, [])."""
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", str(text or "").strip(), re.S)
    if not m or m.group(1).lower() not in COMMANDS:
        return None, []
    return m.group(1).lower(), m.group(2).split()


_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})\D{1,12}(\d{4}-\d{2}-\d{2})")


def report_range(text, caption, upload_day):
    """Date range of an uploaded report: from its first lines, else the caption ('2026-11-16 to 2026-11-22'), else
    the 7 days ending the day before the upload."""
    for src in (str(text or "")[:400], str(caption or "")):
        m = _RANGE.search(src)
        if m:
            return m.group(1), m.group(2)
    end = date.fromisoformat(str(upload_day)) - timedelta(days=1)
    return (end - timedelta(days=6)).isoformat(), end.isoformat()


def parse_upload(text, caption, upload_day, tags, parse_assoc):
    """An Associates tracking-ID report (CSV / TSV, any of its export layouts) → MONEY rows, or [] if unreadable."""
    frm, to = report_range(text, caption, upload_day)
    for skip in (0, 1, 2):
        for sep in (",", "\t"):
            try:
                df = pd.read_csv(io.StringIO(text), sep=sep, skiprows=skip, dtype=str)
            except Exception:
                continue
            if len(df.columns) < 3:                    # a title line read as the header: try the next row
                continue
            by = parse_assoc(df, tags)
            if by:
                return [{"from": frm, "to": to, "source": "assoc", "channel": ch, "clicks": v["clicks"],
                         "ordered": v["ordered"], "shipped": v["shipped"], "earnings": v["earnings"],
                         "added": str(upload_day)} for ch, v in by.items()]
    return []


def money_command(args, today):
    """/money <source> <orders> <earnings ₹> [clicks] — for programmes with no report file (EarnKaro, a pilot payout).
    Covers the 7 days to yesterday. → (rows, reply)."""
    if len(args) < 3 or _f(args[1]) is None or _f(args[2]) is None:
        return [], "Use: /money earnkaro <orders> <earnings ₹> [clicks]"
    frm, to = report_range("", "", today)
    row = {"from": frm, "to": to, "source": args[0].lower(), "channel": args[0].lower(),
           "clicks": _f(args[3]) if len(args) > 3 else 0.0, "ordered": _f(args[1]), "shipped": 0.0,
           "earnings": _f(args[2]), "added": str(today)}
    return [row], f"{args[0]}: ₹{row['earnings']:,.0f} from {int(row['ordered'])} orders logged for {frm} to {to}."


def money_frame(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=MONEY_COLS)
    df = df.copy()
    for c in MONEY_COLS:
        if c not in df:
            df[c] = ""
    return df[MONEY_COLS]


def add_money(money, rows):
    """A later upload for the same range and channel replaces the earlier one (re-sending a report is safe)."""
    m = money_frame(money).astype(str)
    if not rows:
        return m
    new = pd.DataFrame(rows).astype(str)
    out = pd.concat([m, new], ignore_index=True).drop_duplicates(["from", "to", "source", "channel"], keep="last")
    return out.reset_index(drop=True)


def latest_assoc(money):
    m = money_frame(money)
    a = m[m["source"] == "assoc"]
    if a.empty:
        return None, None, a
    last = a.sort_values(["to", "added"]).iloc[-1]
    rng = a[(a["from"] == last["from"]) & (a["to"] == last["to"])]
    return last["from"], last["to"], rng


# ── tests the floor runs every night ─────────────────────────────────
def arm_compare(money, post_log, tags_map):
    """Durable vs momentum on Telegram, per POST (the Bot API gives no view counts; both arms post to the same channel
    at swapped times, so per-post is the fair unit). Needs telegram.durable in AFF_TAGS. → (verdict, detail)."""
    if "telegram.durable" not in (tags_map or {}):
        return "Arms not separable yet", "add telegram.durable=<its own tracking ID> to AFF_TAGS"
    frm, to, rng = latest_assoc(money)
    if frm is None:
        return "No Associates report yet", "send this week's tracking-ID report to the bot as a file"
    pl = post_log if post_log is not None and len(post_log) else pd.DataFrame(columns=["posted_at", "channel", "arm"])
    pl = pl[(pl.get("channel", pd.Series("", index=pl.index)).astype(str) == "telegram")
            & (pl["posted_at"].astype(str).str[:10] >= frm) & (pl["posted_at"].astype(str).str[:10] <= to)]
    arm = pl.get("arm", pd.Series("", index=pl.index)).astype(str)
    posts = {"durable": int((arm == "durable").sum()), "momentum": int((arm != "durable").sum())}
    get = lambda ch, k: float(pd.to_numeric(rng.loc[rng["channel"] == ch, k], errors="coerce").fillna(0).sum())
    a = {"durable": {k: get("telegram.durable", k) for k in ("clicks", "ordered", "earnings")},
         "momentum": {k: get("telegram", k) for k in ("clicks", "ordered", "earnings")}}
    detail = (f"{frm} to {to}: durable {posts['durable']} posts, {a['durable']['clicks']:.0f} clicks, "
              f"{a['durable']['ordered']:.0f} orders, ₹{a['durable']['earnings']:,.0f} · momentum {posts['momentum']} posts, "
              f"{a['momentum']['clicks']:.0f} clicks, {a['momentum']['ordered']:.0f} orders, ₹{a['momentum']['earnings']:,.0f}")
    R = ARM_RULES
    if min(posts.values()) < R["min_posts"]:
        return f"Collecting: each arm needs {R['min_posts']} posts in the report's dates", detail
    if a["durable"]["clicks"] + a["momentum"]["clicks"] < R["min_clicks"]:
        return f"Too few clicks to judge (under {R['min_clicks']})", detail
    per = lambda arm_, k: a[arm_][k] / posts[arm_]
    if min(a["durable"]["ordered"], a["momentum"]["ordered"]) >= R["min_orders"]:
        d, m = per("durable", "earnings"), per("momentum", "earnings")
        what = "earnings per post"
    else:
        d, m = per("durable", "clicks"), per("momentum", "clicks")
        what = "clicks per post (orders too few to compare earnings)"
    if d < R["lose_ratio"] * m:
        return f"Durable loses on {what}", detail
    if d >= m:
        return f"Durable holds on {what}", detail
    return f"Too close to call on {what}: keep both", detail


def audience(ledger, series_fn):
    """→ dict: members now, change today, change over 7 days, joins today by source, and whether it fell 3 nights."""
    mem = series_fn(ledger, "members", "telegram")
    out = {"now": None, "d1": None, "d7": None, "joins": {}, "falling3": False}
    if len(mem):
        out["now"] = int(mem.iloc[-1])
        if len(mem) >= 2:
            out["d1"] = int(mem.iloc[-1] - mem.iloc[-2])
        if len(mem) >= 8:
            out["d7"] = int(mem.iloc[-1] - mem.iloc[-8])
        if len(mem) >= 4:
            diffs = mem.diff().dropna().iloc[-3:]
            out["falling3"] = bool(len(diffs) == 3 and (diffs < 0).all())
    if ledger is not None and len(ledger):
        last = ledger["date"].max()
        j = ledger[(ledger["metric"] == "joins") & (ledger["date"] == last)]
        out["joins"] = {k: int(float(v)) for k, v in zip(j["key"], j["value"]) if _f(v)}
    return out


def goal_line(money, mrr_inr, pilots_paid, goal_inr):
    """Affiliate run-rate (latest report, scaled to 30 days) + seller MRR → '₹X of ₹50,000 a month'."""
    frm, to, rng = latest_assoc(money)
    aff = 0.0
    if frm:
        days = (date.fromisoformat(to) - date.fromisoformat(frm)).days + 1
        aff = float(pd.to_numeric(rng["earnings"], errors="coerce").fillna(0).sum()) * 30 / max(days, 1)
    m = money_frame(money)
    other = m[(m["source"] != "assoc")]
    if len(other):
        last_to = other["to"].max()
        aff += float(pd.to_numeric(other.loc[other["to"] == last_to, "earnings"], errors="coerce").fillna(0).sum()) * 30 / 7
    total = aff + mrr_inr
    pct = 100 * total / goal_inr if goal_inr else 0
    return total, (f"₹{total:,.0f} a month of ₹{goal_inr:,.0f} ({pct:.1f}%): affiliate ~₹{aff:,.0f} run-rate"
                   + (" (no report yet)" if not frm else "") + f" + sellers ₹{mrr_inr:,.0f} MRR"
                   + (f" · {pilots_paid} on a ₹999 pilot" if pilots_paid else ""))


# ── the report ───────────────────────────────────────────────────────
def todos(ctx):
    """Concrete actions for the owner, most valuable first, at most 6."""
    t = []
    crm = ctx.get("crm")
    tier = ctx.get("tier") or {}
    hook = str(tier.get("verdict", "")).startswith("Hook problem")
    if crm is not None and len(crm) and not hook:          # a broken hook: stop sending, fix the message first
        new = crm[crm["status"] == "new"]
        if len(new):
            t.append(f"Send {min(len(new), 5)} outreach messages: /leads shows them with the text ready to copy")
    t += [l for l in ctx.get("pay_lines", []) if l.startswith(("💳", "🔔", "🔁"))][:2]
    if hook:
        t.append("Stop contacting new sellers until the first message changes (hook problem)")
    for a in ctx.get("audit_steps", [])[:2]:
        t.append(a)
    if ctx.get("savings_queue") == 0 and ctx.get("savings_on"):
        t.append("Queue tomorrow's savings offers: /deal <EarnKaro link> <one line> (2 a day)")
    if ctx.get("tags_missing"):
        t.append("Create tracking IDs in Associates Central for " + ", ".join(ctx["tags_missing"])
                 + " and add them to AFF_TAGS — until then money cannot be split by channel or post type")
    rep_age = ctx.get("report_age")
    if rep_age is None or rep_age > 8:
        t.append("Send this week's Associates tracking-ID report to the bot as a file"
                 + (f" (last one is {rep_age} days old)" if rep_age else ""))
    for p in ctx.get("pilots", []):
        if p["verdict"].startswith("STOP"):
            t.append(f"Pilot {p['pilot_id']}: {p['verdict']} → /pilot stop {p['pilot_id']}")
    aud = ctx.get("audience") or {}
    if aud.get("falling3"):
        t.append("Members fell 3 nights running: look at what was posted the day before each drop "
                 "(posting frequency stays as you set it — AFF_SLOTS is your call)")
    for n in ctx.get("notes", []):
        if n.startswith("⚠️"):
            t.append(n[2:].strip())
    return t[:6]


def floor(ctx, esc):
    """The nightly Live Floor message (Telegram HTML)."""
    L = [f"🟢 <b>Live Floor — {esc(ctx['today'])}</b>"]
    aud = ctx.get("audience") or {}
    if aud.get("now") is not None:
        L.append(f"📺 Channel: <b>{aud['now']:,}</b> members"
                 + (f" ({aud['d1']:+,} today" + (f", {aud['d7']:+,} this week)" if aud['d7'] is not None else ")") if aud['d1'] is not None else ""))
    else:
        L.append("📺 Channel: member count not available (bot must be a channel admin)")
    if aud.get("joins"):
        L.append("   joins: " + ", ".join(f"{k} {v}" for k, v in sorted(aud["joins"].items(), key=lambda x: -x[1])))
    pc = ctx.get("posts_today") or {}
    if pc:
        L.append("   posts today: " + ", ".join(f"{k} {v}" for k, v in pc.items()))
    if ctx.get("bot_users") is not None:
        L.append(f"   shopper bot: {ctx['bot_users']} people today")
    if ctx.get("x_line"):
        L.append("   " + ctx["x_line"])
    L.append("")
    L.append(f"💰 {esc(ctx.get('goal_line', ''))}")
    av, ad = ctx.get("arms", ("", ""))
    if av:
        L.append(f"   Durable vs momentum: <b>{esc(av)}</b>" + (f"\n   <i>{esc(ad)}</i>" if ad else ""))
    tier = ctx.get("tier") or {}
    L.append("")
    L.append(f"🏪 Sellers: {tier.get('contacted', 0)} contacted · {tier.get('calls', 0)} calls · {tier.get('paid', 0)} paid"
             + (f" · {ctx.get('subs', 0)} getting the Radar" if ctx.get("subs") else ""))
    if tier.get("verdict"):
        L.append(f"   Verdict: <b>{esc(tier['verdict'])}</b>")
    for l in ctx.get("pay_lines", [])[:4]:
        L.append("   " + esc(l))
    am = ctx.get("audit_month")
    if am is not None:
        L.append("")
        L.append(f"🧾 Audits this month: <b>{am['sold']}</b> sold · ₹{am['cash']:,.0f} collected · {am['hours']:g} h"
                 + (f" · ₹{am['per_hour']:,.0f}/h" if am.get("per_hour") else "") + f" — {esc(am['verdict'])}")
        for l in ctx.get("audit_lines", [])[:4]:
            L.append("   " + esc(l))
    sv = ctx.get("savings_lines")
    if sv is not None:
        L.append("")
        L.append(f"🛍 Savings &amp; cards: {ctx.get('savings_queue', 0)} queued" + (f" · {esc(ctx['savings_paused'])}" if ctx.get("savings_paused") else ""))
        for l in sv[:3]:
            L.append("   " + esc(l))
    pil = ctx.get("pilots") or []
    L.append("")
    if pil:
        L.append("📦 Drop-ship pilots:")
        for p in pil:
            L.append(f"   {p['pilot_id']} {esc(p['title'])}: {p['orders']} orders, {p['cancels']} cancelled"
                     + (f", margin ~{p['margin'] * 100:.0f}%" if p["margin"] is not None else "") + f" — {esc(p['verdict'])}")
    else:
        L.append("📦 Drop-ship: no pilot running" + (f" · {ctx['shortlist_n']} on this week's shortlist" if ctx.get("shortlist_n") else ""))
    if ctx.get("processed"):
        L.append("")
        L.append("🧾 Done since last night: " + "; ".join(esc(p) for p in ctx["processed"][:8]))
    td = todos(ctx)
    L.append("")
    L.append("✅ <b>Today</b>" if td else "✅ Nothing needs you today.")
    L += [f"{i}. {esc(x)}" for i, x in enumerate(td, 1)]
    notes = [n for n in ctx.get("notes", []) if not n.startswith("⚠️")]
    if notes:
        L.append("")
        L += [f"<i>{esc(n)}</i>" for n in notes[:4]]
    return "\n".join(L)


def leads_message(crm, briefs, outreach_fn, links_fn, esc, limit=5):
    """/leads: the leads still to contact, each with its message ready to copy and where to find the contact."""
    if crm is None or crm.empty:
        return "No leads yet. They come with Monday's vendor run."
    new = crm[crm["status"] == "new"].head(limit)
    if new.empty:
        return "Everyone found so far has been contacted. New leads come with Monday's vendor run."
    out = [f"🎯 <b>{len(new)} sellers to contact</b> (tap a message to copy, send it yourself, then /lead &lt;id&gt; contacted)"]
    for _, r in new.iterrows():
        lead = {"lead_id": r["lead_id"], "brand": r["brand"], "category": r["category"], "cname": r.get("cname") or r["category"],
                "kind": r["kind"], "title": r["title"], "rank": r.get("rank", ""), "days": r.get("days", ""),
                "exit_note": r.get("exit_note", "")}
        lk = links_fn(lead)
        out.append(f"\n<b>{esc(r['brand'])}</b> · {esc(r['category'])} · {esc(r['lead_id'])} · {esc(r['kind'])}\n"
                   f"<pre>{esc(outreach_fn(lead, briefs.get(r['category'])))}</pre>"
                   f"Find them: <a href=\"{esc(lk['amazon'])}\">Amazon</a> · <a href=\"{esc(lk['google'])}\">email</a> · "
                   f"<a href=\"{esc(lk['instagram'])}\">Instagram</a>")
    return "\n".join(out)
