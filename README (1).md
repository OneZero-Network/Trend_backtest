# 📡 Bazaar Radar — India E-commerce Trend Fetcher

Sibling of `fortress_fetcher`, built the same way: **GitHub Actions → Google Sheets → Telegram**, with `trigger.html` for one-tap manual runs.

## What it collects (daily, 09:30 IST)
| Tab | Source | What it tells you |
|---|---|---|
| `AMZ_BESTSELLERS` | Amazon.in Best Sellers, top 100 × 19 categories | What sells **now** |
| `AMZ_MOVERS` | Amazon.in Movers & Shakers | Biggest 24h rank jumps (**leading** signal) |
| `AMZ_NEW_RELEASES` | Amazon.in New Releases | New launches gaining traction |
| `GTRENDS` | Google Trends India: daily trending + rising "Shopping" queries for your seed keywords | Demand **before** it reaches carts |
| `HISTORY` | Rolling 60-day log of every run | Raw material for the trend maths |
| `TREND_SIGNALS` | Computed | One row per product, ranked by `trend_score` |
| `CATEGORY_HEAT` | Computed | Which categories are heating up |
| `AMZ_DETAILS` | Product pages of the top 30 scored products | MRP, discount %, **"bought in past month"**, number of sellers, coupons, deals, Amazon fulfilment, COD |
| `DETAIL_HISTORY` | Rolling 60 days of product-page details | Growth in "bought in past month" over time |
| `GTRENDS_STATES` | (weekly job) Google Trends interest by state for each seed keyword (last 3 months vs last month) | Which states search for it, and which are rising |
| `STATE_SUMMARY` | (weekly job) Computed | States ranked by how many keywords they lead or are rising on |
| `RUN_LOG` | Run diagnostics | Blocks, layout changes, errors |

## Trend score (0–100)
| Component | Weight | Meaning |
|---|---|---|
| momentum | 25% | Best-seller rank climb vs ~7 days ago |
| mover | 20% | On Movers & Shakers (log of % gain; "previously unranked" = max) |
| review_vel | 20% | Reviews/day growth (the closest public proxy for units sold) |
| new_launch | 15% | On New Releases **and** Best Sellers |
| persistence | 12% | Days on any list in last 14 (filters one-day spikes) |
| breadth | 8% | Appears on several list types |

Labels: 🚀 Breakout launch · ⚡ Sudden spike (verify) · 📈 Sustained climber · 🛒 High volume seller · 🆕 New release.
**Confidence is "low" for the first 7 days.** Momentum and review velocity need history to mean anything.

## v1.1 additions: price and regions
**Price.** List pages give the selling price, the variant range (₹X – ₹Y) and MRP when shown. Product pages (top 30 only, set with `BR_DETAIL_TOP_N`) add discount %, coupons, bank offers, "Limited time deal", number of sellers and "bought in past month".
- **Discount penalty (in the score):** if a product's rank climbed while its price stayed ≥ 15% below its highest price of the previous 7 days **for at least 3 daily snapshots (48h)**, its momentum and mover components are **halved**, and it's labelled `💸 Discount-driven (verify)`. `trend_score_unpenalized` is kept next to it for transparency. A dip shorter than 48h is **quarantined** (`⏸ Quarantined`): held out of the top recommendations and Telegram list until the next daily runs decide. If the price recovers, the product is released with its full score; if it stays down for 3 snapshots, the penalty applies. The rule lives in `discount_rule.py`, shared with the backtest; the constants match `backtest/config.py`, and `test_fetcher.py` fails if they ever differ, so the live score is always the score that was backtested.
- `crowded_listing`: ≥ 10 sellers on one listing, meaning a price war is likely for a new seller.

**Regions: a separate weekly job (`state_trends.yml`, Sundays), not part of the daily run.** It makes about 20 Google calls that are often rate-limited, so it runs on its own and its failures can't affect the daily Amazon data. It's for ad geo-targeting, not inventory or RTO decisions. Amazon doesn't publish sales by state, and neither does anyone else for free. `GTRENDS_STATES` is Google search interest by state: each state's *share* of its own searches, scaled 0–100 against the top state for that keyword. It is **not sales and not volume.** States with interest below 10 are marked `low_signal` and left out of `STATE_SUMMARY`. States are compared by rank, not by raw values, because the 0–100 scale resets for every query.

## v1.2: automatic calculator, compliance flags, YouTube check
**`CALCULATOR` tab (auto-updated daily).** The top 25 *actionable* signals (quarantined products excluded) are added with price, category, sellers, "bought in past month", compliance and competition. You type the **IndiaMART quote (ex-GST)** and MOQ in the yellow cells. Blue cells are defaults pulled by category from `CALC_ASSUMPTIONS`; type a number to override one. Every run:
- keeps your quotes, MOQs, overrides and notes (rows are matched by ASIN)
- updates existing rows with today's status (a product that goes into quarantine shows **HOLD**)
- drops rows with no quote that haven't appeared for 21 days, and keeps rows you add with no ASIN
- never overwrites `CALC_ASSUMPTIONS` after creating it once

