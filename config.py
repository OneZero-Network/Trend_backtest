"""
LOCKED BACKTEST DEFINITIONS — Bazaar Radar v2 protocol.

Workflow:
  1. Run the PILOT (one category, ~300 listings) — this file may still change.
  2. Size the token budget from pilot rates (`keepa_backtest.py pilot` prints it).
  3. Set LOCKED = True, commit this file, THEN do the full pull.
After LOCKED = True, any edit invalidates the backtest. `run` refuses to produce a
GO/NO-GO decision unless LOCKED is True (it still writes diagnostics).
"""
LOCKED = False

# ══ COHORT ══════════════════════════════════════════════════════════════
# "first_seen": every product in CATEGORIES listed between FIRST_SEEN_START/END.
#               Flagged = enters top ENTRY_TOP_N between listing and ENTRY_CUTOFF.
# "top_entry":  legacy v1 — products entering top-N inside FIRST_SEEN window.
COHORT_MODE      = "first_seen"
FIRST_SEEN_START = "2025-02-01"
FIRST_SEEN_END   = "2025-03-15"
ENTRY_CUTOFF     = "2025-04-13"   # moved from Apr 15: quarantine can delay scoring 2 days, outcomes must end by HORIZON_LIMIT
ENTRY_TOP_N      = 100
FLAG_DELAY_DAYS  = 3              # score on day 3 after entry → alerts are 3 days later than entry

# Last date any outcome window may touch. Startup check fails if the config can exceed it.
HORIZON_LIMIT    = "2025-07-10"   # VERIFY: day before Prime Day India 2025 starts

# Keepa amazon.in root category IDs → name. Fill with real IDs before the pilot
# (GET https://api.keepa.com/category?key=..&domain=10&category=0 lists roots).
CATEGORIES = {
    # 976419031:  "Electronics",
    # 1355016031: "Beauty",
}

# Known sale events inside the horizon (flags near them are tagged; outcomes are
# category-relative regardless). VERIFY dates.
SALE_EVENTS = [
    # ("2025-05-01", "2025-05-06", "Great Summer Sale"),
]
SALE_BUFFER_DAYS = 14

# ══ WINNER DEFINITION (all must hold) ═══════════════════════════════════
LEAD_DAYS                = 28     # outcome starts 4 weeks after the SCORE date
OUTCOME_WEEKS            = 8
WIN_TOP_N                = 50     # weekly median (category-relative) rank <= 50 ...
WIN_MIN_WEEKS            = 6      # ... in >= 6 of 8 weeks
WIN_MIN_REVIEW_GAIN      = 50     # merge-cleaned absolute reviews gained in window, AND
WIN_MIN_REVIEW_GROWTH    = 0.20   # >= 20% growth over reviews at window start
WIN_CATEGORY_RELATIVE    = True
MIN_OBSERVED_SHARE       = 0.5    # outcome window must have >=50% fresh rank days, else unobservable

# ══ MERGE (STEP-JUMP) DETECTOR ══════════════════════════════════════════
MERGE_RATIO_VS_MEDIAN = 8.0
MERGE_SHARE_OF_TOTAL  = 0.15
MERGE_MIN_ABS         = 50
MERGE_MAX_OBS_GAP_D   = 2         # judge only when observations <= 2 days apart
RATING_SHIFT          = 0.1
RANK_FLAT_RATIO       = 0.8
# Denominator = suspected step-jumps (merge_suspect + unresolvable_gap), NOT all gaps.
UNRESOLVABLE_MAX_SHARE = 0.15     # above this → merge filter not validatable on Keepa data

# ══ SCORE (v1 approximated from Keepa) ══════════════════════════════════
# new_launch is constant in a first-seen cohort → replaced by launch_speed
# (fewer days listing→top-N entry = higher). breadth is not reconstructible.
# persistence = share of days in top-N since entry (not a 14d window).
# v1 relative weights; renormalised to sum 1 at scoring time (breadth's 0.08 dropped).
WEIGHTS = {"momentum": 0.25, "mover": 0.20, "review_vel": 0.20,
           "launch_speed": 0.15, "persistence": 0.12}
LAUNCH_SPEED_FULL_DAYS = 90       # entry 0 days after listing = 1.0, >= 90 days = 0.0

# ══ DISCOUNT PENALTY — part of the score (pre-registered; same rule live & backtest) ══
# Price at score vs max of the prior LOOKBACK days. Drop >= DROP_PCT while climbing →
# momentum and mover × PENALTY. Do NOT tune these after seeing backtest results.
DISCOUNT_DROP_PCT      = 15
DISCOUNT_LOOKBACK_DAYS = 7
DISCOUNT_PENALTY       = 0.5
DISCOUNT_FRESH_HOURS   = 72     # latest price obs must be <= 72h old (Keepa samples irregularly;
                                # the live fetcher uses 36h because it snapshots daily). Pilot reports stale share.
