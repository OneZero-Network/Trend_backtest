"""
Opportunity estimator — "if we focus on this product for the next 3 months, what could we earn?"

HONEST LIMITS (read before trusting a number):
  * Amazon publishes ranks, not sales. Units are inferred from SALES ANCHORS: products where we saw
    both a bestseller rank and Amazon's "N+ bought in past month" on the same day. No anchors in a
    category → no unit estimate (we don't invent one).
  * Every output is a RANGE (P10 / P50 / P90 from 4,000 simulations), never a single promise.
    P50 = half the simulated futures were above it. P10 = a bad-but-plausible outcome.
  * Assumptions you haven't replaced with your own numbers are listed in the report. The estimate
    tightens as you add: commission % (COMMISSION tab), your real Associates clicks/orders
    (AFF_ACTUALS tab), manual anchors (SALES_ANCHORS tab), and a supplier quote (CALCULATOR).
  * It assumes the future looks like the history we have. Festivals, stock-outs, a price war or
    Amazon launching its own brand can break that. The longer the history, the better.

Use:
  python estimate.py --xlsx Bazaar_Radar.xlsx --asin B0XXXXXXX [--members 800]
  python estimate.py --xlsx Bazaar_Radar.xlsx --top 5
or in GitHub Actions: BR_MODE=estimate (BR_ESTIMATE_ASINS, AFF_MEMBERS).
"""
import argparse, math, random, re, statistics
from datetime import date
import pandas as pd

import season, compliance

N_SIM = 4000
HORIZON_DAYS = 90
PRIOR_SLOPE = -0.75          # log-units vs log-rank when a category has <3 anchors (assumption; long-tail shape)
ANCHOR_UPLIFT = (1.0, 1.9)   # "100+ bought" means 100–199 → uniform multiplier on the floor
DEFAULTS = {
    "commission_pct": (2.0, 8.0),      # ASSUMPTION until COMMISSION tab is filled (check Associates Central)
    "conversion": (0.02, 0.08),        # ASSUMPTION: orders per click, until AFF_ACTUALS has data
    "ctr_per_member_week": (0.01, 0.04),  # ASSUMPTION: share of group members who click a product post
    "seller_share": (0.03, 0.10),      # ASSUMPTION: your share of the listing's/niche's sales as a new seller
}


def _num(x):
    try:
        v = float(str(x).replace(",", "").replace("₹", "").replace("%", ""))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pct(lo_hi_or_val):
    return lo_hi_or_val if isinstance(lo_hi_or_val, tuple) else (lo_hi_or_val, lo_hi_or_val)


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(p * len(xs))))]


# ── data prep ────────────────────────────────────────────────────────
def best_rank_table(hist):
    bs = hist[hist["list"] == "bestsellers"].copy()
    bs["rank"] = pd.to_numeric(bs["rank"], errors="coerce")
    return bs.dropna(subset=["rank"])


def retention(bs, cat):
    """Daily probability a product in the top-100 is still there tomorrow, by rank band."""
    c = bs[bs["category_slug"] == cat]
    days = sorted(c["date"].unique())
    out = {}
    for lo, hi in ((1, 20), (21, 50), (51, 100)):
        stay = tot = 0
        for a, b in zip(days, days[1:]):
            today = set(c[(c["date"] == a) & c["rank"].between(lo, hi)]["asin"])
            nxt = set(c[c["date"] == b]["asin"])
            tot += len(today); stay += len(today & nxt)
        out[(lo, hi)] = (stay + 1) / (tot + 2) if tot else 0.95   # Laplace-smoothed
    return out, len(days)


def anchors(hist_bs, detail_hist, manual):
    """(category, rank, bought_floor) from product pages + manual SALES_ANCHORS."""
    rows = []
    srcs = []
    if detail_hist is not None and not detail_hist.empty:
        srcs.append(detail_hist[["date", "asin", "bought_past_month_min"]])
    if manual is not None and not manual.empty and {"date", "asin", "bought_past_month"} <= set(manual.columns):
        srcs.append(manual.rename(columns={"bought_past_month": "bought_past_month_min"})[["date", "asin", "bought_past_month_min"]])
    if not srcs:
        return pd.DataFrame(columns=["category_slug", "rank", "units"])
    d = pd.concat(srcs, ignore_index=True)
    d["units"] = d["bought_past_month_min"].map(_num)
    d = d[d["units"].fillna(0) > 0]
    r = hist_bs.groupby(["asin", "date"]).agg(rank=("rank", "min"), category_slug=("category_slug", "first")).reset_index()
    m = d.merge(r, on=["asin", "date"], how="inner")
    return m[["category_slug", "rank", "units"]]


