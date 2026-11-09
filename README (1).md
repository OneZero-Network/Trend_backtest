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

## v1.3 changes after the first live run (Oct 31)
- **Seasonal flag** (`📅 … — seasonal`, Calculator decision **HOLD — seasonal**): Diwali, Halloween, Holi, Rakhi, Christmas, monsoon, summer, winter and so on. The first live list was mostly Diwali lights, diyas and Halloween costumes on Oct 31. With weeks of sourcing lead time, bulk stock would arrive after the event.
- **Calculator waits for 7 days of history** (`BR_CALC_MIN_DAYS`) before adding rows. On day 1 every product scored the same 31, so the "top 25" was an arbitrary tie.
- **Books excluded** from the calculator (`BR_CALC_EXCLUDE`, default `books`). At most **40 rows waiting for a quote** are kept; rows with your quotes are always kept.
- Category is now the slug (`kitchen`, not "Home & Kitchen"), so per-category fees apply.
- ISBN ASINs keep their leading zeros (data tabs are written RAW).
- An empty `BR_SEED_KEYWORDS` variable now falls back to the defaults (it caused "0 rows from 0 seeds").
- The Telegram digest escapes `<`, and falls back to plain text if Telegram still rejects it.
- Analog watches are no longer flagged as electronics, and "tea light" candles no longer as food.
- Product pages where the seller count can't be parsed are saved to `debug/` in the run artifact.

## v1.4: Movers & Shakers (diagnosed from the real saved pages, Oct 31)
Amazon.in served **"Sorry, there are no movers and shakers available in this category"** with an empty product list in every category. The parser was fine; Amazon had no data to give. The fetcher now:
- recognises Amazon's "empty list" message and logs it as **Amazon-side**, not "layout change"
- **calculates movers itself** from the stored daily Best Sellers ranks: 1-day rank gain % = (yesterday − today) ÷ today × 100. That's Amazon's own formula and the backtest's approximation. A product new to the top 100 is treated as coming from #101. Only categories fetched on **both** days are compared, so a category that failed yesterday can't create fake movers. `mover_source` in TREND_SIGNALS shows `amazon`, `own_1d` or `none`.
- Own movers start from the **second** daily run.

## v1.5: IndiaMART sourcing estimates (`sourcing.yml`, runs after each daily fetch)
For calculator rows **still waiting for your quote** (top 20 by score, seasonal and quarantined products last, each re-checked at most weekly), it searches IndiaMART and fills **indicative** columns:
`Indicative cost/pc (IndiaMART median, NOT a quote)` · `range P25–P75` · `comparable listings / suppliers` · `confidence` · `IndiaMART search` link · **`Est. margin % at indicative cost`** · **`Triage`** ("Worth requesting a quote" / "Likely not viable at listed prices").

**It never writes your quote cell, and the real Decision still says NEED SOURCING QUOTE until you enter one.**

Safeguards, each there because the simple version gives confident wrong numbers:
- **Brand is removed** from the search (wholesale sells generics). If the auto-search is wrong, type better words in **`IndiaMART search words`** and the next run uses them.
- **Only per-piece prices are compared:** dozen ÷ 12, "pack/set of N" ÷ N; per kg, litre, metre and boxes without a count are excluded.
- A listing must **share ≥ 50% of the search words**. Prices **above the Amazon retail price** are dropped (almost always a different product).
- **≥ 3 comparable listings** are needed, otherwise confidence is "none" and no cost is shown.
- The parser stops at each listing's own card, so a "price on request" listing can't borrow a neighbour's price (a bug found in testing).

Telegram sends a short list of the best Amazon-price-to-IndiaMART-cost ratios, with search links. **What to do with it:** open the search, check that the product and unit really match, then *request quotes* from 3+ suppliers. Listed prices are often stale, GST-unclear or set to attract enquiries.

Limits: IndiaMART has no public product API and its terms restrict scraping, so this runs small (20 searches/day) and slow. The parser was written from IndiaMART's typical page structure **without a live page**. The first run saves unrecognised pages in the `bazaar-radar-sourcing` artifact (`debug/indiamart_*`), so send them over if estimates are empty.

