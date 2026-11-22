"""
Vendor lane — v1.70. The paying customer: small Amazon.in sellers and D2C brands who already sell.

What it makes, every Monday, from the same daily bestseller data the channels use:
  category_brief   per category: what lasted (14-day presence), what started slipping, new entrants that held, and
                   sourceable openings with a fee-aware number a seller can check ("a supplier price under ₹212 keeps
                   25%"). Rendered as email HTML, a WhatsApp-pasteable text and a short Telegram preview.
  find_leads       small brands (not the big names in resell_gate) whose own listing is on a list right now: either
                   HOLDING (on most of the last 14 days) or SLIPPING (fell ≥10 places in 3 days). The hook is their own
                   observed numbers, never a claim we cannot show.
  outreach         a message drafted from those numbers. A PERSON sends it (account-ban and spam risk; see README).
  CRM              VENDOR_CRM table: new → contacted → replied → call → link_sent → pilot → monthly (or no / stop /
                   lapsed). The owner moves leads with one bot command (/lead L1a2b call); the Operator does the rest.
  payments         Razorpay Payment Links (optional): ₹999 pilot after a call, ₹1,999 renewal 5 days before expiry.
                   Sold and delivered OUTSIDE Telegram — Telegram requires Stars for digital services sold in a bot.
  delivery         the weekly brief by email (SMTP, optional); without SMTP it goes to the owner to forward.
  tier             the seller-test verdict, decided before any data (same tiers as the 22 Nov rules).

No prices are invented: price_inr is Amazon's own, fee lines come from fees.py with their basis labelled.
"""
import base64
import hashlib
import html
import json
import os
import re
from datetime import date, timedelta
from urllib.parse import quote_plus

import pandas as pd

import fees
import resell_gate

PILOT_INR = int(os.environ.get("VENDOR_PILOT_INR") or 999)
MONTHLY_INR = int(os.environ.get("VENDOR_MONTHLY_INR") or 1999)
PILOT_DAYS = 30
RENEW_BEFORE_DAYS = 5
SENDER = (os.environ.get("VENDOR_SENDER") or "OneZero").strip()


def _offer():
    """What the first message sells after the free page: 'audit' (default, v1.80), 'radar' or 'both'."""
    return (os.environ.get("VENDOR_OFFER") or "audit").strip().lower()


def _sender():
    return (os.environ.get("VENDOR_SENDER") or SENDER or "OneZero").strip()

STATUSES = ("new", "contacted", "replied", "call", "link_sent", "pilot", "monthly", "lapsed", "no", "stop")
ACTIVE = ("pilot", "monthly")
REACHED = ("contacted", "replied", "call", "link_sent", "pilot", "monthly", "lapsed", "no", "stop")
CALLED = ("call", "link_sent", "pilot", "monthly", "lapsed")
CRM_COLS = ["lead_id", "brand", "category", "cname", "kind", "asin", "title", "rank", "days", "exit_note", "hook",
            "first_seen", "status", "status_at",
            "email", "phone", "notes", "plan", "link_id", "link_url", "link_amount", "link_status", "link_at",
            "paid_at", "expires", "last_brief"]

H = lambda x: html.escape(str(x if x is not None else ""), quote=True)


def _short(t, n=60):
    s = re.split(r"\s[|–]\s|,\s|\s\(", str(t or ""))[0].strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def _true(s, col):
    return s[col].astype(str).str.lower().isin(["true", "1"]) if col in s else pd.Series(False, index=s.index)


def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _i(v):
    x = _f(v)
    return int(x) if x is not None else None


def _days(r):
    d, w = _i(r.get("durable_days")), _i(r.get("durable_window"))
    if d is None or w is None:
        return ""
    return f"{d} of the last {w} days" if d != w else f"all {w} days"