def sales_curve(anc, cat):
    """→ (a, b, sigma, n, source) for log(units) = a + b*log(rank), or None."""
    c = anc[anc["category_slug"] == cat]
    src = f"{len(c)} anchors in this category"
    if len(c) < 1:
        if len(anc) < 3:
            return None
        c, src = anc, f"no anchors in this category — using {len(anc)} from other categories (wide)"
    x = [math.log(r) for r in c["rank"]]
    y = [math.log(u) for u in c["units"]]
    if len(c) >= 3 and len(set(c["rank"])) >= 3:
        mx, my = statistics.mean(x), statistics.mean(y)
        sxx = sum((xi - mx) ** 2 for xi in x)
        b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx if sxx else PRIOR_SLOPE
        b = min(-0.2, max(-1.5, b))                 # keep physically sensible
        a = my - b * mx
        res = [yi - (a + b * xi) for xi, yi in zip(x, y)]
        sigma = max(0.35, statistics.pstdev(res))
    else:
        b = PRIOR_SLOPE
        a = statistics.mean(yi - b * xi for xi, yi in zip(x, y))
        sigma = 0.8
        src += " (slope assumed)"
    if "other categories" in src:
        sigma = max(sigma, 1.0)
    return a, b, sigma, len(c), src


def region(states, title):
    if states is None or states.empty:
        return [], "No state data yet (weekly state run)."
    toks = set(re.findall(r"[a-z]+", str(title).lower()))
    st = states.copy()
    st = st[st["date"] == st["date"].max()]
    rel = [k for k in st["keyword"].unique() if set(str(k).lower().split()) <= toks]
    if not rel:
        return [], "No state data for this product's search words yet — it is added to the weekly state run when it ranks in the top rising products."
    s = st[st["keyword"].isin(rel)].copy()
    s["i"] = pd.to_numeric(s["interest_3m"], errors="coerce")
    s["rc"] = pd.to_numeric(s["rank_change"], errors="coerce")
    s = s[s.get("low_signal", "False").astype(str) != "True"] if "low_signal" in s else s
    top = s.groupby("state").agg(i=("i", "mean"), rc=("rc", "mean")).sort_values("i", ascending=False).head(5)
    return [(st_, round(r.i), r.rc) for st_, r in top.iterrows()], f"keyword(s): {', '.join(rel)}"


def commission_for(cat, comm):
    if comm is not None and not comm.empty and {"category_slug", "commission_pct"} <= set(comm.columns):
        v = comm.loc[comm["category_slug"] == cat, "commission_pct"].map(_num).dropna()
        if len(v):
            return (float(v.iloc[0]),) * 2, "your COMMISSION tab"
    return DEFAULTS["commission_pct"], "ASSUMED 2–8% — fill COMMISSION tab"


def actual_conversion(actuals):
    if actuals is None or actuals.empty or not {"clicks", "ordered_items"} <= set(actuals.columns):
        return None
    c = actuals["clicks"].map(_num).fillna(0).sum()
    o = actuals["ordered_items"].map(_num).fillna(0).sum()
    if c < 200:                                 # too few clicks to trust
        return None
    p = o / c
    se = math.sqrt(max(p * (1 - p), 1e-6) / c)
    return (max(0.001, p - 2 * se), p + 2 * se), f"your Associates data ({int(c)} clicks)"


