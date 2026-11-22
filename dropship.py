"""
Drop-ship lane — v1.70. A capped PROOF lane, not a third customer.

Why capped: drop shipping on Amazon.in is allowed only when you are the seller of record (your name on every slip and
invoice, the supplier's material removed, returns handled by you), and since 17 Aug 2026 a seller-caused cancellation
costs 10% of the order value plus GST. A supplier stock-out is exactly that. So the lane does three things only:

  shortlist   durable demand × sourceable × light for its price × a fee-aware margin with stock-out risk priced in.
              Used twice: OneZero's own pilots, and the "openings" section sellers pay for.
  readiness   the checklist a pilot must pass BEFORE the first listing (written agreement with the supplier, GST
              place of business for the pickup address, labels, sample).
  pilots      at most MAX_PILOTS at a time, tracked from owner commands in the bot (/pilot add, /order, /cancel,
              /return), judged by kill rules fixed here before any order exists.

Everything is ex-GST and per unit (fees.py). Margin numbers are estimates until the seller's fee preview confirms them.
"""
import os
import re
from datetime import date

import pandas as pd

import fees
import resell_gate

MAX_PILOTS = int(os.environ.get("DS_MAX_PILOTS") or 3)
PRICE_LO, PRICE_HI = 299, 999           # zero referral fee below ₹1,000; below ~₹300 fixed fees eat the unit
MAX_KG = 2.0
PILOT_COLS = ["pilot_id", "asin", "title", "category", "cost", "started", "status", "stopped", "reason"]
ORDER_COLS = ["date", "pilot_id", "kind", "qty", "price"]

READINESS = [
    "Supplier agrees in writing to ship in YOUR name: your invoice, no supplier slips, invoices or branding in the box.",
    "Supplier confirms stock and dispatch within your handling time; agree who pays when they cannot fill an order "
    "(each seller-caused cancellation costs you 10% of the order value + GST).",
    "The pickup address is on your GST registration as an additional place of business (Amazon picks up from it).",
    "Returns come back to you, not the supplier, and you can inspect and restock them.",
    "Packaging carries the Legal Metrology declarations (MRP, manufacturer or packer, country of origin, net quantity, "
    "consumer care) — check with the supplier before the first unit ships.",
    "You have ordered and checked a sample yourself.",
    "Fees confirmed in Seller Central's fee preview for this exact product (category, weight, fee group).",
]

# Kill rules — fixed before any pilot order exists. Edit here, not after the data comes in.
RULES = {"min_orders_judge": 10, "cancel_rate_max": 0.025, "margin_min": 0.10, "margin_keep": 0.25,
         "slow_days": 14, "slow_orders": 5, "keep_orders": 20}


def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _short(t, n=60):
    s = re.split(r"\s[|–]\s|,\s|\s\(", str(t or ""))[0].strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def shortlist(signals, cat_names=None, ind_costs=None, n=8, price_lo=PRICE_LO, price_hi=PRICE_HI, max_kg=MAX_KG):
    """→ list of candidate dicts, best first. Products whose weight is in the title come first (shipping decides
    drop-ship margins); the rest follow, labelled "weigh a sample". Licence-flagged products are left out."""
    if signals is None or not len(signals):
        return []
    s = signals.copy()
    s = s[s.get("durable", pd.Series(False, index=s.index)).astype(str).str.lower().isin(["true", "1"])]
    for col, bad in (("exit_flag", "falling"), ("stock_state", "unavailable")):
        if col in s:
            s = s[s[col].fillna("").astype(str) != bad]
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    out = []
    for _, r in s.iterrows():
        price = _f(r.get("price_inr"))
        if not price or not (price_lo <= price <= price_hi):
            continue
        g = resell_gate.check(r.get("title"), r.get("category_slug"))
        if not g["ok"]:
            continue
        comp = str(r.get("compliance") or "")
        if comp.startswith("REVIEW"):
            continue                                      # a licence or certification first is not a pilot
        kg = fees.weight_kg(r.get("title"))
        if kg is not None and kg > max_kg:
            continue
        cost = _f((ind_costs or {}).get(r["asin"]))
        e = fees.economics(price, cost=cost, kg=kg, dropship=True)
        if e["heavy"] or e["verdict"] in ("no",):
            continue
        headroom = (e.get("margin") if e.get("margin") is not None
                    else (e["max_cost"] / e["price_ex_gst"] if e.get("max_cost") else 0))
        if kg is None:                                    # shipping unknown: listed after the ones we can price
            headroom = 0.0
        score = (_f(r.get("durable_score")) or 0) * max(headroom, 0)
        out.append({"asin": r["asin"], "title": _short(r.get("title"), 70), "category": r.get("category_slug"),
                    "cname": (cat_names or {}).get(r.get("category_slug"), r.get("category_slug")),
                    "price": price, "kg": kg, "rank": _f(r.get("bs_rank_today")), "reviews": _f(r.get("reviews")),
                    "durable_score": _f(r.get("durable_score")), "econ": e, "line": fees.line(e), "ind_cost": cost,
                    "flag": "", "score": round(score, 2)})
    out.sort(key=lambda x: (x["kg"] is None, -x["score"], x["reviews"] or 1e9))
    return out[:n]