# ── the weekly category Radar ────────────────────────────────────────
def category_brief(signals, hist, cat, today, cat_names=None, ind_costs=None, n=8, fader=""):
    """→ dict with lasted / slipping / new_held / openings for one category. Empty lists when the data is thin."""
    cname = (cat_names or {}).get(cat, cat)
    s = signals[signals["category_slug"].astype(str) == str(cat)].copy() if signals is not None and len(signals) else pd.DataFrame()
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    if "title" in s:
        s = s[s["title"].notna() & ~s["title"].astype(str).str.strip().isin(["", "nan"])]
    out = {"cat": cat, "cname": cname, "date": str(today), "on_list": int(len(s)), "lasted": [], "slipping": [],
           "new_held": [], "openings": [], "durable_n": 0, "sourceable_n": 0, "slipping_n": 0, "new_held_n": 0,
           "fader": fader or ""}
    if s.empty:
        return out
    dur = s[_true(s, "durable")]
    if "exit_flag" in dur:
        dur = dur[dur["exit_flag"].fillna("").astype(str) != "falling"]
    dur = dur.assign(_d=pd.to_numeric(dur.get("durable_score"), errors="coerce")).sort_values("_d", ascending=False)
    out["durable_n"] = int(len(dur))
    top = dur.head(n).assign(_r=pd.to_numeric(dur.head(n).get("bs_rank_today"), errors="coerce")).sort_values("_r")
    for _, r in top.iterrows():                                   # the most durable n, shown in rank order
        out["lasted"].append({"asin": r["asin"], "title": _short(r.get("title")), "rank": _i(r.get("bs_rank_today")),
                              "median": _f(r.get("median_rank")), "days": _days(r), "reviews": _i(r.get("reviews")),
                              "rpd": _f(r.get("reviews_per_day")), "price": _f(r.get("price_inr"))})
    if "exit_flag" in s:
        fl = s[s["exit_flag"].fillna("").astype(str) == "falling"]
        out["slipping_n"] = int(len(fl))
        for _, r in fl.head(n).iterrows():
            out["slipping"].append({"asin": r["asin"], "title": _short(r.get("title")),
                                    "note": str(r.get("exit_note") or "").strip(), "rank": _i(r.get("bs_rank_today"))})
    if "first_seen" in s and "days_on_list_14d" in s:
        start = (date.fromisoformat(str(today)) - timedelta(days=13)).isoformat()
        nh = s[(s["first_seen"].astype(str) >= start) & (pd.to_numeric(s["days_on_list_14d"], errors="coerce") >= 7)]
        nh = nh.assign(_n=pd.to_numeric(nh["days_on_list_14d"], errors="coerce")).sort_values("_n", ascending=False)
        out["new_held_n"] = int(len(nh))
        for _, r in nh.head(n).iterrows():
            out["new_held"].append({"asin": r["asin"], "title": _short(r.get("title")), "rank": _i(r.get("bs_rank_today")),
                                    "days": _i(r.get("days_on_list_14d")), "since": str(r.get("first_seen"))[:10]})
    ok = dur[[resell_gate.check(t, cat)["ok"] for t in dur["title"].astype(str)]] if len(dur) else dur
    out["sourceable_n"] = int(len(ok))
    for _, r in ok.iterrows():
        price = _f(r.get("price_inr"))
        if not price:
            continue
        kg = fees.weight_kg(r.get("title"))
        cost = (ind_costs or {}).get(r["asin"])
        e = fees.economics(price, cost=None, kg=kg)
        out["openings"].append({"asin": r["asin"], "title": _short(r.get("title")), "rank": _i(r.get("bs_rank_today")),
                                "durable": _f(r.get("durable_score")),
                                "days": _days(r), "reviews": _i(r.get("reviews")), "price": price, "econ": e,
                                "line": fees.line(e), "ind_cost": cost,
                                "flag": str(r.get("compliance") or "") if str(r.get("compliance") or "").startswith("REVIEW") else ""})
    # rank the openings: a ticket that can carry fees and shipping, held demand, and a leader that is not yet locked in
    # (fewer reviews). Heavy-for-price and under-₹300 items go last: shipping and fixed fees eat them.
    import math
    def _score(o):
        if o["econ"]["heavy"] or o["econ"]["verdict"] == "no":
            return -1.0
        ticket = min(o["price"] / 500.0, 2.0) if o["price"] >= 300 else 0.2
        cert = 0.3 if o["flag"] else 1.0                 # a BIS / licence step first is a weaker opening
        return (o["durable"] or 50) / 100 * ticket * cert / math.log10((o["reviews"] or 0) + 10)
    out["openings"].sort(key=_score, reverse=True)
    out["openings"] = out["openings"][:3]
    return out


