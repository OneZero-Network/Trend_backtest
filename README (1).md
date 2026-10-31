# Keepa backtest — Go/No-Go gate (Bazaar Radar v2 protocol)

## Protocol (do not reorder)
| Step | Command | Tokens |
|---|---|---|
| 0. Self-test | `python test_backtest.py` (~3 min), `python test_client.py`, `python test_discount_rule.py`, `python ../test_fetcher.py` | 0 |
| 1. Verify with Keepa **before** spending: Product Finder field names, token costs, and whether the finder returns ASINs that are delisted/404 today | update `FINDER_FIRST_SEEN_FIELD`, `TOKENS_PER_PRODUCT`, `FINDER_TOKENS_PER_REQUEST`, `KEEPA_REFILL_PER_MIN` | 0 |
| 2. Fill `CATEGORIES` (4–5) and `SALE_EVENTS`; leave `ENTRY_CUTOFF` = Apr 13 and `HORIZON_LIMIT` = Jul 10 (confirm the Prime Day date) | `config.py` | 0 |
| 3. Stratified pilot cohort | `python keepa_backtest.py discover --out pilot.txt --per-cat 150` | ~10 per finder page |
| 4. Pull pilot | `python keepa_backtest.py fetch --asins pilot.txt --data pilot_cache` | ~listings × cost |
| 5. Size | `python keepa_backtest.py pilot --data pilot_cache --out pilot_results` → `PILOT.md` | 0 |
| 6. Top up categories marked "NO: <10 scored" (rerun 3–5 with a higher `--per-cat`; already-pulled ASINs are skipped) | | |
| 7. **Set `LOCKED = True`, commit `config.py`** | | 0 |
| 8. Full pull | `discover --per-cat <conservative figure from PILOT.md>` → `fetch` | budgeted |
| 9. Decide | `python keepa_backtest.py run --data keepa_cache --out results` | 0 |

Set `KEEPA_API_KEY` for steps 3, 4 and 8. **Every Keepa command can be stopped and resumed:** kill it at any point and rerun the same command. `discover` keeps its progress in `<out>.state.json`; `fetch` skips ASINs that are already cached.

### Running the full pull (5–14 days)
Use an always-on VPS. The pull only calls Keepa's API, so the machine's IP address doesn't matter. Run it under `systemd` (it restarts after a reboot) or inside `tmux` (it survives an SSH disconnect but **not** a reboot). Either way, rerunning the same command resumes from the cache. Keep `KEEPA_API_KEY` in an environment file, not in shell history.

### Rate limiting (keepa_client.py)
- Checks the token balance before each call and waits until the bucket can cover it. It learns `refillRate`, `tokensLeft` and `refillIn` from each response.
- On 429, 5xx or network errors it retries the **same** request, so nothing is skipped. It waits for `refillIn` on a 429, otherwise backs off exponentially up to 15 min. It gives up after 72 h on a single request.
- HTTP 400, 401, 402, 403 and 404 stop immediately instead of retrying forever.
- ASINs that Keepa doesn't return are saved as `_missing` placeholders, so resuming doesn't keep requesting them.
- Prints an ETA. At 5 tokens/min, the bucket gives ~7,200 tokens/day, so 40k tokens ≈ 5.5 days and 100k ≈ 14 days. Run it on an always-on machine (a self-hosted runner or a VPS), not in GitHub Actions, where jobs time out after 6 h.

### What the pilot report gives you
- **Per-category** entry rate with a 95% Wilson interval, and a "usable" flag (needs ≥ 10 scored products).
- Budget sized from the **lower bound** of each category's rate. The full pull keeps the pilot's equal split across categories.
- **Survivorship check:** the share of listings whose data stops ≥ 60 days before the end of the dataset. **≈0% means the finder probably returned survivors only.** Don't proceed until Keepa support answers. If the answer is "survivors only", the report's caveat must stay in every summary.
- The merge filter's unresolvable share.