def shortlist_text(items, limit=6):
    if not items:
        return "📦 Drop-ship shortlist: nothing passes this week (durable, sourceable, no licence flag, ₹299–999, ≤ 2 kg, margin room)."
    lines = ["📦 <b>Drop-ship shortlist</b> (durable · sourceable · no licence flag · ₹299–999 · ≤ 2 kg · stock-out risk priced in)"]
    for i, x in enumerate(items[:limit], 1):
        lines.append(f"{i}. {x['title']} [{x['asin']}] — {x['line']}" + (f" ⚠️ {x['flag']}" if x["flag"] else ""))
    lines.append("Start one: /pilot add &lt;ASIN&gt; &lt;supplier cost ex-GST&gt; (max %d at a time, readiness list first)." % MAX_PILOTS)
    return "\n".join(lines)


# ── pilots ───────────────────────────────────────────────────────────
def _frame(df, cols):
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.copy().astype(object)
    for c in cols:
        if c not in df:
            df[c] = ""
    return df[cols].fillna("").astype(str)


def command(pilots, orders, args, today, signals=None):
    """/pilot add <ASIN> <cost> | /pilot stop <P#> [reason] | /pilot list
       /order <P#> <qty> <price> · /cancel <P#> <qty> · /return <P#> <qty>
       args[0] is the command word. → (pilots, orders, reply)."""
    pilots, orders = _frame(pilots, PILOT_COLS), _frame(orders, ORDER_COLS)
    if not args:
        return pilots, orders, "Use /pilot add B0… 210 · /order P1 2 499 · /cancel P1 1 · /return P1 1 · /pilot stop P1"
    cmd = args[0].lower()
    if cmd == "pilot":
        sub = (args[1].lower() if len(args) > 1 else "list")
        if sub == "add":
            if len(args) < 4 or not re.fullmatch(r"[A-Z0-9]{10}", args[2].upper()) or _f(args[3]) is None:
                return pilots, orders, "Use: /pilot add <ASIN> <supplier cost per unit, ex-GST, ₹>"
            active = pilots[pilots["status"] == "active"]
            if len(active) >= MAX_PILOTS:
                return pilots, orders, f"Already {len(active)} active pilots (max {MAX_PILOTS}). Stop one first."
            asin = args[2].upper()
            title, cat = "", ""
            if signals is not None and len(signals) and asin in set(signals["asin"]):
                row = signals[signals["asin"] == asin].iloc[0]
                title, cat = _short(row.get("title"), 70), str(row.get("category_slug") or "")
            pid = f"P{len(pilots) + 1}"
            pilots = pd.concat([pilots, pd.DataFrame([{"pilot_id": pid, "asin": asin, "title": title, "category": cat,
                                                       "cost": str(_f(args[3])), "started": str(today), "status": "active",
                                                       "stopped": "", "reason": ""}])], ignore_index=True)
            return pilots, orders, (f"{pid} started: {title or asin} at ₹{_f(args[3]):,.0f} a unit. Before the first listing, "
                                    f"tick the readiness list (/pilot ready). Log sales with /order {pid} <qty> <price>.")
        if sub == "stop" and len(args) > 2:
            m = pilots.index[pilots["pilot_id"].str.upper() == args[2].upper()]
            if not len(m):
                return pilots, orders, f"No pilot {args[2]}."
            pilots.loc[m[0], ["status", "stopped", "reason"]] = ["stopped", str(today), " ".join(args[3:])[:120] or "owner"]
            return pilots, orders, f"{args[2].upper()} stopped."
        if sub == "ready":
            return pilots, orders, "Before the first listing:\n" + "\n".join(f"☐ {x}" for x in READINESS)
        act = pilots[pilots["status"] == "active"]
        return pilots, orders, ("Active pilots: " + ", ".join(f"{r['pilot_id']} {r['title'] or r['asin']}" for _, r in act.iterrows())
                                if len(act) else "No active pilots. /pilot add <ASIN> <cost> starts one.")
    if cmd in ("order", "cancel", "return"):
        if len(args) < 3 or (cmd == "order" and len(args) < 4):
            return pilots, orders, f"Use: /{cmd} P1 <qty>" + (" <price ₹>" if cmd == "order" else "")
        pid = args[1].upper()
        if pid not in set(pilots["pilot_id"].str.upper()):
            return pilots, orders, f"No pilot {pid}."
        qty = _f(args[2])
        price = _f(args[3]) if cmd == "order" else ""
        if qty is None or qty <= 0 or (cmd == "order" and not price):
            return pilots, orders, "Quantity and price must be numbers."
        orders = pd.concat([orders, pd.DataFrame([{"date": str(today), "pilot_id": pid, "kind": cmd, "qty": str(int(qty)),
                                                   "price": str(price or "")}])], ignore_index=True)
        return pilots, orders, f"{pid}: {cmd} ×{int(qty)} logged."
    return pilots, orders, "Unknown pilot command."