DISCOUNT_SUSTAIN_HOURS = 48     # >= 2 real observations below threshold spanning >= 48h.
                                # Shorter dips = "dip_unconfirmed" (hijacker OR lightning deal): not penalised.

# Shadow variants — REPORT ONLY. The Go/No-Go decision always uses the primary rule above.
# Precommitment: if a shadow beats the primary, the decision does NOT change. A new rule may
# only be adopted after a fresh pre-registered test on HOLDOUT_WINDOW (never seen before).
DISCOUNT_SHADOWS = {
    "drop10_48h": {"drop_pct": 10, "sustain_hours": 48},
    "drop25_48h": {"drop_pct": 25, "sustain_hours": 48},
    "drop15_0h":  {"drop_pct": 15, "sustain_hours": 0},    # previous rule: no hijacker guard
    "no_penalty": None,
    "stale_degrade": "stale",     # 15%/48h rule + STALE_DEGRADE on momentum (critique's proposal)
}
HOLDOUT_WINDOW = ("2024-02-01", "2024-03-15")   # TEMPORAL holdout: tests drift across years. Untouched until needed.

# QUARANTINE (mirrors live): a dip_unconfirmed on the score date is not scored that day. The product
# is re-scored QUARANTINE_DAYS later using only data up to then; the outcome window starts from that
# resolution date. sustained → penalised · recovered → organic · still unresolved → unpenalised, flagged.
QUARANTINE_DAYS = 2

# CONCURRENT holdout: deterministic 20% of ASINs (hash, not random state — cannot be re-drawn).
# Shadow tables show the 80% train slice only; holdout results are written sealed, opened with
# `run --unseal-holdout` only after a shadow has been chosen on train. Primary gate uses 100%
# (nothing in it is tuned). Tests overfitting to PRODUCTS; HOLDOUT_WINDOW tests drift over TIME.
CONCURRENT_HOLDOUT_FRACTION = 0.20
HOLDOUT_SALT = "bazaar-radar-v2"

# Price-freshness: the live fetcher never has stale prices (price & rank read from the same page),
# so staleness is a Keepa artefact. It is MEASURED (report splits fresh vs stale), not penalised —
# penalising it would mark down low-rank products Keepa samples rarely, i.e. early-stage ones.
FRESH_PRICE_HOURS = 36
STALE_DEGRADE = {"after_hours": 36, "per_12h": 0.20}   # shadow only: momentum −20% per 12h stale

# ══ PRICE (exploratory diagnostics only — never gated) ══
PRICE_BANDS   = [0, 500, 1000, 2000, 5000, float("inf")]
PRICE_CUT_PCT = 15                # outcome-window median price <= score price -15% → "bought" outcome

# ══ GO / NO-GO ══════════════════════════════════════════════════════════
# GO only if ALL THREE bootstrap lower bounds clear: lift (top-decile precision ÷ base
# rate) > 1.0, pooled AUC > 0.5, and within-category AUC > 0.5 (winner-weighted mean of
# per-category AUCs; bootstrap stratified by category). Pooled-minus-within gap and the
# precision curve are report-only diagnostics.
TOP_FRACTION   = 0.10
PRECISION_CURVE_FRACTIONS = [0.05, 0.10, 0.20, 0.30, 0.50]
BOOTSTRAP_N    = 2000
CI_LEVEL       = 0.95
GO_LIFT_LOWER  = 1.0
GO_AUC_LOWER   = 0.5
GO_WITHIN_AUC_LOWER = 0.5        # within-category AUC lower bound (hard gate: trend, not category)
TARGET_LIFT    = 2.0              # effect size the power analysis is sized for
TARGET_POWER   = 0.80
SEED = 42

# ══ KEEPA BUDGET — ALL UNVERIFIED: confirm against your plan before the pilot ═══
TOKENS_PER_PRODUCT        = 2     # product + rating history (includes lowest-new & list price); team estimate 1-5
FETCH_BUYBOX              = False # Stage 2 only: Buy Box history costs extra. Turn on ONLY if the baseline
                                  # model passes the gate and we want to test price elasticity precisely.
FINDER_TOKENS_PER_REQUEST = 10    # Product Finder call; team estimate
FINDER_FIRST_SEEN_FIELD   = "trackingSince"   # VERIFY Product Finder key name (maybe listedSince)
KEEPA_REFILL_PER_MIN      = 5     # entry tier per team; client also learns refillRate from responses
FETCH_BATCH               = 20    # ASINs per product call (auto-shrinks to fit the bucket)

# ══ PILOT (stratified: equal listings per category) ═════════════════════
PILOT_PER_CATEGORY = 150          # 60/category can't estimate a ~3% entry rate; see README
SURVIVORSHIP_STALE_DAYS = 60      # product with no rank data in the final N days = likely delisted