# ── the estimate ─────────────────────────────────────────────────────
def analyze(asin, data, members=0, today=None, seed=7):
    rnd = random.Random(seed)
    hist = data["HISTORY"]
    bs = best_rank_table(hist)
    today = today or str(hist["date"].max())
    p = hist[hist["asin"].astype(str) == asin]
    if p.empty:
        return {"asin": asin, "error": "ASIN not in HISTORY (not on any tracked list in the last 60 days)."}
    titled = p[p["title"].fillna("").str.len() > 3]
    title = titled["title"].iloc[-1] if not titled.empty else asin
    cat = p["category_slug"].mode().iloc[0]
    pb = bs[bs["asin"].astype(str) == asin].groupby("date")["rank"].min().sort_index()
    price = _num(p.sort_values("date")["price_inr"].dropna().iloc[-1]) if p["price_inr"].notna().any() else None
    rank_now = float(pb.iloc[-1]) if len(pb) and pb.index[-1] == today else None
    days_seen = len(pb)
    trend = (float(pb.iloc[0]) - float(pb.iloc[-1])) if len(pb) >= 2 else 0.0
    ret, n_days = retention(bs, cat)
    seas_flag = compliance.seasonal_flag(title)
    win = season.window(seas_flag, today)
    comp = compliance.compliance_flags(title, cat)
    calc = data.get("CALCULATOR")
    crow = calc[calc["ASIN"].astype(str) == asin] if calc is not None and "ASIN" in calc else pd.DataFrame()
    assumptions, blockers = [], []

    # survival: days the product stays promotable/sellable within 90 days
    band = next(((lo, hi) for (lo, hi) in ret if rank_now and lo <= rank_now <= hi), (51, 100))
    p_stay = ret[band] if rank_now else 0.0
    def life():
        if not rank_now:
            return 0
        d = 0
        while d < HORIZON_DAYS and rnd.random() < p_stay:
            d += 1
        return d
    lives = [life() for _ in range(N_SIM)]

    # ── affiliate ──
    comm, comm_src = commission_for(cat, data.get("COMMISSION"))
    conv = actual_conversion(data.get("AFF_ACTUALS"))
    conv, conv_src = conv if conv else (DEFAULTS["conversion"], "ASSUMED 2–8% — add AFF_ACTUALS")
    if "ASSUMED" in comm_src: assumptions.append("commission 2–8%")
    if "ASSUMED" in conv_src: assumptions.append("conversion 2–8% of clicks")
    aff_ok = win["affiliate_ok"] and "EXPLOSIVES" not in comp
    epc, aff = [], []
    for i in range(N_SIM):
        e = (price or 0) * rnd.uniform(*conv) * rnd.uniform(*comm) / 100
        epc.append(e)
        if members and aff_ok:
            weeks = min(lives[i], HORIZON_DAYS) / 7
            if win["days_to"] is not None:               # seasonal: only until the event
                weeks = min(weeks, max(0, win["days_to"] - season.AFF_MIN_LEAD) / 7)
            clicks = members * rnd.uniform(*DEFAULTS["ctr_per_member_week"]) * weeks
            aff.append(clicks * e)
    if members:
        assumptions.append("1–4% of members click each product post")
    if not price:
        blockers.append("no price seen — affiliate value unknown")

    # ── selling (flip / own variant) ──
    anc = anchors(bs, data.get("DETAIL_HISTORY"), data.get("SALES_ANCHORS"))
    curve = sales_curve(anc, cat)
    profit_unit, profit_src = None, ""
    if not crow.empty:
        for col, src in (("Net profit per unit (₹)", "your quote (CALCULATOR)"),):
            v = _num(crow[col].iloc[0]) if col in crow else None
            if v is not None:
                profit_unit, profit_src = v, src
        if profit_unit is None and "Est. margin % at indicative cost" in crow and price:
            m = _num(crow["Est. margin % at indicative cost"].iloc[0])
            if m is not None:
                profit_unit, profit_src = price * (m / 100 if abs(m) > 1 else m), "IndiaMART indicative margin (not a quote)"
    units_now, flip = [], []
    if curve and rank_now:
        a, b, sig, n_anc, curve_src = curve
        assumptions.append(f"your share of sales {int(DEFAULTS['seller_share'][0]*100)}–{int(DEFAULTS['seller_share'][1]*100)}%")
        for i in range(N_SIM):
            u = math.exp(a + b * math.log(rank_now) + rnd.gauss(0, sig)) * rnd.uniform(*ANCHOR_UPLIFT)
            units_now.append(u)
            if profit_unit is not None and win["stock_ok"] and "EXPLOSIVES" not in comp:
                flip.append(u * rnd.uniform(*DEFAULTS["seller_share"]) * lives[i] / 30 * profit_unit)
    else:
        curve_src = "no sales anchors yet"
        blockers.append("no sales anchors → can't convert rank to units (add 3+ rows to SALES_ANCHORS)")
    if profit_unit is None:
        blockers.append("no profit per unit — log a supplier quote (SUPPLIER_QUOTES) or get an IndiaMART estimate")
    if not win["stock_ok"] and seas_flag:
        blockers.append(f"stock: {win['note']}")
    if "EXPLOSIVES" in comp:
        blockers.append("firecracker/explosives — excluded")
    if not rank_now:
        blockers.append("not in today's top-100 bestsellers — wave may be over")

    regs, reg_src = region(data.get("GTRENDS_STATES"), title)

    # confidence grade
    pts = (n_days >= 28) + (curve is not None and curve[3] >= 5 and "other" not in curve[4]) + \
          ("quote" in profit_src) + ("Associates" in conv_src)
    grade = {4: "A", 3: "B", 2: "C"}.get(int(pts), "D")

    def rng(xs):
        return (round(_q(xs, .1)), round(_q(xs, .5)), round(_q(xs, .9))) if xs else None

    return {"asin": asin, "title": title, "category": cat, "price": price, "rank_now": rank_now,
            "days_seen": days_seen, "rank_gain": trend, "history_days": n_days,
            "p_stay_daily": round(p_stay, 3), "median_life_days": int(_q(lives, .5)) if lives else 0,
            "season": win["note"], "compliance": comp,
            "epc": rng(epc) if price else None, "aff_3m": rng(aff) if members else None, "members": members,
            "aff_ok": aff_ok, "units_month_listing": rng(units_now), "curve": curve_src,
            "profit_unit": profit_unit, "profit_src": profit_src, "flip_3m": rng(flip),
            "regions": regs, "region_src": reg_src, "grade": grade,
            "assumptions": assumptions, "blockers": blockers, "comm_src": comm_src, "conv_src": conv_src}