## v1.6: fixes from the second live run (diagnosed from the saved pages)
- **Hot New Releases empty (Grocery, Automotive):** Amazon's own message, "there are no *hot* new releases available". Now recognised as Amazon-side (one general pattern covers every list type).
- **Seller counts:** "New (10) from" sits in the **right column**, and the parser only searched the centre column. Now it searches the whole page, and a lone "Sold by" with no other offers counts as **1 seller**. Pages Amazon serves **without a buy box** (no price, no "Sold by", title starting "Buy …") are labelled as such (`seller_source`) instead of "unknown".
- **Google rising searches crashed** with `Retry.__init__() … 'method_whitelist'`: pytrends passes a parameter that newer urllib3 removed, but only when retries or backoff are set. They're no longer set.
- **Product pages hit captchas** (5 of 30 on a second same-day run): an extra 4–7 s pause between product pages. Avoid several manual runs on the same day.
- Regression tests now use trimmed copies of the real pages in `tests_fixtures/`.

## v1.7: weekly affiliate picks = the model's forward test (`affiliate.yml`, Sundays)
**Why:** the trend score has never been validated (the backtest was never run). Every published pick is a dated prediction, so tracking picks against **random products from the same category on the same day** validates the model for free while the channel earns.

**What it does each Sunday** (only after ≥ 7 days of history):
1. Picks the top 8 actionable products, at most 2 per category. Discount-driven and quarantined products are excluded; seasonal products are allowed, since there's no stock risk for affiliates, but labelled.
2. Records **3 random same-category baselines per pick** (`PICKS` tab, deterministic per day so they can't be re-drawn).
3. Posts to your channel (`AFFILIATE_CHAT_ID`), or sends you a **draft** if it isn't set. Links carry your `AFFILIATE_TAG`. **No prices** (Associates rules restrict showing prices not from Amazon's API), and the disclosure line is always included.

**Daily:** the fetcher fills each pick's and baseline's rank at +7/14/28 days. `PICK_SCORECARD` shows pick hit rate vs random.
- **Hit (fixed now, before any data):** still in the category top 100 **and** rank ≤ rank on the pick date.
- Days the category wasn't fetched count as **pending**, not misses.
- The verdict uses the **14-day** horizon only and refuses to judge before 40 picks are resolved (about 5 weeks).

**Secrets to add:** `AFFILIATE_TAG` (your Associates tracking ID) and, optionally, `AFFILIATE_CHAT_ID` (a public channel the bot is an admin of).
**Read the Amazon Associates Operating Agreement before posting.** Data obtained by scraping, price display and link placement all carry account risk. Posts are deliberately conservative, but compliance is your responsibility.

## v1.9: tools for micro-flipping and the seller brief
**SUPPLIER_QUOTES tab** (created once, never overwritten): log 3+ quotes per product with up to 3 quantity tiers, GST %, sample received and quality (1–5), certificate, lead time. It picks the price at your **test quantity** (cell B3, default 50), warns when MOQ is above it, and marks the **★ best** quote, **never one whose sample you rated below 3**. The ★ price flows into **CALCULATOR → Sourcing cost** automatically; typing a number there still overrides it. Products with a logged quote are skipped by the IndiaMART sourcing run.

**FLIP_TRACKER tab** (created once): one row per test batch. Enter what actually happened (units bought, delivered, RTO, returns, damaged, fees, shipping incl. RTO, ads, rating). It calculates sell-through, revenue, GST payable (output − input credit), **real net profit and margin**, RTO % and return %, then gives a verdict using rules **you fix at the top of the tab before buying**:
- before day 30 → RUNNING
- ≥ 60% sold + ≥ 10% real margin + rating ≥ 4 → **SCALE — reorder 2×**
- < 30% sold, or a loss → **KILL — liquidate at cost** (a fast seller at a loss is a kill)
- otherwise → **ITERATE — change one thing and rerun**

**Weekly seller brief** (`BR_MODE=brief`, runs after the affiliate picks): a one-page HTML file sent to the **admin** chat as a document, with track record first, up to 5 rising products and their reasons, category heat, seasonal watch, method and limits, and a closing **"would you pay, and how much?"** question. Forward it yourself to 20–30 sellers: their answers are the Data-as-a-Service demand test. It has no prices and no bulk data dumps.

Header renames are handled: quotes typed under an old column name are kept (a bug caught while upgrading the workbook).

## v1.10: product-page circuit breaker
Amazon's captcha rate on product pages rose with each same-day run (0 → 5 → 18 of 30). After **3 captcha'd product pages in a row**, the fetcher stops requesting product pages for that run (`DETAIL_BLOCK_STREAK`), and the admin digest says so. Continuing would deepen the block and risk the list pages, which are the core data. **Run once a day; extra manual runs add no history** (history counts dates, not runs).

