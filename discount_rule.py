"""
Discount rule — ONE implementation shared by trend_fetcher.py (live) and
backtest/keepa_backtest.py, so the live score is always the backtested score.

discount_status(obs, t_end, ...) looks ONLY at observations <= t_end (no look-ahead).

  reference  = max observed price in [t_end - lookback_days, t_end]
  threshold  = reference × (1 - drop_pct/100)
  run        = the trailing run of observations <= threshold, ending at the latest observation

  status
    "none"             latest observation is above threshold (or no reference)
    "sustained"        run has >= 2 real observations spanning >= sustain_hours
                       → penalise (price cut held long enough to be a brand/seller decision)
    "dip_unconfirmed"  below threshold but run is 1 observation or < sustain_hours
                       → NOT penalised. Could be a listing hijacker OR a Lightning Deal;
                         we cannot tell at score time without future data.
    "stale"            latest observation older than fresh_hours → no decision
    "no_data"

Why "real observations" and not elapsed time: Keepa samples low-rank products every few
days. A single 12-hour hijacker dip carried forward would otherwise look like a multi-day cut.
"""
import numpy as np
import pandas as pd


def discount_status(obs, t_end, drop_pct, lookback_days, sustain_hours, fresh_hours=36):
    out = {"status": "no_data", "drop_pct": np.nan, "run_hours": 0.0, "run_obs": 0,
           "reference": np.nan, "threshold": np.nan, "age_hours": np.nan}
    if obs is None or len(obs) == 0:
        return out
    t_end = pd.Timestamp(t_end)
    past = obs[obs.index <= t_end].dropna()
    if len(past):
        out["age_hours"] = (t_end - past.index[-1]).total_seconds() / 3600
    s = obs[(obs.index <= t_end) & (obs.index >= t_end - pd.Timedelta(days=lookback_days))].dropna()
    if s.empty:
        return out
    s = s.sort_index()
    if (t_end - s.index[-1]) > pd.Timedelta(hours=fresh_hours):
        out["status"] = "stale"
        return out
    ref = float(s.max())
    if not np.isfinite(ref) or ref <= 0:
        return out
    thr = ref * (1 - drop_pct / 100.0)
    latest = float(s.iloc[-1])
    out.update(reference=ref, threshold=thr, drop_pct=(latest / ref - 1) * 100)
    if latest > thr:
        out["status"] = "none"
        return out
    vals, idx = s.values, s.index
    i = len(vals) - 1
    while i >= 0 and vals[i] <= thr:
        i -= 1
    run_start = idx[i + 1]
    n_run = len(vals) - (i + 1)
    hours = (idx[-1] - run_start).total_seconds() / 3600
    out.update(run_hours=hours, run_obs=n_run)
    need_obs = 2 if sustain_hours > 0 else 1          # sustain_hours=0 reproduces the unguarded rule
    out["status"] = "sustained" if (n_run >= need_obs and hours >= sustain_hours) else "dip_unconfirmed"
    return out


def reverted_after(obs, t_end, threshold, within_hours=48):
    """POST-HOC ONLY (uses future data — never call from scoring). Did the price come back
    above threshold within `within_hours` after t_end? → short dip that snapped back."""
    if obs is None or len(obs) == 0 or not np.isfinite(threshold):
        return None
    t_end = pd.Timestamp(t_end)
    fut = obs[(obs.index > t_end) & (obs.index <= t_end + pd.Timedelta(hours=within_hours))].dropna()
    if fut.empty:
        return None
    return bool((fut > threshold).any())