def pilot_status(pilots, orders, signals, today):
    """→ list of dicts per active pilot: orders, cancel rate, realised margin estimate, verdict (RULES)."""
    pilots, orders = _frame(pilots, PILOT_COLS), _frame(orders, ORDER_COLS)
    out = []
    t = date.fromisoformat(str(today))
    for _, p in pilots[pilots["status"] == "active"].iterrows():
        o = orders[orders["pilot_id"].str.upper() == p["pilot_id"].upper()]
        q = lambda k: float(pd.to_numeric(o.loc[o["kind"] == k, "qty"], errors="coerce").fillna(0).sum())
        n_ord, n_can, n_ret = q("order"), q("cancel"), q("return")
        sold = o[o["kind"] == "order"]
        avg_price = (float((pd.to_numeric(sold["qty"]) * pd.to_numeric(sold["price"])).sum()) / n_ord) if n_ord else None
        days = (t - date.fromisoformat(p["started"])).days if p["started"] else 0
        kg, cur = None, None
        if signals is not None and len(signals) and p["asin"] in set(signals["asin"]):
            row = signals[signals["asin"] == p["asin"]].iloc[0]
            kg = fees.weight_kg(row.get("title"))
            cur = "falling" if str(row.get("exit_flag") or "") == "falling" else ("lasting" if str(row.get("durable")).lower() == "true" else "on the list")
        e = fees.economics(avg_price, cost=float(p["cost"]), kg=kg, dropship=False) if avg_price else None
        margin = e.get("margin") if e else None
        cancel_rate = n_can / n_ord if n_ord else 0.0
        if n_ord >= RULES["min_orders_judge"] and cancel_rate > RULES["cancel_rate_max"]:
            v = f"STOP — supplier cancellations {cancel_rate:.0%} (limit {RULES['cancel_rate_max']:.1%}); each costs 10% + GST"
        elif n_ord >= RULES["min_orders_judge"] and margin is not None and margin < RULES["margin_min"]:
            v = f"STOP — margin {margin:.0%} after fees and estimated shipping (limit {RULES['margin_min']:.0%})"
        elif days >= RULES["slow_days"] and n_ord < RULES["slow_orders"]:
            v = f"STOP or fix the listing — {int(n_ord)} orders in {days} days"
        elif n_ord >= RULES["keep_orders"] and (margin or 0) >= RULES["margin_keep"] and cancel_rate <= RULES["cancel_rate_max"]:
            v = "KEEP — demand, margin and supplier all hold; scale one step"
        else:
            v = f"collecting (day {days}, {int(n_ord)} orders)"
        if cur == "falling":
            v += " · ⚠️ the product's own demand started slipping on the list"
        out.append({"pilot_id": p["pilot_id"], "title": p["title"] or p["asin"], "orders": int(n_ord), "cancels": int(n_can),
                    "returns": int(n_ret), "avg_price": avg_price, "margin": margin, "cancel_rate": cancel_rate,
                    "days": days, "demand": cur or "not on the lists today", "verdict": v,
                    "ship_basis": e["ship_basis"] if e else ""})
    return out