## v1.18: week-1 audit fixes + opportunity estimator
**Why:** in week 1, picks were posted on Diwali day, a likely firecracker was promoted, a product that fell in rank was picked, and IndiaMART found nothing for 30 of 30 products.

- **Festival calendar (`season.py`).** A seasonal product is promoted only 30 to 3 days before its event. Our data showed Diwali items peaking about 4 days before Diwali. Stock is suggested only 45 to 120 days ahead. Lunar festival dates are set for 2026–27; **add next year's dates each October**. A festival with no future date is blocked, never silently allowed. The digest and signals show a `season_window` note.
- **Firecracker flag.** Products are flagged "EXPLOSIVES — DO NOT promote or stock" and are never picked.
- **Picks must have really climbed:** at least 10 places on the bestseller list. The admin chat lists high scorers that were dropped, with the reason.
- **IndiaMART self-check.** If the full query finds fewer than 3 matches, it retries with 2 words. The message then says whether IndiaMART returned **no listings** (blocked or layout changed: send `debug/indiamart_*.html` from the artifact) or **listings that didn't match** (type search words in the calculator).
- **Product pages:** 6 a day by default (was 30). 3 go to top signals and 3 to **sales anchors**: one category a day, products at ranks 1/5/15/35/70. Each anchor pairs a rank with Amazon's "N+ bought in past month", which is what turns ranks into units.
- **Regions:** the weekly state run also searches 2-word generic phrases for the top 8 rising products (for example "string light"), so you see *where* a product is searched. Set `BR_STATE_PRODUCT_KW`, default 8.
- **Opportunity estimator (`estimate.py`, workflow "Opportunity estimate").** For any ASIN, it gives a 3-month ₹ range (P10/P50/P90) for affiliate and for selling, plus wave length, season window, top states, a confidence grade A–D, and what's missing.
  - Fill the new tabs to make it more accurate: **COMMISSION** (your rate per category), **AFF_ACTUALS** (weekly clicks, orders and earnings from Associates Central), **SALES_ANCHORS** (optional: paste "N+ bought in past month" from product pages you open).
  - Run it on a local workbook: `python estimate.py --xlsx file.xlsx --asin B0… --members 800`.

## v1.17: separate public groups by category
Set the variable `AFFILIATE_GROUPS` to send each public Telegram group picks from its own categories only:
```
Fashion & Beauty|-100111|apparel,shoes,beauty,jewelry,watches,luggage; Home & Living|-100222|kitchen,home-improvement,garden,pet-supplies,sports,toys,baby; Tech & Daily|-100333|electronics,computers,automotive,grocery,hpc,books
```
Format: `Name|chat_id|category slugs`, with groups separated by `;`. Each category can be in only one group, and a category in no group is not posted publicly. The admin chat id is refused. You get up to `BR_AFF_TOP_N_GROUP` picks per group (default 4). One forward test covers all groups. When this is set, `AFFILIATE_CHAT_ID` is ignored. Check commission rates in Associates Central; they change.

## v1.16: missing-day warning
The admin digest lists any date in the last 14 days with no history ("🕳 Missing days"). Gaps weaken mover calculations (comparisons skip gaps over 2 days), so check GitHub Actions when you see one. GitHub may also pause scheduled workflows in inactive repos, so check its current rules.

## v1.15: data retention
| Where | What | How long |
|---|---|---|
| Google Sheet `HISTORY`, `DETAIL_HISTORY`, `YT_HISTORY`, **`GTRENDS_HISTORY`** (new) | Every day's ranks, prices, reviews, product details, YouTube checks, rising searches | Rolling 60 days (keeps the Sheet fast and under its cell limit) |
| Sheet `AMZ_*`, `TREND_SIGNALS`, `YT_CHECK`, `GTRENDS` | **Today only** (daily snapshot; their history lives in the tabs above) | 1 day |
| **GitHub branch `data-archive`** (new) | `archive/YYYY-MM-DD/history.csv.gz`, `signals.csv.gz`, `gtrends.csv.gz` | **Forever** (~40 KB/day, ~15 MB/year) |

The archive branch is created automatically on the first run and never touches your code branch. To read it: on GitHub, switch branch to `data-archive` → `archive/` → pick a date. Or load it in Python with `pd.read_csv("…/history.csv.gz")`.