def brief_text(b, limit=6):
    """Telegram (HTML) preview for the owner."""
    if not b["lasted"] and not b["slipping"]:
        return f"<b>{H(b['cname'])}</b>: not enough on the list this week."
    lines = [f"🏪 <b>{H(b['cname'])} — what lasted</b> ({H(b['date'])})",
             f"{b['on_list']} on the lists · {b['durable_n']} lasted · {b['sourceable_n']} a seller can source"]
    for x in b["lasted"][:limit]:
        lines.append(f"• {H(x['title'])} — #{x['rank']} · {H(x['days'])}")
    if b["slipping"]:
        lines.append("📉 Slipping: " + "; ".join(f"{H(x['title'])} ({H(x['note'])})" for x in b["slipping"][:3]))
    if b["openings"]:
        lines.append("🎯 Openings:")
        lines += [f"  {i}. {H(o['title'])} — {H(o['line'])}" for i, o in enumerate(b["openings"], 1)]
    return "\n".join(lines)


def brief_whatsapp(b):
    """Plain text a person can paste into WhatsApp (no HTML, no links)."""
    lines = [f"*{b['cname']}: what lasted on Amazon.in* (to {b['date']})", "",
             f"{b['on_list']} products on the bestseller lists; {b['durable_n']} stayed most of the last 14 days."]
    if b["lasted"]:
        lines += ["", "*Lasted*"] + [f"• {x['title']} — #{x['rank']}, {x['days']}" for x in b["lasted"][:6]]
    if b["slipping"]:
        lines += ["", "*Started slipping*"] + [f"• {x['title']} — {x['note']}" for x in b["slipping"][:4]]
    if b["new_held"]:
        lines += ["", "*New and holding*"] + [f"• {x['title']} — on {x['days']} days since {x['since']}" for x in b["new_held"][:4]]
    if b["openings"]:
        lines += ["", "*Openings to check*"] + [f"{i}. {o['title']}: {o['line']}" for i, o in enumerate(b["openings"], 1)]
    if b.get("fader"):
        lines += ["", b["fader"]]
    lines += ["", "Observed data, one snapshot a day. Rank is not sales. Fees: confirm in Seller Central's fee preview."]
    return "\n".join(lines)


def brief_html(b, reply_to=""):
    """Self-contained, email-safe HTML (inline styles, tables)."""
    css_td = "padding:6px 8px;border-bottom:1px solid #e5e5e5;font:14px Arial,sans-serif;color:#222;vertical-align:top"
    css_th = css_td.replace("#222", "#555") + ";font-weight:bold;background:#f5f5f5"

    def table(head, rows):
        return ("<table style='border-collapse:collapse;width:100%;margin:8px 0 18px'>"
                + "<tr>" + "".join(f"<th align='left' style='{css_th}'>{H(h)}</th>" for h in head) + "</tr>"
                + "".join("<tr>" + "".join(f"<td style='{css_td}'>{H(c)}</td>" for c in r) + "</tr>" for r in rows)
                + "</table>")

    p = lambda t: f"<p style='font:15px Arial,sans-serif;color:#222;line-height:1.5'>{t}</p>"
    h2 = lambda t: f"<h2 style='font:bold 18px Arial,sans-serif;color:#111;margin:22px 0 4px'>{H(t)}</h2>"
    parts = [f"<h1 style='font:bold 22px Arial,sans-serif;color:#111'>{H(b['cname'])}: what lasted on Amazon.in</h1>",
             p(f"Week to {H(b['date'])}. Of {b['on_list']} products on the {H(b['cname'])} bestseller lists, "
               f"<b>{b['durable_n']}</b> were on the list most of the last 14 days; <b>{b['sourceable_n']}</b> of those "
               f"are product types a seller can source (no big brand, no formulated product).")]
    if b["lasted"]:
        parts += [h2("What lasted"), table(["Product", "Rank now", "On the list", "Reviews", "Price seen"],
                  [[x["title"], f"#{x['rank']}", x["days"], f"{x['reviews']:,}" if x["reviews"] else "–",
                    f"₹{x['price']:,.0f}" if x["price"] else "–"] for x in b["lasted"]])]
    if b["slipping"]:
        parts += [h2("Started slipping"), table(["Product", "What happened"], [[x["title"], x["note"]] for x in b["slipping"]])]
        if b.get("fader"):
            parts.append(p(H(b["fader"])))
    if b["new_held"]:
        parts += [h2("New and holding"), table(["Product", "First seen", "Days on the list"],
                  [[x["title"], x["since"], x["days"]] for x in b["new_held"]])]
    if b["openings"]:
        parts += [h2("Openings to check"), table(["Product", "What the numbers allow"],
                  [[o["title"] + (f" ({o['flag']})" if o["flag"] else ""), o["line"]] for o in b["openings"]]),
                  p("Fees use Amazon's published 2026 figures where they exist (no referral fee under ₹1,000 in most "
                    "categories; the highest published closing fee in each band). Shipping is Amazon's published Easy Ship "
                    "weight fee (Standard STEP) for the weight in the title; a bulky box is billed on its volume instead. "
                    "Confirm both in Seller Central's fee preview before you buy stock.")]
    parts.append(p("<i>Observed data from one snapshot a day. A rank shows where a listing sits, not how many units "
                   "it sells. Nothing here is a forecast.</i>"))
    if reply_to:
        parts.append(p(f"Questions or a product to check: reply to {H(reply_to)}."))
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{H(b['cname'])} Radar {H(b['date'])}</title></head><body style='margin:0;padding:16px;background:#fff'>"
            "<div style='max-width:680px;margin:0 auto'>" + "".join(parts) + "</div></body></html>")


