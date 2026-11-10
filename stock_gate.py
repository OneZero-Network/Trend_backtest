"""
Stock gate — "never buy stock because something is rising THIS week".

Bulk stock takes ~3–6 weeks to arrive (quote → sample → bulk → Amazon inbound). So a product is
STOCK-READY only when ALL hold:
  1. history: we have ≥ MIN_HISTORY days of data (otherwise we can't judge persistence)
  2. persistence: in its category's top-100 on ≥ MIN_DAYS_IN of the last LOOKBACK days, first seen
     ≥ MIN_DAYS_IN days ago (survived 2+ weeks, not a spike)
  3. not fading: median rank of the last 7 days is not worse than the 7 days before
  4. long waves: in its category/rank band, products typically stay ≥ MIN_LIFE days
     (from daily retention: expected stay = 1 / (1 − p_stay))
  5. season: if seasonal, today is inside the stock window (season.py)
  6. not flagged EXPLOSIVES
Otherwise the verdict says which test failed and, for WATCH, how many days to go.
This is a gate, not a buy signal: a READY product still needs a quote, sample and margin check.
"""
import pandas as pd
import season

MIN_HISTORY = 21
LOOKBACK = 21
MIN_DAYS_IN = 14
MIN_LIFE = 60


def _bs(hist):
    b = hist[hist["list"] == "bestsellers"].copy()
    b["rank"] = pd.to_numeric(b["rank"], errors="coerce")
    return b.dropna(subset=["rank"])


def retention_by_band(bs):
    """{(category, band): p_stay} — daily chance a product in that band is still in the top-100 tomorrow."""
    out = {}
    for cat, c in bs.groupby("category_slug"):
        days = sorted(c["date"].unique())
        by_day = {d: set(g["asin"]) for d, g in c.groupby("date")}
        for band, (lo, hi) in {"1-20": (1, 20), "21-50": (21, 50), "51-100": (51, 100)}.items():
            stay = tot = 0
            for a, b in zip(days, days[1:]):
                today = set(c[(c["date"] == a) & c["rank"].between(lo, hi)]["asin"])
                tot += len(today); stay += len(today & by_day.get(b, set()))
            out[(cat, band)] = (stay + 1) / (tot + 2) if tot else 0.9
    return out


def _band(rank):
    return "1-20" if rank <= 20 else "21-50" if rank <= 50 else "51-100"


def gate_all(hist, signals, today, min_history=None, lookback=None, min_days_in=None):
    """→ {asin: verdict string} for every ASIN in signals. The optional overrides are for the
    ADMIN-ONLY preview (fewer days); the calculator's Decision always uses the real thresholds."""
    MIN_HISTORY_ = min_history or MIN_HISTORY
    LOOKBACK_ = lookback or LOOKBACK
    MIN_DAYS_IN_ = min_days_in or MIN_DAYS_IN
    if hist is None or hist.empty or signals is None or signals.empty:
        return {}
    bs = _bs(hist)
    days_all = sorted(bs["date"].unique())
    n_hist = len(days_all)
    ret = retention_by_band(bs) if n_hist >= 2 else {}
    recent = days_all[-LOOKBACK_:]
    best = bs.groupby(["asin", "date"]).agg(rank=("rank", "min"), cat=("category_slug", "first")).reset_index()
    first_seen = best.groupby("asin")["date"].min().to_dict()
    days_in = best[best["date"].isin(recent)].groupby("asin")["date"].nunique().to_dict()
    series = {a: g.set_index("date")["rank"] for a, g in best.groupby("asin")}
    cats = best.groupby("asin")["cat"].agg(lambda x: x.mode().iloc[0]).to_dict()
    out = {}
    for _, s in signals.iterrows():
        a = str(s["asin"])
        comp, seas = str(s.get("compliance", "")), s.get("seasonal", "")
        if "EXPLOSIVES" in comp:
            out[a] = "❌ NO — firecracker/explosives"; continue
        w = season.window(seas, today)
        if w["event"] and not w["stock_ok"]:
            out[a] = f"❌ NO — season: {w['note']}"; continue
        if n_hist < MIN_HISTORY_:
            out[a] = f"⏳ NEED DATA — {n_hist}/{MIN_HISTORY_} days of history"; continue
        din = days_in.get(a, 0)
        age = (pd.Timestamp(today) - pd.Timestamp(first_seen.get(a, today))).days
        if a not in series:
            out[a] = "❌ NO — not on the bestseller list"; continue
        if din < MIN_DAYS_IN_ or age < MIN_DAYS_IN_:
            need = max(MIN_DAYS_IN_ - din, MIN_DAYS_IN_ - age, 1)
            out[a] = f"👀 WATCH — in top-100 {din}/{LOOKBACK_} days; check again in ~{need}d"; continue
        r = series[a]
        last7 = r[r.index.isin(days_all[-7:])]
        prev7 = r[r.index.isin(days_all[-14:-7])]
        if len(last7) and len(prev7) and last7.median() > prev7.median() + 5:
            out[a] = f"❌ FADING — median rank #{prev7.median():.0f} → #{last7.median():.0f}"; continue
        if days_all[-1] not in r.index:
            out[a] = "❌ FADING — dropped out of the top-100 today"; continue
        p = ret.get((cats.get(a), _band(r.iloc[-1])), 0.9)
        life = 1 / max(1 - p, 1e-3)
        if life < MIN_LIFE:
            out[a] = f"❌ SHORT WAVES — at this rank products last ~{life:.0f} days on average"; continue
        out[a] = f"✅ STOCK-READY — {din}/{LOOKBACK_} days in top-100, typical stay ~{min(life, 365):.0f}d; get quotes"
    return out


def preview(hist, signals, today):
    """ADMIN-ONLY early look while history < MIN_HISTORY: same tests, scaled to the days we have
    (persistence = in top-100 on ≥ 2/3 of them). Less reliable — never used for decisions."""
    if hist is None or hist.empty:
        return {}, 0
    n = hist.loc[hist["list"] == "bestsellers", "date"].nunique()
    if n >= MIN_HISTORY or n < 5:
        return {}, n
    return gate_all(hist, signals, today, min_history=n, lookback=n, min_days_in=max(3, -(-2 * n // 3))), n