## v1.14: from the day-2 live data
- **Variant-swap guard:** Amazon sometimes swaps which size or flavour of a product it lists (Purepet cat food 6 kg → 1 kg, both at #1). Our own-mover calculation saw a "new" product jumping from #101 to #1 (+10,000%). A new entrant now inherits the best rank its **product family** (same first title words, same category) had yesterday. 12 swaps were caught in your day-2 data. Shown in `mover_note`.
- **Title fill:** ranks 31–50 and 81–100 come only from Amazon's embedded list, without titles (40% of rows). Titles are now filled from any day or list where the product appeared with one (960 → 718 untitled after 2 days, improving daily). Untitled products are kept out of the calculator and picks, but appear in the ranked digest as a link.
- Compliance: insecticides (**Insecticides Act / CIB&RC**) and pet food are now flagged.
- Rising searches: "how to / what is / why / kaise / recipe" queries are dropped.

## v1.13
- **Festive decor** seasonal group: string, fairy, curtain, pixel, rope and waterfall lights, toran, bandhanwar. These peak for Diwali, Christmas and weddings even when the title names no festival (Desidiya curtain lights in late October).
- Rising searches about films, songs or meanings are dropped ("lunch box **movie**").

## v1.12: fixes from the first live digest
- **Learning mode:** until 7 days of history exist, the admin digest says *"Learning: day N of 7"* and lists a plain sample, with **no ranking and no category heat** (on day 1 everything scores the same 31).
- Untitled products (embedded-list items without a card) are no longer shown as blank lines.
- "Tap **extension**" is no longer flagged as electronics; extension board/cord/socket/box still are.
- YouTube "negative buzz" ignores words in the product's own name ("**Fake** Lizards").
- Rising searches show at most 2 per seed keyword.

## v1.11: X and WhatsApp
**Share kit (default, free, no ban risk):** every Sunday the **admin** chat gets each pick with two one-tap buttons, **𝕏 Post on X** and **🟢 Share on WhatsApp**. Each opens the app with the post pre-written; you choose the community, channel or account and send. About 2 minutes a week. The same texts are saved in the `SHARE_KIT` tab.

**Why WhatsApp isn't automated:** there's no official API for posting to WhatsApp Channels or Communities. Unofficial WhatsApp Web bots break WhatsApp's terms, and the usual result is a **banned number**, which takes your community with it.

**Optional X auto-posting (paid):** add `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` (an X developer project with billing enabled). X's free tier closed to new projects in Feb 2026. Pay-per-use was reported at roughly $0.015 per post and **~$0.20 per post containing a link**; check docs.x.com for current pricing. `X_MAX_POSTS_PER_RUN` (default 8) caps posts per week, and posting stops at the first billing or permission error (HTTP 402/403).

X posts are kept within 280 characters (links count as 23; emoji count as 2), contain no prices, and always end with `#ad`. **Register every account you post links from (X handle, WhatsApp channel) in your Amazon Associates account settings**, since the program requires your traffic sources to be listed.

**Brand:** use your own name, logo and wording. Copying another channel's branding or posts is impersonation and gets accounts reported.

## Two Telegram chats
| Secret | Who | Receives |
|---|---|---|
| `TELEGRAM_CHAT_ID` | **Admin** (private group) | Daily digest, scores, quarantine, compliance and seasonal flags, calculator and IndiaMART estimates, state trends, forward-test scorecard, drafts, all errors and failure alerts |
| `AFFILIATE_CHAT_ID` | **Public channel** | Weekly only: a header with the disclosure + one message per product (title, "Climbed #X → #Y", tagged link, #ad). Nothing else, ever |

If both secrets hold the **same** ID, the weekly job refuses to post publicly and warns the admin chat. Leave `AFFILIATE_CHAT_ID` unset to receive public posts as drafts in the admin chat first.

## Troubleshooting
| Telegram / log says | Meaning | Fix |
|---|---|---|
| `Sheet not updated: … refused access (403)` | The sheet isn't shared with the service account, or `GOOGLE_SHEET_ID` is a different sheet | Share the sheet with the `client_email` shown in the message as **Editor**; copy the ID between `/d/` and `/edit` |
| `not found (404)` | Wrong `GOOGLE_SHEET_ID` | Copy it again from the sheet URL |
| `Amazon shows no items for this list today (Amazon-side)` | Amazon's own page says the list is empty | Nothing to fix; movers are calculated from our history instead |
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