# ── leads ────────────────────────────────────────────────────────────
_GENERIC = {"generic", "unbranded", "amazon", "new", "the", "pack", "set", "combo", "premium", "pull", "push", "yoga",
            "resistance", "hand", "gym", "home", "kitchen", "steel", "stainless", "plastic", "wooden", "electric",
            "portable", "adjustable", "heavy", "multi", "men", "women", "kids", "baby", "pet", "dog", "cat", "car",
            "bike", "mobile", "phone", "usb", "led", "smart", "wireless", "mini", "large", "small", "original"}


def brand_guess(title):
    """Amazon titles lead with the brand. → the first word when it looks like a name, else ''."""
    w = re.findall(r"[A-Za-z][A-Za-z0-9&'.-]*", str(title or ""))
    if not w:
        return ""
    b = w[0].strip(".-'")
    if len(b) < 3 or b.lower() in _GENERIC:
        return ""
    return b


def lead_id(brand, cat):
    return "L" + hashlib.sha1(f"{brand.lower()}|{cat}".encode()).hexdigest()[:4]


def find_leads(signals, cat_names=None, known=(), per_cat=3, cats=None):
    """Small brands with a listing on a list today. → [lead dicts], SLIPPING first (the stronger reason to talk)."""
    if signals is None or not len(signals):
        return []
    s = signals.copy()
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    if cats:
        s = s[s["category_slug"].astype(str).isin(cats)]
    s["_dur"] = _true(s, "durable")
    s["_fall"] = s.get("exit_flag", pd.Series("", index=s.index)).fillna("").astype(str) == "falling"
    s = s[s["_dur"] | s["_fall"]]
    out, seen = [], set(known)
    s = s.assign(_k=s["_fall"].astype(int), _d=pd.to_numeric(s.get("durable_score"), errors="coerce").fillna(0))
    for cat, g in s.sort_values(["_k", "_d"], ascending=False).groupby("category_slug", sort=False):
        taken = {"slipping": 0, "holding": 0}
        cap = {"slipping": (per_cat + 1) // 2, "holding": per_cat // 2 or 1}       # a mix: urgency and success stories
        for _, r in g.iterrows():
            t = str(r.get("title") or "")
            if resell_gate.brand_of(t):
                continue                                  # big brands have teams for this; not our buyer
            b = brand_guess(t)
            if not b:
                continue
            kind = "slipping" if r["_fall"] else "holding"
            if taken[kind] >= cap[kind]:
                continue
            lid = lead_id(b, cat)
            if lid in seen:
                continue
            seen.add(lid)
            hook = (f"{_short(t, 50)} {str(r.get('exit_note') or '').strip()}" if kind == "slipping"
                    else f"{_short(t, 50)} on the list {_days(r)}, now #{_i(r.get('bs_rank_today'))}")
            out.append({"lead_id": lid, "brand": b, "category": cat, "cname": (cat_names or {}).get(cat, cat), "kind": kind,
                        "asin": r["asin"], "title": _short(t, 70), "hook": hook,
                        "rank": _i(r.get("bs_rank_today")), "days": _days(r), "exit_note": str(r.get("exit_note") or "")})
            taken[kind] += 1
            if sum(taken.values()) >= per_cat:
                break
    return out


def contact_links(lead):
    b = quote_plus(lead["brand"])
    return {"amazon": f"https://www.amazon.in/s?k={b}",
            "google": f"https://www.google.com/search?q=%22{b}%22+contact+email",
            "instagram": f"https://www.google.com/search?q=%22{b}%22+instagram"}


def _product(title, n=48):
    """The product as it reads in a sentence: cut at a word, no trailing ellipsis."""
    t = re.split(r"\s[|–]\s|,\s|\s\(|\s\[", str(title or ""))[0].strip().rstrip("…").strip()
    if len(t) > n:
        t = t[:n].rsplit(" ", 1)[0]
    words = t.split()
    while len(words) > 2 and words[-1].lower().strip("-&,") in {"with", "for", "and", "&", "of", "the", "to", "in", "on", "-", "by", "a"}:
        words.pop()
    return " ".join(words).rstrip(" -&,")


def outreach(lead, brief=None, sender=None, pilot=PILOT_INR):
    """The first message, built from observed numbers only. A person sends it. No brand in the greeting: a guessed
    brand ('Fur' for 'Fur Jaden') reads worse than none."""
    sender = sender or _sender()
    cname = lead.get("cname") or lead["category"]
    extra = ""
    if brief:
        bits = []
        if brief.get("slipping_n"):
            n = brief["slipping_n"]
            bits.append(f"{n} listing{'s' if n != 1 else ''} in {cname} started slipping")
        if brief.get("new_held_n"):
            n = brief["new_held_n"]
            bits.append(f"{n} new one{'s' if n != 1 else ''} held for a week or more")
        if bits:
            extra = " This week " + " and ".join(bits) + "."
    if lead["kind"] == "slipping":
        open_ = (f"Hi, we track Amazon.in's {cname} bestseller list every day. "
                 f"Your {_product(lead['title'])} {lead['exit_note'] or 'dropped sharply'} this week.")
    else:
        open_ = (f"Hi, we track Amazon.in's {cname} bestseller list every day. "
                 f"Your {_product(lead['title'])} has been on it {lead['days']} (now #{lead['rank']}).")
    audit_fee = int(os.environ.get("AUDIT_FEE_SKU") or 10000)
    offer = {"radar": f"If it's useful we send it every Monday (₹{pilot:,} for a 30-day pilot).",
             "audit": (f"If one of the openings fits you, we can get 3 written supplier quotes and a landed Easy Ship "
                       f"margin sheet for it (₹{audit_fee:,} per product)."),
             }
    offer["both"] = offer["audit"] + " " + offer["radar"]
    return (f"{open_}{extra}\n\nWe put the category on one page: what lasted, what is slipping and who is new. "
            f"It's free; want me to send it? {offer.get(_offer(), offer['audit'])}"
            f"\n\nReply STOP and we won't write again. — {sender}")


# ── CRM ──────────────────────────────────────────────────────────────
def crm_frame(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=CRM_COLS)
    df = df.copy().astype(object)
    for c in CRM_COLS:
        if c not in df:
            df[c] = ""
    return df[CRM_COLS + [c for c in df.columns if c not in CRM_COLS]].fillna("").astype(str)


def crm_merge(crm, leads, today):
    """Add new leads as status 'new'. Existing leads keep their status and notes."""
    crm = crm_frame(crm)
    have = set(crm["lead_id"])
    rows = [{**{c: "" for c in CRM_COLS}, "lead_id": l["lead_id"], "brand": l["brand"], "category": l["category"],
             "cname": l.get("cname", ""), "kind": l["kind"], "asin": l["asin"], "title": l["title"],
             "rank": "" if l.get("rank") is None else str(l["rank"]), "days": l.get("days", ""),
             "exit_note": l.get("exit_note", ""), "hook": l["hook"], "first_seen": str(today),
             "status": "new", "status_at": str(today)} for l in leads if l["lead_id"] not in have]
    if rows:
        crm = pd.concat([crm, pd.DataFrame(rows)], ignore_index=True)
    return crm.astype(str), len(rows)


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def apply_command(crm, args, today):
    """/lead <id> <status|email|phone|note|paid> [value…] → (crm, reply). Unknown id or status → a helpful reply."""
    crm = crm_frame(crm)
    if len(args) < 2:
        return crm, "Use: /lead L1a2b contacted | replied | call | no | stop | paid pilot | paid monthly | email a@b.in | phone 98… | note …"
    lid, what, rest = args[0].strip(), args[1].strip().lower(), [a for a in args[2:]]
    m = crm.index[crm["lead_id"].str.lower() == lid.lower()]
    if not len(m):
        return crm, f"No lead {lid}. /leads lists them."
    i = m[0]
    name = crm.at[i, "brand"]
    if what == "email":
        v = rest[0] if rest else ""
        if not _EMAIL.match(v):
            return crm, f"That doesn't look like an email address: {v}"
        crm.at[i, "email"] = v
        return crm, f"{name}: email saved."
    if what == "phone":
        crm.at[i, "phone"] = re.sub(r"[^\d+]", "", " ".join(rest))
        return crm, f"{name}: phone saved."
    if what == "note":
        crm.at[i, "notes"] = (crm.at[i, "notes"] + " | " if crm.at[i, "notes"] else "") + f"{today}: " + " ".join(rest)[:200]
        return crm, f"{name}: note added."
    if what == "paid":
        plan = (rest[0].lower() if rest else "pilot")
        plan = "monthly" if plan.startswith("m") else "pilot"
        crm.at[i, "status"], crm.at[i, "plan"], crm.at[i, "paid_at"] = plan, plan, str(today)
        crm.at[i, "expires"] = (date.fromisoformat(str(today)) + timedelta(days=PILOT_DAYS)).isoformat()
        crm.at[i, "status_at"] = str(today)
        return crm, f"{name}: marked paid ({plan}) until {crm.at[i, 'expires']}. They get the Radar every Monday."
    if what not in STATUSES:
        return crm, f"Unknown status '{what}'. Use one of: " + ", ".join(STATUSES)
    crm.at[i, "status"], crm.at[i, "status_at"] = what, str(today)
    tip = {"call": " The Operator prepares the ₹%s pilot link tonight." % f"{PILOT_INR:,}",
           "stop": " They will not be contacted again."}.get(what, "")
    return crm, f"{name}: {what}.{tip}"


def tier(crm, extra_paid=0):
    """The seller-test verdict (rules fixed on 22 Nov, before any data). extra_paid: sellers who paid for something
    else (v1.80: audits) — a payer is a payer."""
    crm = crm_frame(crm)
    st = crm["status"]
    contacted = int(st.isin(REACHED).sum())
    calls = int(st.isin(CALLED).sum())
    pilots = int(((crm["plan"] == "pilot") | (st == "pilot")).sum())
    monthly = int(((crm["plan"] == "monthly") | (st == "monthly")).sum())
    paid = int((crm["paid_at"].astype(str) != "").sum()) + int(extra_paid or 0)
    if monthly >= 2 or paid >= 4:
        v = "Sellers first: build the seller product next"
    elif paid >= 1:
        v = "Promising: 15 more sellers, sharpen the offer"
    elif contacted >= 15 and calls < 4:
        v = f"Hook problem: {calls} calls from {contacted} contacts. Change the message or where you find sellers before contacting more"
    elif contacted >= 30 and calls >= 8:
        v = f"No payers after {calls} calls: drop the subscription, offer a one-off research sprint"
    elif contacted >= 15 and calls >= 4:
        v = f"No payers yet from {calls} calls: change the offer and contact 15 more"
    else:
        v = f"Collecting: {contacted} of 15 contacted, {calls} calls"
    return {"contacted": contacted, "calls": calls, "pilots": pilots, "monthly": monthly, "paid": paid, "verdict": v}


def mrr(crm, today):
    crm = crm_frame(crm)
    live = crm[crm["status"].isin(ACTIVE) & (crm["expires"].astype(str) >= str(today))]
    return int((live["status"] == "monthly").sum() * MONTHLY_INR), int((live["status"] == "pilot").sum()), len(live)


# ── payments (Razorpay Payment Links) ────────────────────────────────
class Razorpay:
    """POST /v1/payment_links (amount in paise, Basic auth) and GET /v1/payment_links/{id}. Off without keys."""
    BASE = "https://api.razorpay.com/v1/payment_links"

    def __init__(self, key_id=None, key_secret=None, http=None):
        self.key_id = key_id if key_id is not None else os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret if key_secret is not None else os.environ.get("RAZORPAY_KEY_SECRET", "")
        self.http = http
        self.on = bool(self.key_id and self.key_secret)

    def _auth(self):
        return "Basic " + base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()

    def _req(self, method, url, body=None):
        if self.http is not None:
            return self.http(method, url, body)
        import requests
        r = requests.request(method, url, headers={"Authorization": self._auth(), "Content-Type": "application/json"},
                             data=json.dumps(body) if body is not None else None, timeout=30)
        return r.status_code, r.json()

    def create(self, amount_inr, description, reference, name="", email="", phone="", expire_days=7):
        import time
        body = {"amount": int(round(amount_inr * 100)), "currency": "INR", "description": description[:2048],
                "reference_id": reference[:40], "expire_by": int(time.time()) + expire_days * 86400,
                "reminder_enable": True, "notes": {"source": "onezero-operator"},
                "notify": {"email": bool(email), "sms": False}}
        cust = {k: v for k, v in (("name", name), ("email", email), ("contact", phone)) if v}
        if cust:
            body["customer"] = cust
        code, js = self._req("POST", self.BASE, body)
        if code >= 300:
            raise RuntimeError(f"Razorpay {code}: {js.get('error', js) if isinstance(js, dict) else js}")
        return js["id"], js.get("short_url", ""), js.get("status", "created")

    def status(self, link_id):
        code, js = self._req("GET", f"{self.BASE}/{link_id}")
        if code >= 300:
            raise RuntimeError(f"Razorpay {code}")
        return js.get("status", "")


def payment_actions(crm, today, rzp, notify=print):
    """Daily: make links for leads at 'call', renewals before expiry; poll open links; mark paid / lapsed.
    → (crm, lines for the owner). Without Razorpay keys it only lists what to do by hand."""
    crm = crm_frame(crm)
    t = date.fromisoformat(str(today))
    lines = []
    for i, r in crm.iterrows():
        st = r["status"]
        try:
            if st == "call" and not r["link_id"]:
                if rzp is not None and rzp.on:
                    ref = f"{r['lead_id']}-pilot-{today}"
                    lid, url, _ = rzp.create(PILOT_INR, f"{SENDER} Radar — {r['category']} — 30-day pilot", ref,
                                             name=r["brand"], email=r["email"], phone=r["phone"])
                    crm.loc[i, ["link_id", "link_url", "link_amount", "link_status", "link_at", "status", "status_at"]] = \
                        [lid, url, str(PILOT_INR), "created", str(today), "link_sent", str(today)]
                    lines.append(f"💳 {r['brand']}: ₹{PILOT_INR:,} pilot link ready → {url}"
                                 + (" (Razorpay emailed it too)" if r["email"] else " — send it on WhatsApp"))
                else:
                    lines.append(f"💳 {r['brand']}: had a call — send the ₹{PILOT_INR:,} pilot payment link, then /lead {r['lead_id']} paid pilot")
            elif st == "link_sent" and r["link_id"] and rzp is not None and rzp.on:
                s = rzp.status(r["link_id"])
                crm.at[i, "link_status"] = s
                if s == "paid":
                    plan = "monthly" if str(r["link_amount"]) == str(MONTHLY_INR) else "pilot"
                    start = max(t, date.fromisoformat(r["expires"])) if r["expires"] else t     # a renewal extends, never cuts
                    crm.loc[i, ["status", "plan", "paid_at", "expires", "status_at"]] = \
                        [plan, plan, str(today), (start + timedelta(days=PILOT_DAYS)).isoformat(), str(today)]
                    lines.append(f"✅ {r['brand']} paid ₹{int(float(r['link_amount'] or 0)):,} ({plan}) — covered until "
                                 f"{crm.at[i, 'expires']}.")
                elif s in ("expired", "cancelled"):
                    if r["paid_at"]:                                  # a renewal that was not paid
                        still = r["expires"] >= str(today)
                        crm.loc[i, ["status", "status_at"]] = [(r["plan"] or "pilot") if still else "lapsed", str(today)]
                        lines.append(f"⌛ {r['brand']}: renewal link {s}" + (" — access ends " + r["expires"] if still else " — subscription ended."))
                    else:
                        crm.loc[i, ["status", "status_at"]] = ["replied", str(today)]
                        lines.append(f"⌛ {r['brand']}: payment link {s}. Ask if they still want it.")
                elif r["paid_at"] and r["expires"] < str(today):
                    crm.loc[i, ["status", "status_at"]] = ["lapsed", str(today)]
                    lines.append(f"⚪ {r['brand']}: subscription ended (renewal unpaid).")
                elif r["link_at"] and (t - date.fromisoformat(r["link_at"])).days >= 3:
                    lines.append(f"🔔 {r['brand']}: link sent {(t - date.fromisoformat(r['link_at'])).days} days ago, unpaid — follow up.")
            elif st in ACTIVE and r["expires"]:
                left = (date.fromisoformat(r["expires"]) - t).days
                if left < 0:
                    crm.loc[i, ["status", "status_at"]] = ["lapsed", str(today)]
                    lines.append(f"⚪ {r['brand']}: subscription ended.")
                elif left <= RENEW_BEFORE_DAYS and r["link_status"] in ("paid", "") and r["link_at"] < (t - timedelta(days=RENEW_BEFORE_DAYS + 1)).isoformat():
                    if rzp is not None and rzp.on:
                        ref = f"{r['lead_id']}-m-{today}"
                        lid, url, _ = rzp.create(MONTHLY_INR, f"{SENDER} Radar — {r['category']} — monthly", ref,
                                                 name=r["brand"], email=r["email"], phone=r["phone"], expire_days=10)
                        crm.loc[i, ["link_id", "link_url", "link_amount", "link_status", "link_at"]] = \
                            [lid, url, str(MONTHLY_INR), "created", str(today)]
                        crm.at[i, "status"] = "link_sent"
                        # keep access until the paid period ends: 'expires' is unchanged
                        lines.append(f"🔁 {r['brand']}: renewal link (₹{MONTHLY_INR:,}) → {url}")
                    else:
                        lines.append(f"🔁 {r['brand']}: ends in {left} days — send the ₹{MONTHLY_INR:,} renewal link.")
        except Exception as exc:                                      # one lead never stops the others
            lines.append(f"⚠️ {r['brand']}: payment step failed ({exc})")
    return crm.astype(str), lines


def subscribers(crm, today):
    """Leads whose paid period covers today (a renewal link out does not end access)."""
    crm = crm_frame(crm)
    paid = crm[(crm["paid_at"] != "") & (crm["expires"].astype(str) >= str(today))
               & ~crm["status"].isin(["no", "stop", "lapsed"])]
    return paid


# ── email (SMTP, optional) ───────────────────────────────────────────
def smtp_ready():
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def send_email(to, subject, html_body, text_body="", sender=None, smtp=None, attachments=()):
    """SMTP over TLS (port 587) or SSL (465). `smtp` is injectable for tests. attachments: file paths. → True when sent."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    alt = MIMEMultipart("alternative")
    frm = sender or os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "")
    alt.attach(MIMEText(text_body or re.sub(r"<[^>]+>", " ", html_body), "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(alt)
        for path in attachments:
            with open(path, "rb") as fh:
                part = MIMEApplication(fh.read(), Name=os.path.basename(path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(path)}"'
            msg.attach(part)
    else:
        msg = alt
    msg["Subject"], msg["From"], msg["To"] = subject, frm, to
    if smtp is not None:
        smtp(frm, [to], msg.as_string())
        return True
    import smtplib
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.sendmail(frm, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.sendmail(frm, [to], msg.as_string())
    return True