def report(r, html=False):
    if "error" in r:
        return f"{r['asin']}: {r['error']}"
    b = (lambda s: f"<b>{s}</b>") if html else (lambda s: s)
    rs = lambda t: f"₹{t[0]:,} / ₹{t[1]:,} / ₹{t[2]:,}" if t else "—"
    L = [b(f"📊 {r['title'][:90]}"),
         f"{r['asin']} · {r['category']} · rank today {int(r['rank_now']) if r['rank_now'] else 'not in top-100'}"
         f" · ₹{r['price']:,.0f}" if r["price"] else f"{r['asin']} · {r['category']}",
         f"Confidence: {b(r['grade'])} (A best · D = mostly assumptions) · history {r['history_days']}d",
         f"Wave: stays in top-100 next day {r['p_stay_daily']*100:.0f}% of the time in its rank band → "
         f"median {r['median_life_days']} of next 90 days",
         (f"Season: {r['season']}" if r["season"] else "Season: not seasonal"),
         "",
         b("Affiliate (3 months, P10 / P50 / P90)")]
    L.append(f"  Per click: {rs(r['epc']).replace('₹0 / ', '₹<1 / ') if r['epc'] else '—'}  "
             f"(commission: {r['comm_src']}; conversion: {r['conv_src']})")
    if r["members"]:
        L.append(f"  With {r['members']:,} members: {rs(r['aff_3m'])}" if r["aff_ok"] else "  Not promotable now (season/compliance).")
    else:
        L.append("  Set members (--members / AFF_MEMBERS) to get a ₹ total.")
    L += ["", b("Selling it yourself (3 months net profit, P10 / P50 / P90)"),
          f"  Listing sells ~{rs(r['units_month_listing']).replace('₹', '')} units/month ({r['curve']})"
          if r["units_month_listing"] else f"  Units: — ({r['curve']})",
          f"  Profit/unit: ₹{r['profit_unit']:,.0f} ({r['profit_src']})" if r["profit_unit"] is not None else "  Profit/unit: —",
          f"  You: {rs(r['flip_3m'])}" if r["flip_3m"] else "  You: — (see what's missing)"]
    if r["regions"]:
        L += ["", b("Where it's searched most") + f" ({r['region_src']})"]
        L += [f"  {s}: interest {i}" + (" · rising" if rc and rc > 0 else "") for s, i, rc in r["regions"]]
    else:
        L += ["", f"Regions: {r['region_src']}"]
    if r["assumptions"]:
        L += ["", "Assumptions still in use: " + "; ".join(r["assumptions"])]
    if r["blockers"]:
        L += ["What's missing / blocking:"] + [f"  • {x}" for x in r["blockers"]]
    L.append(f"Compliance: {r['compliance']}")
    out = "\n".join(L)
    if html:
        import html as h
        out = h.escape(out).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    return out


def load_xlsx(path):
    x = pd.ExcelFile(path)
    tabs = ["HISTORY", "TREND_SIGNALS", "DETAIL_HISTORY", "CALCULATOR", "GTRENDS_STATES",
            "SALES_ANCHORS", "COMMISSION", "AFF_ACTUALS"]
    return {t: pd.read_excel(path, sheet_name=t, dtype=str) for t in tabs if t in x.sheet_names}


def top_asins(data, n):
    s = data["TREND_SIGNALS"].copy()
    s["_t"] = pd.to_numeric(s["trend_score"], errors="coerce")
    return s.sort_values("_t", ascending=False)["asin"].head(n).tolist()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--asin", action="append")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--members", type=int, default=0)
    a = ap.parse_args()
    d = load_xlsx(a.xlsx)
    for asin in (a.asin or []) + (top_asins(d, a.top) if a.top else []):
        print(report(analyze(asin, d, members=a.members)), "\n" + "─" * 60)
