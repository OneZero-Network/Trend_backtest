"""
Weekly B2B brief — the sample product for the Data-as-a-Service demand test.

Sells INSIGHT, not raw data: a handful of rising products with the reason, category heat,
seasonal timing, and the forward-test track record. No prices, no bulk ASIN dumps.
Output: one self-contained HTML file (email / WhatsApp / Telegram document) + a short text.

The last section asks the reader whether they'd pay and how much — that answer, from 20–30
real sellers, is the demand test. Don't build a subscription before it.
"""
import html
import pandas as pd

H = lambda x: html.escape(str(x), quote=True)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is(s, col, val="true"):
    return s[col].astype(str).str.lower() == val if col in s else pd.Series(False, index=s.index)


def durable_items(signals, n=6, per_cat=2, cat_names=None):
    """v1.51: products that have been on a bestseller list most of the last 14 days — the part of the brief that is
    backed by observation rather than by a one-week move. Sourceable only (no brands / cosmetics / food / hazmat),
    not seasonal, not currently falling, not a bill-payment tile. → [(row, cname)]"""
    s = signals.copy()
    if "durable" not in s:
        return []
    s = s[_is(s, "durable")]
    for col, bad in (("actionable", "false"), ("discount_driven", "true"), ("short_dip", "true"), ("resell_ok", "false")):
        if col in s:
            s = s[s[col].astype(str).str.lower() != bad]
    if "exit_flag" in s:
        s = s[s["exit_flag"].fillna("").astype(str) != "falling"]
    if "seasonal" in s:
        s = s[~s["seasonal"].astype(str).str.startswith("SEASONAL")]
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    s = s[s["title"].astype(str).str.len() >= 4]
    s["_d"] = pd.to_numeric(s.get("durable_score"), errors="coerce")
    out, per = [], {}
    for _, r in s.sort_values("_d", ascending=False).iterrows():
        cat = r.get("category_slug") or r.get("category")
        if per.get(cat, 0) >= per_cat:
            continue
        per[cat] = per.get(cat, 0) + 1
        out.append((r, (cat_names or {}).get(cat, cat)))
        if len(out) >= n:
            break
    return out


def exit_watch(signals, n=5, cat_names=None):
    """v1.51: durable products that have just started falling — the early part of leaving the lists.
    → [(row, cname)]"""
    s = signals.copy()
    if "durable" not in s or "exit_flag" not in s:
        return []
    s = s[_is(s, "durable") & (s["exit_flag"].fillna("").astype(str) == "falling")]
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    s["_d"] = pd.to_numeric(s.get("durable_score"), errors="coerce")
    return [(r, (cat_names or {}).get(r.get("category_slug") or r.get("category"), r.get("category")))
            for _, r in s.sort_values("_d", ascending=False).head(n).iterrows()]


def rising(signals, hist, today, n=5, picks_mod=None, cat_names=None):
    s = signals.copy()
    for col, bad in (("actionable", "false"), ("discount_driven", "true"), ("short_dip", "true")):
        if col in s:
            s = s[s[col].astype(str).str.lower() != bad]
    if "resell_ok" in s:                                    # v1.51: the brief is for sellers — brands / cosmetics / food / hazmat are not theirs to source
        s = s[s["resell_ok"].astype(str).str.lower() != "false"]
    if "exit_flag" in s:                                    # v1.51: a product that just fell is not "rising"
        s = s[s["exit_flag"].fillna("").astype(str) != "falling"]
    if "non_product" in s:
        s = s[s["non_product"].fillna("").astype(str).isin(["", "nan"])]
    seas = s.get("seasonal", pd.Series([""] * len(s), index=s.index)).astype(str).str.startswith("SEASONAL")
    s["_score"] = pd.to_numeric(s["trend_score"], errors="coerce")
    main = s[~seas].sort_values("_score", ascending=False)
    out, per = [], {}
    for _, r in main.iterrows():
        cat = r.get("category_slug") or r.get("category")
        if per.get(cat, 0) >= 2 or len(str(r.get("title", ""))) < 4:
            continue
        per[cat] = per.get(cat, 0) + 1
        f = picks_mod.trend_facts(r["asin"], cat, hist, today) if picks_mod is not None else {}
        out.append((r, f, (cat_names or {}).get(cat, cat)))
        if len(out) >= n:
            break
    seasonal = s[seas].sort_values("_score", ascending=False).head(5)
    return out, seasonal