## What the v2 protocol changes from v1
- **Cohort** = every listing first seen between FIRST_SEEN_START and FIRST_SEEN_END (Feb 1 – Mar 15, 2025). A product is flagged when it enters the top 100 **by ENTRY_CUTOFF (Apr 13)**. The startup check refuses any config whose last outcome window passes HORIZON_LIMIT (the day before Prime Day).
- **Scoring on day 3** after entry (`FLAG_DELAY_DAYS = 3`). Persistence = share of days in the top 100 since entry.
- **`new_launch` replaced by `launch_speed`**: in a cohort of new listings, "listed within 90 days" is true for every product, so it can't distinguish anything. `launch_speed` measures days from listing to top-100 entry instead. `breadth` is dropped; the remaining weights are rescaled to sum to 1.
- **Winner** = category-relative weekly median rank ≤ 50 in ≥ 6 of 8 weeks, **and** ≥ 50 reviews gained (after merge cleaning), **and** ≥ 20% review growth.
- **Unresolvable share** = unresolvable gaps ÷ all suspected review jumps. If above 15%, the report marks the merge filter "not validated".
- **Gate (all three must pass)** = the lower bound of the bootstrapped **lift** (top 10% precision ÷ base rate) > 1.0, the lower bound of **pooled AUC** > 0.5, and the lower bound of **within-category AUC** > 0.5 (per-category AUCs averaged with each category weighted by its number of winners; the bootstrap resamples within each category). The gap between pooled and within-category AUC, and the precision curve, are reported only. Self-test 8b shows why: a score that only knows the category reaches a pooled AUC of 0.67 but a within-category AUC of 0.47.
- **Power** is reported on every run. A NO-GO at under 80% power is labelled **inconclusive**, not a failure.
- `run` won't output GO or NO-GO unless `LOCKED = True`. Before that, it outputs diagnostics only.

## Report diagnostics added in v2.1
- **Category-confound diagnostic:** pooled AUC next to within-category AUC, with a warning when the gap is over 0.05. The within-category AUC itself is a hard gate (above).
- **Entry-date uncertainty:** days between the last Keepa observation before entry and the first top-100 observation. With sparse sampling, the real entry may be earlier than recorded, which skews launch_speed and persistence.

## Discount penalty (pre-registered: in the score and therefore in the gate)
If the price on the score date is ≥ `DISCOUNT_DROP_PCT` (15%) below its highest price in the previous `DISCOUNT_LOOKBACK_DAYS` (7) days, while the product is climbing, momentum and mover are multiplied by `DISCOUNT_PENALTY` (0.5). The live fetcher uses the same rule. The report shows how often it triggered, the winner rate when it did, **how many winners it penalised** (cost to genuine launches with introductory pricing), and the AUC with and without it. The gate uses the penalised score. Switching to the unpenalised version after seeing results invalidates the test.

Price source: Keepa's **lowest new price** (csv[1]), which is included in the 2-token pull. It's noisier than the Buy Box: one seller briefly listing low can trigger the penalty. Watch the trigger rate in the pilot.