Decision order: quarantined → HOLD · no quote → NEED SOURCING QUOTE · margin < min → NO-GO · ROI < min → NO-GO · compliance flag → HOLD (certification) · otherwise "GO — still verify compliance & sample".

**The fee and cost defaults are placeholders, not Amazon's real fees.** Replace them in `CALC_ASSUMPTIONS` with figures from Seller Central and your courier rates.

**Why no AI agent fills in sourcing costs:** IndiaMART has no public product-search API, and listed prices aren't real quotes (MOQ, negotiation and quality vary). An automated guess would give confident wrong margins. That one cell stays manual on purpose.

**Compliance (`compliance.py`):** category plus keyword rules give `REVIEW: BIS / FSSAI / CDSCO…`. **An unflagged product reads `NOT FLAGGED (≠ cleared — verify)`; it is never shown as cleared.** Matching by category catches what keywords miss (any product in Toys gets the toys certification flag).

**Competition note:** seller count is read together with demand. Few sellers on a *bestseller* usually means a brand-owned listing (you'd need your own variant), not a lack of demand. ≥ 10 sellers means a price war is likely, but high demand can still justify entering.

**YouTube (`youtube_check.py`, optional):** set the secret `YOUTUBE_API_KEY` (free Google Cloud key with the YouTube Data API v3 enabled). It searches "<product> review / unboxing" from the last 30 days and reports views per day, like rate, the share of negative titles ("fake", "explode", "don't buy"…), and the **top 3 video links to watch yourself.** About 100 quota units per product, so 15 products ≈ 1,500 of the free 10,000/day.

## Troubleshooting
| Telegram / log says | Meaning | Fix |
|---|---|---|
| `Sheet not updated: … refused access (403)` | The sheet isn't shared with the service account, or `GOOGLE_SHEET_ID` is a different sheet | Share the sheet with the `client_email` shown in the message as **Editor**; copy the ID between `/d/` and `/edit` |
| `not found (404)` | Wrong `GOOGLE_SHEET_ID` | Copy it again from the sheet URL |
| `Amazon lists: 0 ok · N blocked` | Amazon is blocking GitHub's servers | Run from your own computer (see "Run locally") or use fewer categories |
| `parsed 0 items — layout change?` | Amazon page structure not recognised | The page is saved to `output/debug/` inside the run artifact. Send it over for a parser fix |

If the sheet can't be opened, the run still fetches Amazon and saves everything as a downloadable **artifact** (`bazaar-radar-output`, on the run's page), then shows red so you notice.

## Setup
1. New repo → add `trend_fetcher.py`, `.github/workflows/trend_fetcher.yml`, `trigger.html`.
2. Secrets (same as fortress, so you can reuse the service account): `GOOGLE_CREDS_JSON`, `GOOGLE_SHEET_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Use a **new** sheet and share it with the service account as Editor.
3. Optional repo *Variables*: `BR_CATEGORIES` (e.g. `beauty,kitchen,sports`), `BR_SEED_KEYWORDS` (e.g. `air fryer,sunscreen,kurti`), `BR_DETAIL_TOP_N` (default 30, 0 = off). The weekly states job uses `BR_SEED_KEYWORDS` too.
4. Offline tests: `python test_fetcher.py` and `python test_calculator.py` (no network).
5. Local test without Google: `pip install requests pandas beautifulsoup4 curl_cffi pytrends && python trend_fetcher.py` writes CSVs to `./output/`.

## Known limits — read before trusting the numbers
- **Amazon blocks datacenter IPs.** GitHub runners are Azure IPs. Expect some lists to hit the captcha wall; `RUN_LOG` and the Telegram digest report `blocked` counts. If most lists are blocked for days, use a self-hosted runner on a home machine, or a residential proxy.
- **Amazon's Conditions of Use prohibit automated scraping.** Keep volume low (it's throttled to ~1 request every 4–6 s, once a day). For anything commercial, use official data (Amazon Product Advertising API, which requires an Associates account with qualifying sales) or a licensed data vendor.
- **Flipkart, Meesho, Myntra and Nykaa are not included.** Their listing pages render in JavaScript behind stronger bot protection; a Playwright-based add-on would be needed and would break often.
- **Rank ≠ units.** #1 in Watches might sell less than #80 in Electronics. Compare within a category, never across.
- **Layout changes:** if a list parses 0 items, the log says `layout change?`. The parser uses `#gridItemRoot`, `data-asin` and `/dp/` links, which change less often than CSS classes, but it will break eventually.
- **pytrends is unofficial** and is often rate-limited (HTTP 429). The run continues without it.