def build(signals, heat, scorecard_line, hist, today, picks_mod, cat_names, weeks_tracked=0, early=None, ready=None, fader=""):
    """early: [(name, sources)] found by Google Trends/YouTube before Amazon ranked them (2+ sources).
    ready: [(title, gate_note)] products that held their rank 2–3 weeks (stock gate passed).
    fader: durable.fader_line(...) — observed leave-rates after a fall, '' when there is not enough history."""
    items, seasonal = rising(signals, hist, today, picks_mod=picks_mod, cat_names=cat_names)
    lasting = durable_items(signals, cat_names=cat_names)
    fading = exit_watch(signals, cat_names=cat_names)
    css = ("body{font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#1d1d1f;"
           "line-height:1.45}h1{font-size:22px;margin-bottom:4px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ddd;"
           "padding-bottom:4px}.muted{color:#6e6e73;font-size:13px}.card{border:1px solid #e3e3e6;border-radius:8px;padding:12px 14px;"
           "margin:10px 0}.why{color:#0a6b2e;font-weight:bold}.box{background:#f5f5f7;border-radius:8px;padding:12px 14px}"
           "a{color:#0a58ca}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:6px;border-bottom:1px solid #eee}"
           "@media (prefers-color-scheme:dark){body{background:#111;color:#eee}.card{border-color:#333}.box{background:#1c1c1e}"
           ".muted{color:#a1a1a6}.why{color:#5fd38d}td,th{border-color:#333}a{color:#7ab8ff}}")
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>India E-commerce Trend Brief</title><style>{css}</style></head><body>",
             f"<h1>India E-commerce Trend Brief</h1><div class='muted'>Week of {H(today)} · Amazon.in bestseller lists, "
             f"tracked daily across {len(cat_names or {})} categor{'y' if len(cat_names or {}) == 1 else 'ies'}</div>",
             "<h2>Track record</h2><div class='box'>"
             + (H(scorecard_line) if scorecard_line else "Building: picks are tracked against random same-category "
                "products for 7/14/28 days. Results appear here once enough picks have matured.")
             + f"<div class='muted'>Weeks tracked: {weeks_tracked}. Every past pick and its outcome is logged — ask for the full list.</div></div>",
             ]
    if lasting:
        parts.append("<h2>Lasting demand</h2><p class='muted'>On an Amazon.in bestseller list on most of the last 14 days, at a "
                     "real rank, not falling. This is observed presence, not a forecast. It also means other sellers already "
                     "sell it — check margin and competition before buying stock. Rank is not sales.</p>")
        for r, cname in lasting:
            note = str(r.get("durable_note", "") or "")
            comp = str(r.get("competition", "") or "")
            parts.append(f"<div class='card'><b>{H(str(r['title'])[:120])}</b><br><span class='why'>🧱 {H(note) if note and note != 'nan' else 'On the list most days'}</span>"
                         f"<br><span class='muted'>{H(cname)}</span>"
                         + (f"<br><span class='muted'>{H(comp)}</span>" if comp and comp != "nan" else "")
                         + (f"<br><span class='muted'>Compliance: {H(r['compliance'])}</span>"
                            if str(r.get("compliance", "")).startswith("REVIEW") else "")
                         + f"<br><a href='https://www.amazon.in/dp/{H(r['asin'])}'>View on Amazon.in</a></div>")
    if fading:
        parts.append("<h2>Exit watch</h2><p class='muted'>Products that were steady and have just lost 10+ places in 3 days. "
                     "Think twice before ordering stock on these.</p><ul>"
                     + "".join(f"<li>{H(str(r['title'])[:90])} — {H(str(r.get('exit_note', '')))}</li>" for r, _ in fading)
                     + "</ul>" + (f"<p class='muted'>{H(fader)}</p>" if fader else ""))
    parts.append("<h2>Rising this week</h2><p class='muted'>Climbs are the noisiest signal here — in our own history a one-week "
                 "climb said little about being on the list a week later. Treat these as things to watch, not to stock.</p>")
    if not items:
        parts.append("<p>No qualifying products yet (needs ≥ 7 days of rank history).</p>")
    for r, f, cname in items:
        why = picks_mod.trend_line(f, H(cname)) if f else ""
        comp = str(r.get("competition", "") or "")
        parts.append(f"<div class='card'><b>{H(str(r['title'])[:120])}</b><br><span class='why'>{why}</span>"
                     + (f"<br><span class='muted'>{H(comp)}</span>" if comp and comp != "nan" else "")
                     + (f"<br><span class='muted'>Compliance: {H(r['compliance'])}</span>"
                        if str(r.get("compliance", "")).startswith("REVIEW") else "")
                     + f"<br><a href='https://www.amazon.in/dp/{H(r['asin'])}'>View on Amazon.in</a></div>")
    if heat is not None and not heat.empty:
        parts.append("<h2>Category heat</h2><table><tr><th>Category</th><th>Heat (top-10 avg score)</th>"
                     "<th>Climbing products</th></tr>")
        h = heat.copy()
        h["_t"] = pd.to_numeric(h["top10_score"], errors="coerce")
        for _, c in h.sort_values("_t", ascending=False).head(6).iterrows():
            parts.append(f"<tr><td>{H(c['category'])}</td><td>{_num(c['top10_score']) or 0:.0f}</td>"
                         f"<td>{H(c.get('movers', ''))}</td></tr>")
        parts.append("</table>")
    if ready:
        parts.append("<h2>Held up for 2–3 weeks</h2><p class='muted'>In their category's top 100 on most of the last 21 days, "
                     "rank not falling, in a category where products usually stay for months — the ones worth getting "
                     "supplier quotes for.</p><ul>")
        parts += [f"<li>{H(str(t)[:100])}</li>" for t, _ in ready[:6]]
        parts.append("</ul>")
    if early:
        parts.append("<h2>Early signals</h2><p class='muted'>Rising in Google searches and/or linked by several YouTube "
                     "creators this week, but <b>not yet</b> on Amazon's bestseller lists. Unproven — interest, not sales. "
                     "We track how many reach Amazon's lists within 14 days.</p><ul>")
        parts += [f"<li>{H(str(n)[:80])} <span class='muted'>({H(src)})</span></li>" for n, src in early[:6]]
        parts.append("</ul>")
    if not seasonal.empty:
        parts.append("<h2>Seasonal watch</h2><p class='muted'>Climbing on a date-bound occasion — great for "
                     "short-term listing/ads, risky for bulk stock that arrives after the event.</p><ul>")
        for _, r in seasonal.iterrows():
            parts.append(f"<li>{H(str(r['title'])[:90])} — {H(str(r.get('seasonal', '')).replace('SEASONAL: ', ''))}</li>")
        parts.append("</ul>")
    parts.append("<h2>How this is made</h2><p class='muted'>Ranks are observed once a day from public Amazon.in "
                 "bestseller lists and scored for momentum, persistence and new-launch speed; climbs driven by "
                 "sustained price cuts are penalised. No sales figures are implied and trends can reverse. "
                 "Not affiliated with Amazon. Not investment or legal advice — compliance flags are prompts to "
                 "check, not clearances.</p>"
                 "<h2>One question</h2><div class='box'>Would this be worth paying for every week? Reply with "
                 "<b>yes / no</b> and the <b>monthly price</b> you'd pay — and which section was most useful.</div>"
                 "</body></html>")
    text = (f"📰 India E-commerce Trend Brief — week of {today}\n{scorecard_line or 'Track record: building'}\n"
            + ("\n🧱 Lasting: " + "; ".join(str(r["title"])[:40] for r, _ in lasting[:3]) if lasting else "")
            + "\n" + "\n".join(f"• {str(r['title'])[:70]}" for r, _, _ in items))
    return "".join(parts), text