### Hijacker guard (v2.2)
The rule lives in `discount_rule.py`, shared by the live fetcher and the backtest. The penalty fires only when the price stays below the threshold for **≥ 2 real observations spanning ≥ 48h** (not elapsed time, because Keepa's sampling gaps would make one 12-hour dip look like days). Shorter dips are `dip_unconfirmed`: **not penalised**, and labelled "hijacker *or* Lightning Deal", because the two can't be told apart on the score date. Whether a dip bounced back is checked **afterwards, in the report only**, since it needs future data. Freshness: 72h for Keepa, 36h for the live daily snapshots.

On synthetic data, the guard's cost is measured: flash-sale catch rate falls from 92% to 71% (most misses are stale samples or cuts that started before the 7-day window), while 0 of 58 planted hijacker dips are penalised (the unguarded rule would have penalised 90%).

### Quarantine (v2.3, live and backtest)
A dip under 48h on the score date isn't judged on that day. **Live:** the product is held out of the top recommendations and Telegram list (`⏸ Quarantined`); the next daily runs resolve it automatically. **Backtest:** the product is re-scored `QUARANTINE_DAYS` (2) later using only data up to then, and its outcome window starts from that date. Results: `resolved_penalised` (flash sale caught), `resolved_organic` (dip bounced back), `unresolved`. Because of the 2-day delay, `ENTRY_CUTOFF` moved from Apr 15 to **Apr 13**, so outcomes still end by Jul 10. On synthetic data: 46 of 58 late flash sales were penalised and 64 of 66 hijacker dips released as organic.

### Stale prices: measured, not penalised
The live fetcher reads price and rank from the same page at the same moment, so its prices are never stale. Staleness only happens with Keepa. Penalising it would mark down products Keepa samples rarely, which are mostly early-stage ones, and would make the backtest differ from live. The report splits AUC by fresh (≤ 36h) vs stale price data. The proposed −20%-per-12h degradation is included as the `stale_degrade` **shadow** only.

### Two holdouts, testing two different things
- **Concurrent (20% of ASINs, assigned by hash so it can't be reshuffled):** shadow tables use the 80% only; the 20% is written to `HOLDOUT_SEALED.md` and printed only with `run --unseal-holdout`. It checks for overfitting to *products*. With ~25 winners it has low power: it can reject a rule but rarely confirm one.
- **Time window (Feb 1 – Mar 15, 2024):** checks whether a rule holds in a different year. It's for testing a rule that has already been chosen, never for tuning.
- The main gate uses 100% of products, since nothing in it is tuned.

### Shadow thresholds (report only)
Alongside the primary rule (15%, 48h), the report shows 10%/48h, 25%/48h, 15% with no guard, and no penalty: trigger rate, winners penalised, AUC, lift and within-category AUC for each. **Precommitment:** a shadow that looks better does not change the decision. A new rule can only be adopted after a fresh pre-registered test on `HOLDOUT_WINDOW` (Feb 1 – Mar 15, 2024), which stays untouched until then.

## Budget: Buy Box history is stage 2 only
`FETCH_BUYBOX = False` and `TOKENS_PER_PRODUCT = 2`. Validate the baseline model first. Turn Buy Box history on (≈2× tokens, unverified) **only if** the model passes the gate and we want a precise test of price sensitivity. The parser is kept and tested.

## Price diagnostics (exploratory: never gated)
Uses the lowest new price (csv[1]), list price (csv[4]), Amazon's own price (csv[0]) and, in stage 2, the Buy Box (csv[18]). The report shows:
- Winner rate by **price band** at the score date, with 95% intervals.
- Single-feature AUC for 14-day price change, discount vs list price, and number of sellers.
- Winner rate when Amazon itself is selling the product versus when it isn't.
- **Winners who needed a price cut:** the share of winners whose median price during the outcome window was ≥ 15% below the score-date price. Above 30% means much of the winning was paid for with discounts.

Do **not** adjust the score weights using these numbers after seeing results. If one looks predictive, add it to a *new* pre-registered config and test it on a different time window.

## Self-test coverage
Horizon check · cohort excludes listings outside the window · merge recall and precision · Keepa gaps never mistaken for merges · no use of future data · persistence and launch_speed vary · synthetic data with planted signal passes · synthetic data with no signal fails · shuffled scores pass ≤ 1 in 20 · power simulation.

## Unverified until checked with Keepa
- Product Finder field names in `discover` (`trackingSince_gte/lte`, `rootCategory`). Check them against Keepa's docs before spending tokens.
- Whether Product Finder returns products later removed from Amazon (survivorship).
- `TOKENS_PER_PRODUCT` (set to 2). Check your plan's cost for requests with `rating=1`.
