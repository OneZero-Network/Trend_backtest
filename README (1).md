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

## v1.80: done-for-you sourcing & margin audits, published Easy Ship fees, a savings & cards funnel (22 Nov)

**Why.** Keepa already shows Amazon.in sales-rank history, so rank history alone is not our edge. What Keepa cannot
give an Indian seller is *verified supplier quotes turned into a landed Easy Ship margin*. That is now a paid product:
the **Sourcing & Margin Audit** (₹10,000 per product, ₹20,000 per category up to 5 products, 50% advance). And the
channel gets a second earning lane that pays more per conversion than Amazon's ~₹15: EarnKaro deals and bank card
referrals (EarnKaro lists ₹350–₹2,975 per activated card), capped and judged per post.

### What is new
| File | What it does |
|---|---|
| `fees.py` | Amazon's published weight-handling table (Standard STEP, from 16 Mar 2026, ex-GST): Easy Ship ₹55 / 75 / 112 / +₹34 per kg to 5 kg / +₹18 per kg after; fulfilment-centre regional and national too. Chargeable weight = higher of actual and L×B×H÷5000. Replaces the v1.70 rough estimate. |
| `quotes.py` | RFQ text (every question a landed cost needs, brand-free) · `/quote` parsing · landed cost per unit (with or without GST registration) · every supplier at the target price: fees, weight fee on chargeable weight, returns, profit, margin, cash at MOQ, break-even units · verdict on **written** quotes only · the margin sheet as XLSX with live formulas (tested: LibreOffice recalculates it to the code's numbers). |
| `audit.py` | Audit jobs from the bot: `/audit new`, `email`, `fee` (Razorpay advance link), `paid`, `name`, `hours`, `build`, `deliver` (email with the sheet attached + balance link), `lost`. **Build refuses below 3 quotes / 2 written.** Report: verdict, our demand evidence (or "not evidenced"), every quote landed, price ±10%, compliance, what it does not cover. Month view: sold, ₹ collected, hours, ₹ per hour, against 3 a month. |
| `savings.py` | `/deal <EarnKaro link> <line>` and `/card <link> <card> \| <line>` queue offers; the drip posts one at 13:00 / 19:00 (max 2 a day; cards max 2 a week, never two days running, evening only). #Ad disclosure first; cards carry "the bank decides … not financial advice"; approval promises and "best card" refused; only links on `SAVINGS_DOMAINS`. Arms compared **per post** against Amazon; an arm is paused 7 days automatically if members fall 3 nights after it. |
| Operator / bot | Live Floor shows audits (sold, ₹, h, ₹/h) and savings (queue, per-post verdicts, pauses). New bot replies `/audits` (open jobs, our IndiaMART suppliers, RFQ text) and `/queue`. An audit buyer counts as a payer in the seller test. Outreach now offers the audit after the free page (`VENDOR_OFFER=audit` · `radar` · `both`). |

### Run an audit (owner)
1. `/audit new FitCo | B0CKBND48X | 1249` (an ASIN or product words; add `| category` for ₹20,000) → `/audit email A1 ops@fitco.in` → `/audit fee A1` (₹5,000 advance link; Razorpay emails it).
2. When paid: `/audits` shows the suppliers our IndiaMART run found and the RFQ text. Send it to 4–6 suppliers (IndiaMART enquiry, WhatsApp, email). `/audit name A1 doorway pull up bar` if the generic name needs fixing.
3. Each answer: `/quote A1 Shree Traders price=520 moq=100 gst=18 freight=2500 kg=2.6 dims=100x12x8 lead=7 written=yes`.
4. `/audit build A1` → report + sheet come to you. Read them. `/audit deliver A1` → to the client with the balance link. Log time with `/audit hours A1 2`.

### Settings
Variables: `AUDIT_DELIVER_TO` (city for freight), `AUDIT_FEE_SKU` (10000), `AUDIT_FEE_CATEGORY` (20000), `AUDIT_ADVANCE_PCT`
(0.5), `VENDOR_OFFER` (audit), `SAVINGS_SLOTS` (13,19; empty = off), `SAVINGS_PER_DAY` (2), `CARDS_PER_WEEK` (2),
`SAVINGS_DOMAINS` (ekaro.in,earnkaro.com). New tabs the machine keeps: `AUDITS`, `QUOTES`, `SAVINGS_QUEUE`, `SAVINGS_STATE`.
EarnKaro earnings: `/money ek-deals <orders> <₹> [clicks]` and `/money ek-cards …` once a week (no API).

### Verified / not verified (22 Nov 2026)
Verified: the weight-handling table and volumetric rule (Zoutons, from Amazon's fee schedule), EarnKaro's card payouts
(₹350–₹2,975, paid on activation, confirmed within 75 days), ASCI's rule that unqualified creators may share generic
financial information but not technical advice, disclosure upfront, Keepa's Amazon.in rank history. Not verified:
your STEP level (Premium/Advanced pay less, Basic more), EarnKaro's link domain for every merchant (hence the setting),
whether a given bank's programme allows Telegram promotion (check each campaign's terms in EarnKaro).

## v1.70: three users, one Operator — run the business from the bot, no spreadsheet (22 Nov)

**Who it serves (corrected).** Shoppers on Telegram / WhatsApp / X are the *audience* (Amazon pays ~₹15 an order:
about ₹6 per 1,000 views). Amazon sellers who already sell are the *payers* (₹999 pilot, then ₹1,999 a month; 25 of
them = ₹50,000). Drop shipping is a *capped proof lane*: at most 3 pilots, judged by fixed kill rules, whose real
numbers become the strongest pitch to sellers. Release notes and the end-to-end workflow: the "OneZero Machine v1.70 —
Release Notes" doc.

### What is new
| File | What it does |
|---|---|
| `fees.py` | Amazon.in fee model as of 22 Nov 2026: 0% referral under ₹1,000 (since 16 Mar 2026), the **highest** published closing fee per band (after the 7 Sep rise), a rough Easy Ship estimate (published examples: 350 g → ₹55, 3.5 kg → ₹180), the 10% + GST seller-cancellation fee (since 17 Aug), weight read from titles (load ratings skipped). Every number says whether it is *published* or *ASSUMED*; override with `AMZ_FEES`. GST on fees is not a cost (input tax credit). |
| `vendor.py` | Weekly category Radar (what lasted, started slipping, new and holding, openings with "a supplier price under ₹X keeps 25%") as email HTML, WhatsApp text and a Telegram preview · small-brand leads (slipping and holding) with a message drafted from their own numbers · `VENDOR_CRM` · Razorpay Payment Links (₹999 after a call, ₹1,999 renewal 5 days before the end) · SMTP delivery · the seller-test verdict (hook-problem tier at 15 contacts / < 4 calls; dropping needs 30 and 8). |
| `dropship.py` | Shortlist: durable · sourceable · no licence flag · ₹299–999 · ≤ 2 kg · stock-out risk priced in. Readiness checklist (seller of record, GST place of business for the pickup address, Legal Metrology labels, sample, fee preview). Pilots from bot commands, kill rules fixed in code: > 2.5% supplier cancellations, < 10% margin, < 5 orders in 14 days → stop; 20+ orders, ≥ 25% margin → keep. |
| `operator_agent.py` | The Operator: applies the owner's bot commands and uploaded reports (idempotent), compares durable vs momentum **per post** (the Bot API gives no view counts), works out the ₹50,000 goal from measured money, and writes ONE nightly **Live Floor** report with today's to-dos. |
| `cloudflare_worker/worker.js` | **Owner desk** in the bot (your chat id only): `/floor`, `/leads`, `/lead L1a2b call`, `/pilot add B0… 210`, `/order P1 2 499`, `/cancel`, `/return`, `/money earnkaro 3 140`, send the Associates report as a CSV file, `/refresh` (runs the Operator now). Queued in KV; `/events` and `/push` for the Operator. Strangers get the shopper bot as before. |
| `.github/workflows/operator.yml` | Nightly 22:00 IST (+ on demand). Replaces `growth.yml`'s schedule (it runs the same counts first). |
| `.github/workflows/vendor.yml` | Mondays 08:30 IST: Radar to paying sellers, new leads, drop-ship shortlist. |

Also: the growth goal maths now assumes ₹15 an order (was ₹40); the bot's `/pro` says nothing is sold inside Telegram
(Telegram requires Stars for digital services sold in a bot — the Radar is sold by payment link and delivered by email).

### The owner's whole job
1. Morning: open the Live Floor in Telegram (or `/floor`). Do the numbered to-dos.
2. Send the outreach messages `/leads` gives you (copy, paste, send — a person sends, never the bot). Tell the bot
   what happened: `/lead L1a2b contacted`, `replied`, `call`, `no`, `stop`, `email name@shop.in`.
3. After a call: the Operator makes the ₹999 link that night (Razorpay emails it when you saved an email). Paid → the
   seller gets the Radar every Monday; renewal link 5 days before the end.
4. Once a week: download the tracking-ID report in Associates Central and send it to the bot as a file.
5. Drop shipping: `/pilot ready` (checklist) → `/pilot add <ASIN> <cost>` → log each sale with `/order`.

### Switch it on
- Worker: add `OWNER_CHAT_ID` (your Telegram user id) to `wrangler.toml`, make sure the `RADAR_KV` namespace is bound,
  `npx wrangler deploy`. The Worker's GH_TOKEN already has "Actions: write" (needed for `/refresh`).
- Repo **variables**: `WORKER_URL` (https://&lt;worker&gt;.workers.dev), `VENDOR_SENDER` ("Ayaz, OneZero"),
  `VENDOR_REPLY_TO`, and add `telegram.durable=<ID>` to `AFF_TAGS` so the two arms can be compared.
- Repo **secrets** (optional, each switches one thing on): `RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET` (payment links),
  `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `SMTP_FROM` (Radar by email; without them it comes to you to forward).
- New tabs the machine keeps for itself (you never edit them): `VENDOR_CRM`, `PILOTS`, `PILOT_ORDERS`, `MONEY`,
  `OPERATOR_DONE`, `FLOOR_LOG`, `DS_SHORTLIST`. The old manual `SELLER_LEADS` tab is no longer read.

### What I verified, and what I could not
- Verified (22 Nov 2026): referral 0% under ₹1,000 (Amazon fee guide), closing-fee groups and the 7 Sep rise
  (Zoutons, from Amazon's fees page), the 17 Aug cancellation fee, Amazon's drop-shipping policy, Telegram's Stars rule,
  Razorpay's Payment Links API shape, the Creators API's 10-sales rule.
- Not verified: closing fees for ₹501–1,000 and referral rates above ₹1,000 (both ASSUMED and labelled), GST rates
  per product (ASSUMED 18%; set `AMZ_FEES={"gst": …}`), and the exact column names of the Associates India CSV export
  (matched loosely; send one real file and the parser can be pinned).
- Tests: `test_v170.py` (fees, vendor, drop-ship, Operator, end-to-end operator + vendor runs with the bot queue,
  Razorpay and SMTP faked) and the Worker suite. A run on the real 17-day history produced the leads, Radar and
  shortlist shown in the release notes.

## v1.60: the growth machine — measured channels, a share loop, an X agent and a shopper bot
The strategy is in the doc **"OneZero Growth Machine — Strategic Plan"**. This release builds the parts the plan needs. The plan's core finding: at assumed rates, ₹50,000 a month from affiliate links alone needs about 52,000 engaged members. Members are the bottleneck, so the machine measures where they come from before it tries to scale anything.

**Nothing here spends money or posts anywhere new until you switch it on.** X is `off`, the copy agent is off, and the shopper bot runs only once you deploy the Worker update.

### What is new
| Part | File | What it does | Default |
|---|---|---|---|
| Tracking IDs per channel | `tags.py` | Each link carries its channel's Associates tracking ID (`telegram`, `whatsapp`, `x`, `bot`, optionally `telegram.durable`), so Amazon's report says **where** a click came from | falls back to `AFFILIATE_TAG` |
| Share loop | `content.py` | Every Telegram post ends with a forward line and a named join link (`tg-forward`). The daily WhatsApp text carries your channel link and its own Telegram link (`wa-channel`) | on (`AFF_SHARE_FOOTER=0` turns it off) |
| Weekly formats | `content.py` | Sunday "lasting bestsellers" roundup (one per category, only "N days on the list" claims); Wednesday poll from the day's picks | on: Sun 11:00, Wed 19:00 |
| X agent | `xpost.py` | OAuth 1.0a poster (signature verified against Twitter's published example). **Link-free** posts from rotating templates. Never replies, likes, follows or uses hashtags. Caps of 4 a day and $10 a month are checked from `X_POST_LOG` before every call; duplicate text is refused | `AFF_X_MODE=off` |
| Copy agent | `content.py` | Optional Claude (Haiku) rewrite of X hooks for variety. Every draft is fact-checked: a number not in our data, a link, a hashtag or a hype word sends it back to the template | off |
| Growth ledger + review | `growth.py`, `growth.yml` | Daily: channel members, a named invite link per source (created once), joins per link, bot use, X spend → `GROWTH`. Mondays: every kill line from the plan with its value and status, plus the goal worked backwards with **measured** rates once an Associates report is pasted → admin chat + `GROWTH_REPORT` | daily 22:00 IST |
| Shopper bot (the product) | `cloudflare_worker/worker.js`, `radar_index.py` | Send @OneZeroDeals_bot any Amazon.in link or ASIN: it answers with what we observed (days on the list out of 14, rank now and a week ago, climbing / steady / slipping), a tagged link and the join link. `/top <category>`, `/pro` (seller waitlist → admin chat). Counts channel joins per invite link and bot use in KV and serves `/stats` | off until deployed |

New sheet tabs (in the affiliate sheet if you use one): `GROWTH`, `GROWTH_LINKS`, `GROWTH_STATE`, `GROWTH_REPORT`, `X_POST_LOG`. Two are yours to fill: `ASSOC_REPORT` (paste the weekly tracking-ID report) and `GROWTH_INPUT` (`key`,`value` rows: `paying_sellers`, `waitlist_opened`). `AFF_POST_LOG` gains `channel` and `tag` columns.

### Switch it on, in this order
1. **Associates (10 min).** Create tracking IDs for Telegram, WhatsApp, X and the bot under Account → Manage Your Tracking IDs. List every place you post in your Associates profile (Telegram channel URL, WhatsApp Channel URL, X profile, the bot's t.me link). Until a place is listed, don't post tagged links there.
2. **Repo variables:** `AFF_TAGS` = `telegram=…-21, whatsapp=…-21, x=…-21, bot=…-21` and `WA_CHANNEL_URL`.
3. **Telegram:** the posting bot must be a channel admin with **"Invite users via link"**. Run the *Growth snapshot* workflow once by hand. The admin chat lists the new invite links. Put `x-bio` in your X profile, `wa-channel` in the WhatsApp Channel description, `site` on your website, and keep `swap-1`/`swap-2` for cross-promotion swaps.
4. **X (optional):** create an app with Read and Write, buy credits, set a monthly spend limit **in the X console**, and add the secrets `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`. Set `AFF_X_MODE=dry` for a week (the admin chat shows what it would post), then `linkfree`. Only use `links` after X is listed in Associates; it costs about $0.20 a post.
5. **Shopper bot (optional, Phase 2):**
   - Create the KV namespace (`npx wrangler kv namespace create RADAR_KV`) and uncomment it in `wrangler.toml`.
   - Add the Worker secrets `BOT_TOKEN` (the public bot's token), `TG_WEBHOOK_SECRET` (any random string) and `RUN_KEY`.
   - Give `GH_TOKEN` **Contents: Read** as well.
   - Set `AFF_TAG_BOT` and `CHANNEL_JOIN_URL` (the `bot` link) in `wrangler.toml`, then deploy.
   - Open `https://<worker>.workers.dev/setup?key=<RUN_KEY>` once.
   - Set the repo variable `WORKER_STATS_URL` and the secret `WORKER_KEY` (= `RUN_KEY`). Once the webhook is set, getUpdates stops working, so the Worker counts joins instead.
6. **Every Monday:** paste last week's tracking-ID report into `ASSOC_REPORT`; read the review.

### What I verified, and what I could not
- **Verified offline:**
  - The OAuth signer reproduces Twitter's documented example signature exactly.
  - 70+ new checks and 24 Worker checks (in Node, with Telegram, GitHub and KV mocked).
  - A full daily, drip and growth run on your real 17 days of history. The bot index for 1,284 products is 172 KB.
  - Real-data bot answers. These exposed two bugs, which are now fixed: "#?" on a falling product with no week-ago rank, and an empty name on a product with no title.
- **Not verified:**
  - A live X post (no account here).
  - Live Telegram join attribution on your channel.
  - The exact column names of India's Associates report. The importer matches them loosely; send one real export and I will pin it.
  - The Worker on Cloudflare itself. KV counts are eventually consistent, so treat them as approximate.
  - X's prices: they come from third-party pages citing X's April 2026 rate card, so check your Developer Console.
- **"Going viral" cannot be built.** What is built is the loop and the meter: every post carries a join link that tells you whether forwards bring people in. The kill lines decide what continues.
- **Still manual:** WhatsApp. I found no official way to post to Channels, and the unofficial tools risk a number ban.

## v1.52: friendlier channel posts (17 Nov)
Feedback on the live v1.51 posts: the intro sentence was noise, the title wasn't right, and "on the list 14 of the last 14 days" was too long.
- **Title** is now **Amazon Hidden Bestsellers** (was "Hidden Amazon Finds"). It is one setting: repo variable `AFF_POST_TITLE` (drip workflow) — change it there, no code. *Caveat:* "hidden" is a hook, not a fact — a #2 bestseller is the opposite of hidden. It is harmless, but it is the one word in the post that cannot be checked against HISTORY. Don't put "Amazon" in the channel or bot *name* (Associates rules restrict that); a post title is a different thing.
- **Removed** "On Amazon.in's bestseller lists (tracked daily): new, climbing or holding. Tap a name for today's price." (Telegram) and its WhatsApp twin, and the 🧱/📈 legend. The names are still the links.
- **Durable line** is now `🏆 Sports, Fitness & Outdoors · #2 · 14 days on the list` — or `13 of 14 days on the list` when it missed a day. WhatsApp/X: `🏆 #2 in Sports, Fitness & Outdoors · 14 days on the list`. Same fact as before, ~45% shorter. Momentum lines are unchanged (`📈 Beauty · #100 → #83`, `📈 Beauty · new at #27`). Admin digest, calculator and brief keep the 🧱 marker; only the public posts use 🏆.

## v1.51: what lasts, and what is dying — durability, exit flag, two-arm forward test
Built from the reverse-logic review of the same 17 days (22,793 HISTORY rows, ~800 products on the lists each day). The question was: *the score ranks what is rising — does rising predict anything?*

**What the data said (population level, one festival, item-days overlap — descriptive, not proof).**
- **Momentum predicted ~nothing.** A product that climbed this week was not more likely to be on the list a week later than one that did not.
- **Persistence predicted survival.** Products already on the list most days stayed.
- **A fresh fall predicted leaving.** After a fall of 10+ places in 3 days, **39%** of products were off the top 100 a week later (n=583 item-days) vs **17%** of steady ones (n=2,825). By rank band: #1-30 **50% vs 11%** (n=40 vs 1,100) · #31-60 **29% vs 15%** · #61-100 **42% vs 27%**. The same direction in all three bands.
- So the daily score put the *least* predictive signal first, and every public pick was chosen by it.
- **Correction to my own analysis message:** it quoted 372 of 797 durable (47%). That used a looser rule. The rule now in code (`durable.py`) is stricter — on the lists ≥ 80% of the days observed in the last 14, median rank ≤ 70, ≥ 7 observed days, not a non-product — and gives **312 of 797 bestseller-list products, of which 179 are sourceable, not falling and not seasonal.**

**What changed.**
1. **`durable.py` (new, pure functions).** `durability()` → per product `durable_days`, `durable_window`, `median_rank`, `durable_score`, `durable`, and a plain-words note ("on the list 14 of the last 14 days · median #10"). A day when a category's list was never fetched is not counted as a miss. `exit_flags()` → `falling` when a product on the list is ≥ 10 places worse than 3 observed days ago. `fader_rates()` → the base rates above, recomputed from your own history every run and shown only where a cell has ≥ 30 item-days.
2. **New TREND_SIGNALS columns:** `durable`, `durable_score`, `durable_days`, `durable_window`, `median_rank`, `durable_note`, `exit_flag`, `exit_note`, `non_product`. The `signal` text gets "🧱 Durable" (replaces a plain "• Watch") or " · 🧱 durable" appended to any other label, and " · 📉 falling" when flagged. The calculator uses that text, so **its sheet layout is unchanged**.
3. **Two posting arms (`BR_DURABLE_SHARE`, default 0.67).** Of each day's 12 public picks, 8 come from the **durable** arm and 4 from the **momentum** arm (the v1.50 logic), interleaved 2:1 through the day. If one arm runs short the other fills the slots. `0` = momentum only (the v1.50 behaviour), `1` = durable only. Every pick carries an `arm` (PICKS, AFF_POOL, AFF_POST_LOG, PICK_SCORECARD), so earnings logged in AFF_ACTUALS can be split by arm.
4. **Falling products are never posted, and never added to the calculator.** A post that says "climbing" or "lasting" about something that just lost 10+ places is wrong on the day it is read. Admin sees them in "🚫 Not picked — falling — fell #13 → #60 in 3 days".
5. **Provable post copy.** A durable pick says *"14 days on the list"* (v1.52 wording; was "on the list 14 of the last 14 days · now #1") — a fact you can verify in HISTORY (the test compares the number in the post with HISTORY). No forecast, no "hot", no price. (The 🧱/📈 legend line from v1.51 was removed in v1.52.) Momentum picks keep the "#38 → #4" line.
6. **Forward test per arm.** Baselines are rank-matched as in v1.50 and are tagged with their pick's arm. The **pre-registered verdict is unchanged and is about the momentum arm only** (hit at 14 days, ≥ 10 pick days; rows from before v1.51 count as momentum). The durable arm makes a different claim, so it is judged on **"still in the top 100"** against rank-matched products *of any durability* — the question is whether durability adds anything beyond simply having a similar rank today. Digest: "🧱 Durable picks (7d): still in top-100 … vs matched baseline … (n=…, inconclusive / picks ahead / picks behind)".
7. **Calculator and sourcing priority.** Up to `BR_CALC_DURABLE_N` (default 10) of the 25 daily calculator slots go to durable, sourceable, non-seasonal, non-falling products first. Among rows waiting for a quote: 🧱 first, 📉 last. IndiaMART searches follow the same order, so the 20 searches a day go to products with proven presence before one-week climbers.
8. **Digest.** "🧱 Durable demand: 312 of 797 bestseller-list products … 179 sourceable, not falling, not seasonal" · "📉 Exit watch" (durable products that started falling, with the observed base rate).
9. **Brief for sellers.** New sections **Lasting demand** (sourceable, not seasonal, not falling, with the "crowded / rank is not sales" warning) and **Exit watch**, ahead of "Rising this week", which now says climbs are the noisiest signal and leaves out brand / cosmetic / food / hazmat items and falling items.
10. **Non-products are gone from every list.** "Credit Card Bill" sat at **#1 in Bags & Luggage for all 17 days** (it would have been "durable" forever), plus "Amazon Shopping Voucher", "Amazon Pay Digital Gift Card" and "LPG cylinder booking". `compliance.non_product()` matches specific phrases only — digital scales, card holders, rechargeable items, a "Gas Recharge Can" and WHOOP memberships are kept (checked against all 1,362 real titles). A NaN title read back from CSV no longer crashes the compliance step.

**What I did NOT do.** I did not re-weight `trend_score`. Tuning weights on 17 days and one festival is how you fit last week's noise, and the pre-registered test is about this score. The new logic sits beside it as a separate arm, so the old test keeps running unchanged and the new claim gets its own.

**Counterpoints you should hold on to.**
- **Durable ≠ profitable.** On the lists every day also means every other seller sells it. Demand is proven; **margin is not**. The calculator and your IndiaMART quote still decide.
- **Brand pull.** Lifelong, Boldfit and similar names own many of the long-running commodity items (dumbbells, pull-up bars). A generic version may not rank however long the item lasts.
- **Thin evidence.** 17 days, one festival, items counted on consecutive days. The base rates are descriptive. They will move.
- **Rank is not sales.** Nothing here measures units.
- **No flag is not clearance.** A rechargeable mosquito bat shows no compliance flag but still needs BIS/CE checks. "Not flagged ≠ cleared" is in every compliance cell.

**What would prove this wrong (decided now, so it cannot be argued later).** Judge the durable arm at 14 days once **≥ 10 pick days** and **≥ 40 resolved durable picks** exist: if its "still in the top 100" rate is not above the rank-matched baseline's interval, durability adds nothing beyond rank — set `BR_DURABLE_SHARE=0`. Separately, if per-post earnings in AFF_ACTUALS (split by `arm`) are no better for durable picks after the same period, the arm is not paying either. If the 📉 flag cries wolf (items return within a week), raise `FALL` in `durable.py`. If the calculator fills with crowded commodity items nobody can win on price, set `BR_CALC_DURABLE_N=0`.

**Switches.** `BR_DURABLE_SHARE` (drip workflow, default 0.67) · `BR_CALC_DURABLE_N` (daily workflow, default 10) · thresholds at the top of `durable.py` (`WINDOW` 14, `PRESENCE` 0.8, `MAX_MEDIAN_RANK` 70, `LOOKBACK` 3, `FALL` 10, `MIN_BASE_N` 30).

**After you replace the files:** nothing to configure. The first daily run adds the new columns (the sheet tabs are rewritten whole, so older rows simply have blanks). The first digest shows the "🧱 Durable demand" and "📉 Exit watch" lines; the first drip pool mixes the two arms and the admin chat says which post is which.

## v1.50: weekly review fixes — what the first 17 days of data showed
Built from the week-3 review (HISTORY, PICKS, SOURCING, DISC_SEEDS and the six run logs). Every number below was measured on your own sheet.

**Two corrections to the review itself.** (1) The digest header said "v1.45" because the string was hard-coded in the code; it was never evidence of which version ran. There is now ONE `VERSION` constant used by the digest, the log banner and the Tool Watch digest. (2) The 7-day forward-test results that exist so far are the picks of **8 and 9 Nov — Diwali day and the day after**, the worst possible days to judge a trend score.

**1. "No price" is no longer "out of stock" (`capture.py`).** Amazon lazy-loads the second half of each 50-item page. In HISTORY, ranks 31-50 and 81-100 have **0% price, rating and reviews** (4,578 of the 4,756 unpriced bestseller rows are these placeholders; only 178 are real sold-out cards). v1.49 read all of them as "probably out of stock", so ~19% of every day's scored items were dropped — NIVEA Deep Impact was the #1 signal on 16 Nov *and* in "Not picked: out of stock". Each item now has a `stock_state`:
- `in_stock` priced today · `unavailable` card shown, no price (really sold out) · `carried` placeholder today but priced within 5 days (price carried) · `unknown` never seen priced → **not posted, reported as "unverified"**, never as sold out.
- **Below-the-fold fetch (unverified).** Each run tests an extra `isAboveTheFold=0` request on the first bestseller page that needs it; if ≥ 50% of the hidden cards come back priced it keeps using it for the other pages, otherwise it stops after one attempt. The daily digest says which happened ("🔬 below-the-fold fetch working / tested → failed"). Control: `BR_LAZY_FETCH` = auto (default) | 0 | 1.
- The digest gets a "🧾 Stock check" line: priced today · carried · unverified · sold out.

**2. Reselling lane only gets things a reseller can source (`resell_gate.py`).** Affiliate posts are unchanged — a brand is what people click. For the reselling lane (calculator queue, IndiaMART searches, stock gate) a product is excluded when it is: a **big brand** (curated list of ~200 national/MNC names, matched at the start of the title only), **formulated** (beauty and grocery lists, cosmetics/food/supplements/pet food), **dangerous goods** (butane, aerosols, flammable liquids) or an explosive. Small private-label brands are *not* blocked — their product can be sourced generically. 36 of 60 SOURCING rows had relevance 0.0; they were all this kind. New signal columns `resell_ok` / `resell_note`; the digest shows "🏷 Reselling lane: N of M signals are sourceable". Controls: `BR_RESELL_GATE=0` (off), `BR_BRANDS_EXTRA`, `BR_RESELL_ALLOW_CATS`.

**3. Compliance flags were backwards.** A toy "…Sticky Wall Soft Bomb" and dog ear-muffs ("…Anti-Noise Thunder Fireworks") were flagged EXPLOSIVES while butane gas cans were NOT FLAGGED, and an oil *sprayer* was flagged as food oil. Explosives now count only when the **product itself** (title head, before `|` `,` `–` or " for ") is one; butane/aerosol/thinner get a DANGEROUS GOODS review flag; "oil spray/sprayer/dispenser…" is not FSSAI.

**4. Forward test: fair comparison, with error bars (`picks.py`).**
- New baselines are **rank-matched** (±15 ranks of the pick, same category and day; column `baseline_type` says `matched` or `random`). The old baselines started at median rank 54 vs the picks' 26.
- `PICK_SCORECARD` now holds every horizon × metric (**hit** and **alive**) × cohort (**all**, **rank-matched**, **non-festival-days**) with a 95% Wilson interval. The daily digest adds one line, e.g. "Like-for-like (7d): still in top-100 46% vs matched baseline 84% (n=11, inconclusive)".
- The **pre-registered verdict is unchanged** (hit at 14 days, ≥ 10 pick days). Switching the metric after seeing the results would be p-hacking, so `alive` and the matched cohort are shown next to it, not instead of it. On the data so far the like-for-like comparison is *worse* for the picks than the unmatched one — it is not rescued by the fairer baseline. Still n=11 from 2 festival days; the first non-festival cohort resolves from 17 Nov.
- A pick whose own rank history reads "holding" (or fell) no longer reaches the public pool (`drop_holders`) — week 3 posted "Holding #51" under a "Rising" header. The post header now says "new, climbing or holding".

**5. Discovery seeds can no longer drift (`discovery.py`).** `DISC_SEEDS` held 'namjooning stanford', 'mehul sir tuition ahmedabad', 'jobs hunt stripe engineer', 'burmese python concrete pipe nest', 'megaa click', 'badakit wiki'… they ran **first** in every run and burned the Trends budget (hence the 429s). Now: a rising search becomes a *candidate*; it is used only after it has risen on **2 separate days**; it must be about its parent seed, ≤ 3 words, with no model number ('hp8143', 'hd3151'); at most 2 growing seeds per lane run first (was 3); a seed expires after 5 days (was 7); the table is capped at 30 rows per lane. Rows from before v1.50 have no hit count, so they stay inactive until they rise again.

**6. Rising searches are capped at the Breakout level (5,000%).** "+2,347,100%" is a tiny number doubling. The daily list and discovery now show "+5,000%+ (breakout)"; the GTRENDS `value` is capped and the raw figure is kept in `note`.

**7. Less waste.** Amazon's Movers & Shakers pages have rendered empty for the whole period (0 movers rows in HISTORY; our own movers come from rank history). After 5 days of that the 8 daily Movers requests are skipped and re-checked once a week (`BR_FETCH_MOVERS` = auto | 0 | 1). The weekly states report builds its keywords from contiguous phrases found in ≥ 3 titles ("lunch box", "body lotion") instead of "toy boys" / "deep roll" / "anti skin", 6 product keywords instead of 8.

**Not changed — your decision: posting cadence.** 7 posts a day go to an audience whose size the logs cannot show. Posting hours come from `BR_DRIP_HOURS` (default 9,11,13,15,17,19,21) and the Cloudflare worker's schedule; change both together (e.g. `9,14,20`) if you want fewer.

**After you replace the files:** nothing to configure. Day 1 shows the "🧾 Stock check" line and whether the below-the-fold fetch worked; the sourcing run will say how many calculator rows it skipped as brand / formulated / hazmat.

## v1.49: Tool Watch, third live run — tickets were being cut off
**The file you attached showed it:** tickets TW-2646-01 and -02 end mid-word at **exactly 3,500 characters** ("…suggest: C", "…calculator-amazon-"). Cause: *resend* sent the stored text cut at 3,500 characters, with no marker — the team would have received the two most important tickets without their last sections (internal links, tracking, keep/kill, score). It also resent whatever had been withdrawn.
- **Tickets are never cut now.** A ticket that fits one message after dropping only what the standing rules already say (TRACK, KEEP / KILL) and our own SCORE line goes as one box, and the box **names** what was left out. Otherwise **every section is sent, split into parts** ("TW-2646-01 · part 1/2 — send all 2 parts, in order"), each ≤ 3,600 characters. The two long engine tickets (festival, profit) now come as 2 parts each — two taps, nothing missing.
- **Resend is lossless.** It sends the stored parts, or — for tickets made by older versions — splits the full text into parts. It sends only tickets still `NEW`: a withdrawn ticket is never sent again. New tracker column `box` holds the parts.
- **A Google Trends answer of "no data" now counts as TINY** (Google returns an empty table for searches too small to chart; 3 of 9 searches in the live run came back that way and were left "unverified" at ×0.6). A *refused* request still stays unverified. The log now says, for every search, which of reused / answered / no data / refused it was.
- **Seller seeds now carry a tool word** ("amazon seller calculator", "flipkart seller calculator", "meesho seller tools", "product title generator", "product description generator", "barcode generator", "product photo resizer", "shipping cost calculator"). The old seeds ("amazon seller", "reselling"…) returned 99 steady searches and **none** tool-shaped (login, central, registration…). This is a bet — it could only be tested offline. The log prints `steady searches N → M tool-shaped candidates` and each Wave-1 evidence match, so next week shows whether it worked.
- **Asks:** developer questions ("sql input sanitization library", "cookie authentication in blazor") are not tools people search for and are dropped.
- **Festival event pages say whether Google can rank them in time.** A new page takes weeks to rank; the old "live by (promote date − 7 days)" assumed it would not. Each event page now carries "SEO: enough lead time to rank" (promote window ≥ 8 weeks away) or "SEO: under 8 weeks… serves the channel, keep it for next year" (on 13 Nov: Christmas, Sankranti, Republic Day are channel-only; Valentine's can rank). Judge the short-notice pages on `tool_use`, not impressions.

**After you replace the files:** run Tool Watch once with `resend = 1`. The four open tickets arrive complete (and the cyclone is not among them); send those to the team in place of the cut-off ones.

## v1.48: Tool Watch, second live run — what the logs showed and what changed
The 13 Nov run produced **0 tickets, 5 filtered, 12 below the bar** — and the logs showed why that was partly wrong, partly right:
- **The digest said "backlog is full" with 5 of 8 used.** The real reason: the weekly allowance (5) was already used by the earlier run the same week. It now says which limit it is, and how to resend.
- **A junk ticket was still in the team's queue.** TW-2646-05 (the cyclone) was issued by the previous version and stayed `NEW`. Tool Watch now **withdraws any open search ticket that today's rules would refuse** (status `WITHDRAWN`, with a "Tell the team: do not build it" line in the digest); it frees the weekly allowance and backlog slot and is never re-ticketed.
- **"Google Trends refused" was false.** 7 searches simply fell past the 10-check weekly limit — and the same top 10 would have been checked every week, starving the rest. Last week's curves are now **reused** (`TOOL_CANDIDATES`), so each week's checks go to new searches; "not checked (limit)" and "refused" are separate messages.
- **The demand-curve test measured the anchor, not the candidate.** Compared with a huge term ("image compressor"), every small query reads 0 or 1 on Google's 0–100 scale, so 10 of 10 came out tiny or spike — a test that rejects everything separates nothing. Now the **shape comes from the query alone** (full resolution; a query searched on under 15% of the 90 days is tiny, a late burst is a spike) and a **second request against the anchor** gives volume only for steady curves. `TINY_REL` is now 0.005 (below Google's resolution next to the anchor) instead of 0.02.
- **Filters missed:** a Hinglish AI prompt typed as a "search", website names (unmask.com…), people-finders (privacy / DPDP risk — we do not build tools that locate private individuals), social-login tools (unfollow checker), AI image makers and audio recognition (paid models, giants), moon/eclipse trackers (live data), emoji. All now refused with the right reason; the digest groups reasons with counts.
- **Root cause of the thin feed: "rising" is a small base by construction.** The feed now also takes Google's **steady TOP searches for the six seller seeds** (kind `top`, scored on steadiness, not % rise).
- **Evidence for the Wave-1 tools.** A top or rising search that matches an engine tool (fba/fee/profit/margin → profit calculator; RTO/return loss → RTO tool; festival/season → planner…) is shown in the digest as **demand evidence** (and in that tool's ticket) instead of becoming a duplicate ticket. If there is none, the digest says so — "demand still unmeasured" — rather than implying it.
- Run time is longer (two Trends requests per steady candidate, about 6–8 minutes) and Trends may rate-limit more: refused checks are retried once and retried again next week.

**Honest limits:** the top-searches parser follows pytrends' documented `related_queries()` output but was tested offline only; the thresholds (0.005, 15%, 60%, score bar 25) are still first guesses — `TOOL_CANDIDATES` now stores `active_days` and `kind` as well as `rel_volume` and `recent_share` so they can be calibrated after 3–4 runs. **The search feed is a bonus; the Wave-1 tools are the queue.** If the team has not shipped them, more tickets change nothing.

## v1.47: Tool Watch fixes from the first live run (a rising % is not demand)
The first live run ticketed a **cyclone** ("Cyclone Montha" +900%), proposed a "tool" for a query with **zero** tool sites on page 1 as if that were an opening, and a festival verdict had a **gap at 31–44 days**. Reverse logic: what made those win? A % rise ranks the *smallest base* first, and news words look like searches. Fixes:
- **Rise is capped at 5,000%** (a bigger % only means a tinier base) and multiplied by the **demand curve**: Google Trends interest over 90 days, compared with an anchor term (`BR_TW_ANCHOR`, default "image compressor"). `spike` (≥ 60% of interest in the last 14 days) ×0.2, `tiny` (below 2% of the anchor) ×0.5, `ok` ×1.0, unchecked ×0.6. **A rising search is never ticketed without that check.** The shape numbers are stored per candidate (`shape`, `rel_volume`, `recent_share`) in `TOOL_CANDIDATES`, so thresholds can be calibrated from real data (`BR_TW_TINY_REL`, `BR_TW_SPIKE_SHARE` override them).
- **News, weather and live-data filters** (cyclone, storm, election, IPL, train/flight status, gold rate, courier tracking…). They are skipped **with a plain-words reason**; the digest lists "🚫 Filtered (and why)" and "🤔 Considered, not ticketed".
- **Empty page 1 is no longer an opening.** 0 tool sites ×0.5 (no proof anyone wants a tool); 1–2 sites ×1.0 (proof of tool intent, beatable); then crowding discounts.
- **Quality bar** (`BR_TW_MIN_SCORE`, default 25): below it a search is not ticketed, even in a quiet week; the digest says how many fell below. **Staged Wave 1:** 3 engine tools plus the 2 best verified searches in week 1; the 4th engine tool follows in week 2.
- **Festival planner has a full verdict table** (too early → stock now → prepare creatives, **do not order stock** at 31–44 days → promote only → too late), tests on both sides of each boundary, and a "today:" verdict on every event page. The earlier note "too early; stock window opens <past date>" is gone.
- **Google Trends rate limits:** each seed retries after a 60 s pause; the run stops cleanly after 3 lost seeds instead of silently skipping them.
- Cosmetics: proper minus signs, no `<site>` placeholder in the rules box, "1 day" singular.

**Calibrate, don't trust:** the anchor term, the spike/tiny thresholds and the score bar of 25 are first guesses. After 3–4 weekly runs, read the `TOOL_CANDIDATES` tab (rows with a shape and a score) and move the thresholds so the tickets you would have rejected sit below the bar. Demand for the four engine tools is still unmeasured: that is what `BIZOPS_SITE` + `GSC_SITE` tracking is for.

## v1.46: Tool Watch — ready-to-send tickets for the bizopstool team (automatic, weekly)
**What it is:** a runner (`toolwatch.yml`, Mondays 14:00 IST, after the 12:00 automation report). It writes the tickets the bizopstool tech team builds from. **You copy and send; you fill in nothing.**

**Three feeds, one queue** (a supermarket of tools, not one shop):
1. **Engine tools (Wave 1, the seller aisle):** Amazon India seller profit & ROI calculator, minimum selling price calculator, RTO & return loss calculator, festival stock & promotion planner. The logic already runs in our engine; the test cases in each ticket are computed by it. `test_toolwatch.py` recalculates the real CALCULATOR workbook in LibreOffice and checks the ticket numbers equal it exactly.
2. **Rising searches:** tool-shaped Google Trends India searches (calculator / generator / converter / checker / maker / planner / tracker / finder…), 14 seeds a week. Regulated topics (tax, GST, loans, insurance…), banned lanes and non-tools are dropped. Scored on rise, persistence (seen in 2+ weeks ×1.3), seller fit (×1.25), simple tools that AI answers and giants own (×0.6) and how crowded page 1 is (Brave, optional).
3. **Asks:** the week's OPPORTUNITIES rows without vendor-only, platform-locked, regulated or large-build items.

**What you get in Telegram (admin chat only):** a short digest, a **standing rules** box (once), then **one grey tap-to-copy box per ticket** (≤ 3,600 characters, so it never splits), plus all tickets in one `.md` file. Each ticket has: why now with numbers, who uses it, inputs/outputs, the exact formula, test cases, page title / meta description / H1 / FAQ questions, internal links to three neighbouring tools, events to track, and the keep/kill rule.

**Automatic tracking (no reporting back):**
- Set repository variable **`BIZOPS_SITE`** (e.g. `https://yourdomain`). The runner checks `/tools/<slug>` (and `sitemap.xml`) each week: a published page turns the ticket **NEW → LIVE**.
- Set **`GSC_SITE`** (e.g. `sc-domain:yourdomain.com`) to read Search Console impressions and clicks per tool. One-time: enable the *Search Console API* in the Google Cloud project, and add the service account's e-mail (the `client_email` in `GOOGLE_CREDS_JSON`) as a user on the property.
- 8 weeks after going live a tool is **KEEP** (≥100 impressions a week or ≥20 clicks in 28 days) or **KILL** (merge or remove). Portfolio rules: at week 8, fewer than 3 judged tools meeting the bar → change the mix; at week 16, under 1,000 search clicks in 28 days → park the lane.
- Past the ship-by date + 7 days, the digest gives a status-check message to copy to the team.

**Throttles:** `BR_TW_TICKETS` (new tickets a week, default 5), `BR_TW_MAX_OPEN` (stops adding when the team has this many unfinished tickets, default 8). Tabs: `TOOL_TICKETS` (the tracker, protected from shrink), `TOOLWATCH_SEEN` (persistence), `TOOL_CANDIDATES` (everything scored this week, with the reason). Re-send a week's tickets: run the workflow with `resend = 1`.

**Also in v1.46 (automation report):**
- Focus words are now a **boost (×1.0 vs ×0.8), not a filter**; nothing is "Parked" any more. 🎯 marks needs matching your focus words.
- A GitHub repo counts as competition only if it is about the need's **distinctive** words (generic ones like desktop/panel/tool don't count) — the 2016 `clippy.desktop` match on a usage-stats request is gone.
- Each need shows the **repos** its GitHub asks came from, so a label can be checked. The report header now shows `v1.46`.

**Honest limits:** rising ≠ will rise (we detect early signals, we cannot predict); a new site takes weeks to rank, so calendar-known and evergreen tools beat spikes; engine-tool demand is a bet until week 8; Search Console reading is written to Google's documented API but could only be tested offline here.

## v1.45: full audit fixes (storage safety, fair pick test, honest lead test)
- **Storage could wipe a tab.** Saving used to clear the sheet and then write; a failed write (Google returns errors now and then) left the tab empty, including HISTORY. Now it writes first and trims leftovers after, retries failed reads and writes 3 times, and **refuses to overwrite a protected tab with less than half its rows** (HISTORY, PICKS, DETAIL_HISTORY, GTRENDS_HISTORY, YT_HISTORY, SOURCING_HISTORY, DISCOVERY, AFF_POST_LOG, ASKS, OPPORTUNITIES), alerting the admin chat instead.
- **The pick test was unfair in two directions.**
  - Baselines could be sold-out products (which drop off the list fast), flattering the picks. Baselines must now have a price, like picks.
  - "Rank no worse than on pick day" penalises picks, which are chosen just after a jump. A second measure, **still in the top 100** (`alive_d7/14/28`), is now tracked beside it.
  - Daily picks share one market wave, so 40 picks from 4 days isn't 40 independent results. The verdict now needs **10 different pick days** and reports **days won** ("picks beat random on 9 of 12 days"). "Beat random" requires winning at least 60% of days. The verdict line appears in the daily digest (🧪).
- **One-day entrants are no longer picked.** 7–15 products enter each category's top 100 daily and most leave again. A pick must have been listed on 3+ of the last 14 days, or have entered in the top 30.
- **The discovery lead test could pass by chance.** One-word searches ("toys") match some Amazon title any day. Now: phrases need 2+ words; an item counts only if it was absent from Amazon's lists for the 14 days **before** discovery; one-word searches never count as "Amazon agrees".
- **Product searches get a YouTube check** ("<phrase> review" videos from 3+ channels with real views, top 4 phrases a day), so the Flipkart lane's 2-source rule can actually be met. Before, it depended on creators linking the same product, which found 0 in live runs.
- **Schedules:** the daily fetch has a concurrency group, so the backup run waits for the Worker's run instead of fetching Amazon twice at once.
- **Alerts:** if there's no fresh Amazon data by 11:00–12:00 IST, the admin chat is told that posts are paused. "No eligible picks" is sent once in the morning, not every 2 hours.
- **Festivals added:** Makar Sankranti/Lohri/Pongal (kites, 14 Jan 2027), Republic/Independence Day (flags), Mother's/Father's Day.

**Known limits (not fixed, by design or not fixable here):** see the audit notes in the chat of 11 Nov 2026: customer-return losses aren't in the calculator; pincode delivery can't be checked; scraped Amazon data shouldn't be resold; affiliate income stays small until the audience is large.

## v1.44: Flipkart lane works through an affiliate network
Direct Flipkart sign-ups are paused at times. Set the secret `FLIPKART_LINK_TEMPLATE` to your network's deep-link format, with `{url}` where the Flipkart address goes (copy it from the network's link or deep-link tool; it's different for every network and account). The engine URL-encodes the Flipkart search address into it. Either `FLIPKART_AFFID` (direct) or `FLIPKART_LINK_TEMPLATE` (network) enables the lane. The lead-test gate still applies. Check that the network's Flipkart campaign is active and allows Telegram traffic.

## v1.43: Flipkart lane (rising searches confirmed by Google Trends + YouTube)
- **What:** once a day, in the drip job (first run at or after `BR_FLIPKART_HOUR`, default 19:00 IST), up to 3 product searches from the latest **Independent discovery** run where **2+ independent sources agree**. Not already on Amazon's bestseller lists (that's the Amazon lane), in season, nothing banned, not posted in the last 7 days.
- **Post:** "🔎 Rising Google searches · shop on Flipkart". Each item is a Flipkart **search** link (`flipkart.com/search?q=…&affid=…`), followed by an #ad line. It goes through the public bot to the same chat(s) as the Amazon picks.
- **Gate:** it posts publicly only when the secret `FLIPKART_AFFID` is set **and** the discovery lead test has passed (≥30 items judged, ≥30% reached Amazon's lists within 14 days). Otherwise the admin chat gets a **DRAFT** with the reason. `BR_FLIPKART_FORCE=1` skips the lead test (not advised).
- **Own log (FK_POST_LOG tab),** so it never blocks the Amazon slot's 75-minute duplicate check.
- **Check Flipkart's affiliate terms** that search-page links with `affid` earn commission before relying on them.

## v1.42: fix: focus search on Software Recommendations limited to the last 12 months (it had no date limit and counted years-old questions as current demand)

## v1.41: vendor-only requests, better crowding query, AI = Developer
- **VENDOR-ONLY:** on 10 Nov both "enough evidence" needs were feature requests on `anthropics/claude-code` (408 and 158 votes): real demand that only the product's maker can build. Requests made *only* as issues on a big vendor's own repo (Anthropic, OpenAI, Microsoft, Google, GitHub, Apple, Meta, AWS, Cursor, Vercel, JetBrains, Notion) are now VENDOR-ONLY, unless the request has an outside angle (API, CLI, plugin, extension, MCP, proxy/base URL, SDK, export, webhook, self-hosting). Score ×0.2, never shortlisted, listed on one line (🏢). If someone *outside* also asks for it as a tool (HN, Software Recs), it's not vendor-only.
- **Web crowding** searches the readable request ("Bring back the usage stats panel…"), not word stems, so it stops matching random sites.
- **AI and LLM words** (llm, model, prompt, agent, mcp, claude, gpt, token, inference, rag, copilot…) mark a need as **Developer**, not General.

## v1.40: evidence counts votes, builds up over 8 weeks, and searches your focus
The first v1.39 run kept 106 real requests but found 0 needs with enough evidence. That wasn't because the filter was too strict, but because the evidence rule ignored votes, reset every week, and the searches weren't aimed at the focus niche.
- **Votes count:** each request = 1 + extra for its votes or views (GitHub +1 per 25 👍, HN +1 per 40 points, Software Recs +1 per 1,500 views; capped at +10). A single heavily voted request (50+ 👍 issue, 100+ point HN post, 5,000+ view Software Recs question) reaches **Medium** confidence on its own.
- **8-week memory (ASKS tab):** requests are stored and regrouped with the last 8 weeks, so a need asked a few times a month adds up.
- **Focus searches:** with `BR_AUTO_FOCUS_WORDS` set, the engine also searches HN **posts and comments** (last 90 days), GitHub feature requests with the word in the title (last 180 days, 3+ 👍) and Software Recommendations, for each focus word ("llm tool", "token tool", "api cost"…). The same tool-request filter applies.
- The report shows evidence per need: asks, vote-weighted asks, total 👍/points, sources.

## v1.39: only real tool requests count (from the first live run, 10 Nov)
The first live v1.38 run read 430 items and reported 411 "needs", nearly all noise: 199 Stack Overflow debugging questions, "Show HN" launches counted as demand, and unreadable word-fragment names.
- **Only tool requests count as demand:** "is there a tool / app / library…", "looking for…", "alternative to…", "how do I automate / bulk / batch…", "tool to convert / export / sync…". GitHub feature requests, Google Trends tool searches and Software Recommendations questions count as requests by nature.
- **Launches are never demand:** "Show HN", "Launch HN", "I built…" are dropped (they're competition, already counted as supply).
- **New source: Software Recommendations** (softwarerecs.stackexchange.com, same official API), a site made of "I need a tool that does X" questions from the last 30 days.
- **Readable names:** each need shows its strongest request, cleaned ("tracks LLM API cost per user"), not word stems.
- **The report leads with evidence:** "Real tool requests this week: N of M". Needs with Medium+ confidence are listed in full. If there are none, it says **"No need has enough evidence this week. Nothing to act on."** Single signals get one line.
- **Telegram:** long messages now split at line breaks, so links are never cut in half. This applies to every message.

## v1.38: evidence, confidence, focus, web crowding, regulated flag
- **Evidence and confidence:** each need shows its asks and sources, a confidence (Low < 4 asks or 1 source; Medium; High = 8+ asks from 3+ sources), and **how its score was made** (base, then ×0.7 regulated, ×0.4 web-crowded, ×0.6 low evidence). Low confidence can never be BUILD; it shows as **UNPROVEN**.
- **⚠️ Regulated:** tax/GST/invoices, payroll, legal, medical, insurance, loans and KYC are flagged and scored down. A shortlisted regulated need says "check tax/legal rules & liability first".
- **Focus:** set `BR_AUTO_FOCUS_WORDS` (repository variable, comma-separated, e.g. `llm,token,openai,api cost`). Off-focus needs go to **🅿️ Parked** and are never shortlisted. The report warns when no focus words are set.
- **Web crowding (optional):** with a `BRAVE_API_KEY` secret, the engine counts dedicated tool sites on the first results page (forums, GitHub and social sites excluded). 3+ makes a need CROWDED. It uses Brave Search API's official free monthly credit, because Google's Custom Search API is closed to new customers and shuts down 1 Jan 2027.
- **Repos** show their last commit date and a quality note (hobby, stale, maintained or active). ClearTax was added to the GST apps.
- **Links** to the example requests appear in the message, not just the sheet.

## v1.37: stronger judgement in the automation report
- **Competition beyond GitHub:** launched products (Hacker News "Show HN" posts with 10+ points, last 2 years), npm packages for developer needs, and a **known-apps list** for business problems (Zoho, Vyapar, Khatabook, Tally, WATI…). The 10 Nov run called GST invoices "BUILD" because GitHub had only ★3 projects; it's now CROWDED.
- **HARD:** needs that depend on platform permission (WhatsApp, Instagram, banking/UPI, scraping, iOS…) are labelled HARD and scored down.
- **Effort S/M/L:** converters, generators and templates are S; AI, video, sync, mobile and payments are L. Scores are lowered for bigger builds.
- **Audience:** each need is Business, Developer or General, and the report is split by audience. `BR_AUTO_FOCUS` = all | business | developer.
- **📊 Data edge:** needs your Bazaar Radar data can power (sellers, prices, trends, stock, sourcing, festivals) get a boost.
- **Reuse only from 20+ star projects;** practice repos are never suggested.
- **Streaks survive re-wording:** a need is matched to last week's similar need (≥50% shared words).
- **Distribution-independent demand test (WAITLIST tab):** `shown` = people you showed it to directly, `positive` = said they'd use or pay. BUILD when ≥30% of at least 20 are positive, or 30+ organic sign-ups. The test measures demand, not your site's traffic.
- The report header shows the version, so you can tell which code ran.

## v1.36: open discovery: any tool people want built
The weekly "Automation opportunities" run now **discovers needs** instead of using 16 fixed business groups (those stay as a 🏪 tag).
- **Sources** (official APIs only, no scraping):
  - **GitHub issues** labelled feature request / enhancement / feature, created in the last 90 days, with 10+ 👍, most-voted first.
  - **Hacker News**: "is there a tool / app…" posts from the last 30 days (Algolia API).
  - **Stack Overflow**: this week's most-viewed questions with no accepted answer, any topic.
  - **Google Trends**: "is there an app", "chrome extension for", "free tool to", "online converter", "app to automate".
- **How:** each request is reduced to its key words ("Ask HN: Is there a tool that converts Figma designs to Flutter?" → convert figma design flutter). Similar requests are grouped across sources (🔗 = 2+ sources). The top 25 groups (`BR_AUTO_SUPPLY_CHECKS`) are checked on GitHub: maintained, easy to use, licence. Each is labelled BUILD / PACKAGE / CROWDED.
- **Shortlist ⭐:** top 10 for 3+ weeks, 2+ sources, not CROWDED. Then a waitlist page on bizopstool; build at 30+ sign-ups or 3+ people who'd pay.
- Banned topics (betting, loans, trading/crypto, piracy, adult) are removed from every source.

## v1.35: automation opportunities for bizopstool (weekly, admin only)
New workflow **"Automation opportunities"** runs Mondays at 12:00 IST. It finds business problems people are trying to automate and checks whether a usable, legally reusable solution already exists.
- **Demand:**
  - Stack Overflow (official API): this week's questions tagged excel-vba, google-apps-script, google-sheets, excel, automation and vba, with views and unanswered counts.
  - Google Trends: rising searches around 6 rotating automation seeds (excel macro, gst invoice, tally to excel, …).
  - Both are grouped into **16 fixed business-problem groups** (GST invoices, WhatsApp messages via the official API, reconciliation, inventory alerts, Tally exports, seller operations…), so weeks are comparable.
- **Supply (GitHub search):** for each group, how many projects exist; how many are **maintained** (updated in the last 12 months, 20+ stars); how many look **usable by non-programmers** (app, add-on, extension, no-code); and each top project's **licence**.
- **Gap type:**
  - **BUILD:** nothing maintained exists.
  - **PACKAGE:** code exists but only developers can use it.
  - **CROWDED:** several easy tools already exist.
- **Shortlist:** in the top 5 for 3+ weeks, with both sources agreeing, and not CROWDED. Next step: an honest **waitlist page** on bizopstool, logged in the **WAITLIST** tab (signups, sellers_asked, sellers_yes). **Build only when** 30+ sign-ups or 3+ sellers say they'd pay.
- **Licences:** MIT, Apache, BSD or ISC: reuse with credit. GPL: you must open your own code. No licence: **do not reuse**.
- **No setup needed:** GitHub provides `GITHUB_TOKEN` automatically. `STACKEXCHANGE_KEY` is optional (it raises Stack Overflow's daily limit).

## v1.34: discovery fixes from the second run on 10 Nov
- **Growing seeds are rechecked daily** against the current filters and festival window, so earlier noise ("jobs hunt stripe engineer", "namjooning stanford") can't come back as a seed.
- **Skills need a learning word** (course, tutorial, learn, certification, roadmap, …): "python course" ✓, "burmese python…" ✗.
- **Apps accept short brand or tool names** (≤3 words, e.g. "capcut pc"), because app searches are mostly brands. They still need YouTube confirmation to count as 2+ sources.
- **Products are checked against the festival window,** so post-Diwali "diwali gift hamper same day" is dropped.
- **The summary shows, per lane, how many searches were found and why each was dropped** (blocked, off-topic, weak, out of season, seeds with no rising searches).
- **YouTube:** a refused search (429) waits and retries; if it still fails, it's skipped and the rest continue. Only "daily quota used up" stops YouTube for the day.

## v1.33: discovery seeds fixed (fewer films and news, more real products)
On 10 Nov, the broad seeds ("flipkart", "amazon", "course", "software") produced films, K-pop news, a local tutor and a forensics company. The daily fetch's specific seeds found real products the same day ("hot lunch box for office" +144,800%).
- **Specific seeds, rotated daily:** 42 product types (10 a day), 25 skills (5 a day) and 25 software types (5 a day), defined in `discovery.py` (PRODUCT_SEEDS, SKILL_SEEDS, APP_SEEDS). Up to 3 growing seeds go first.
- **Results must be about their seed:** they share a seed word. In apps, comparison searches ("capcut alternative", "X vs Y") also count.
- **More blocked words:** release date, OTT/streaming names, box office, cast; university and college names in skills; jobs, hiring, salary, lawsuits and breaches in apps.
- **Unnamed YouTube products** (links without a name in the address) show the video's title as a label.

## v1.32: stock analysis is admin-only, with an early preview
- **The seller brief no longer includes the stock-ready list.** That's your edge. It goes to the admin chat only ("🔒 Stock-ready, admin only").
- **Admin preview:** while there are fewer than 21 days of history, the digest's 📦 Stock gate line adds "🔒 Admin preview", which applies the same tests scaled to the days available (in the top 100 on at least ⅔ of them). It's less reliable and **never changes the calculator's Decision**, which keeps the real 21-day gate. Turn it off with `BR_STOCK_PREVIEW=0`.
- **Fixes:** "ladi light" (a string of lights) is festive decor, not explosives. Only "bomb/cracker ladi" counts as a firecracker.

## v1.31: three admin-only lanes and release gates
Nothing below is posted publicly. Each lane is released only when its gate shows ✅ in the **🚦 Release readiness** message, sent every Monday after discovery (force it with `BR_READINESS=1`).

| Lane | Sources (2 must agree) | Gate |
|---|---|---|
| Product discovery (future Flipkart lane) | Google Trends (Shopping) + YouTube creator links + Amazon lists | Lead test: ≥30 items judged, ≥30% reach an Amazon list within 14 days |
| **Skills & courses** | Google Trends ("course", "learn", "certification", "classes" + growing seeds) + YouTube: "<skill> tutorial" videos from the last 30 days with ≥100 views/day on 3+ channels | ≥20 items with 2 sources, and ≥50% rising again 10+ days after first found |
| **Apps & software** | Google Trends ("app", "ai tool", "software" + growing seeds) + YouTube: "<app> review" videos, same rule | Same as skills |
| **Seller data (B2B)** | The weekly brief, now with **Early signals** and **Held up for 2–3 weeks** sections | SELLER_LEADS tab: ≥10 sent, ≥3 "yes", median price ≥ ₹299/month (`BR_B2B_MIN_PRICE`) |

- **Blocked in every lane:** betting and real-money games (banned in India, 2025), loan and instant-cash apps, trading, stock tips and crypto, pirated or modded software, adult content. Also blocked: exam results, jobs and government services in skills, and login or customer-care searches in apps.
- **Budget:** Google Trends makes 20 lookups a day (products 10, skills 5, apps 5). YouTube uses about 1,500 extra units a day (6 product searches plus 4 checks each for skills and apps), well within the free 10,000.
- **SELLER_LEADS:** one row per seller you send the brief to: `date_sent`, `replied` (yes/no), `price_inr_month` (the price **they** said).

## v1.30: independent discovery (not dependent on Amazon's lists)
New workflow **"Independent discovery"** runs daily at 11:00 IST. It writes to the DISCOVERY tab and the admin chat; **nothing is posted publicly**.
- **Google Trends:** rising searches around shopping intent, using 6 base seeds ("flipkart", "amazon", "meesho", "under 500", "under 1000", "gift for") plus up to 8 **growing seeds**. A strong (≥1000%) multi-word product search becomes a seed for 7 days (DISC_SEEDS tab). Non-product searches (news, sales dates, customer care, "how to") are removed, and shop and budget words are stripped ("air fryer flipkart under 3000" → "air fryer"). Capped at 14 lookups a day; it stops early if Google refuses.
- **YouTube:** 6 searches ("amazon finds india", "flipkart haul", "meesho haul", …) for Indian videos from the last 7 days, sorted by views. Product links in video descriptions (amzn.to, fkrt.it, …) are followed **only to the redirect address**, never loading the shop page, to get the exact Amazon or Flipkart product ID and name. Products are counted by **different channels**; 3+ creators is a signal. Uses about 700 of the free 10,000 daily API units.
- **Agreement:** each item lists its independent sources (gtrends, youtube, amazon_list). It's marked **"2+ sources"** only when two agree, and a Trends-only search must also appear on 2+ days.
- **Lead test:** for items found while *not* on Amazon's lists, the summary reports how many reached an Amazon bestseller list within 14 days, and the median lead in days. This shows whether these sources give early warning.
- Settings: `BR_DISC_TRENDS_CALLS` (14), `BR_DISC_MAX_LINKS` (300 short-link lookups a day).

## v1.29: on-time scheduling with a Cloudflare Worker
GitHub's `schedule:` often starts runs 15–60 minutes late. `cloudflare_worker/` holds a free Worker that fires on Cloudflare's punctual cron and starts the daily fetch (07:45 IST) and the drip posts (9 am–9 pm IST, every 2 hours) through GitHub's API. Those runs usually begin within about a minute.
- GitHub's schedules stay as a **backup**. A backup daily run exits if today's data is already in, so Amazon isn't fetched twice. A drip run within 75 minutes of the last post does nothing (`BR_DRIP_MIN_GAP_MIN`).
- If GitHub refuses a start request (for example, an expired token), the Worker alerts the admin chat.
- Setup is in `cloudflare_worker/SETUP.md`.

## v1.28: WhatsApp post of the day
When the morning pool is built, the admin chat gets **one** ready-made WhatsApp post with the top 5 picks and a tap-to-share link (choose your channel, send). The plain text follows it so you can copy it on a computer. WhatsApp can't make words clickable, so every product shows its full tagged link on its own line; copying the Telegram post into WhatsApp loses the links.

## v1.27: posts every 2 hours (drip) + separate affiliate sheet
- **The daily fetch moves to 07:45 IST** so data is ready before the first post.
- **New workflow "Affiliate drip posts"** runs at 9, 11, 1, 3, 5, 7 and 9 IST.
  - The first run of the day builds a **pool** of up to 12 picks: in stock, rising, in season, and not posted in the last 7 days. It records the forward test once and sends the pool list and share kit to the admin chat.
  - Each run posts the next 1–2 picks as one compact message, best picks in the morning.
  - It makes no Amazon requests and takes about 30 s per run.
- **The weekly workflow now sends only the seller brief**, so picks aren't posted twice.
- **Separate affiliate spreadsheet:** set `AFFILIATE_SHEET_ID` and the tabs AFF_POOL, AFF_POST_LOG, SHARE_KIT, COMMISSION and AFF_ACTUALS live there; the estimator reads COMMISSION and AFF_ACTUALS from it. If it isn't set, everything stays in the main sheet.
- **Settings:** `BR_POOL_SIZE` (default 12), `BR_POOL_PER_GROUP` (6), `BR_DRIP_HOURS` (default `9,11,13,15,17,19,21`; also change the cron in drip.yml), `BR_NO_REPEAT_DAYS` (7).

## v1.26: in-stock check for picks
Amazon keeps ranking sold-out items on its bestseller lists for days, but shows no price for them. On 9 Nov, 5 of the 8 posted picks were unavailable, and every one of them had no price on that day's list. Picks now require a price on today's list. Dropped items appear in the admin chat as "probably out of stock".

## v1.25: one compact post
All picks now go out as **one message**: a numbered list where each short product name is a tagged link, followed by the category, the rank move (`#45 → #12`, `new at #5`, `holding #1`), a 📅 label for seasonal items, and one disclosure line. Set `AFF_POST_STYLE=per_product` to go back to one message per product. Chhath was added as a seasonal event (15 Nov 2026, 4 Nov 2027).

## v1.24: rerun publishes picks that were only drafted
If today's picks were made before `AFFILIATE_CHAT_ID` and the tag were set, they were only drafted to the admin chat. A rerun now posts them **once** to the public chat, rebuilt with your current tag. The forward test isn't re-drawn. A new `PUBLIC_POSTS` tab records what went where, so nothing is posted twice. A run that has nothing to do now tells the admin chat why, instead of stopping silently.

## v1.23: separate public bot
Set the secret `AFFILIATE_BOT_TOKEN` (for example @OneZeroDeals_bot's token) and public posts go out through that bot, while errors, margins and sourcing stay on the admin bot. If it isn't set, the admin bot posts as before. `AFFILIATE_GROUPS` can now be set as a secret too.

## v1.22: stock gate + hyperlinked posts
- **Stock gate (`stock_gate.py`).** "Never buy stock because something is rising this week." A product is ✅ STOCK-READY only if all of these hold:
  - at least 21 days of history exist
  - it was in the top 100 on 14 or more of the last 21 days, and was first seen at least 14 days ago
  - its rank is not falling (last 7 days compared with the 7 before)
  - products at its rank in that category typically stay 60 days or more
  - for seasonal items, it's inside the stock window
  - it isn't a firecracker

  Otherwise the gate shows 👀 WATCH (with days to go), ❌ FADING, ❌ SHORT WAVES, ❌ season, or ⏳ NEED DATA. It appears in the signals, a new **"Stock gate"** column in CALCULATOR, the digest and the estimator. **Decision** now shows "WAIT — …" until the gate passes.
- **Posts:** the product title and a "👉 Check today's price on Amazon" line are tappable links carrying your tag. No raw URL, and still no price (Associates rules).

## v1.21: crash fix + 1-word search warning
A listing showing "₹ ," with no digits crashed sourcing. Such listings are now skipped, and a failure on one product skips only that product. A one-word search is never rated above low confidence.

## v1.20: IndiaMART parser fixed
GitHub's servers are in the US, so IndiaMART sends them to its **export site** (export.indiamart.com). Its product links look like `/products/?id=…` rather than `/proddetail/`, so every search found 0 listings. The parser now reads both layouts and was tested on the 8 real pages from 9 Nov.
**Caveat:** export-site listings are a narrower set than the domestic directory, and prices are sellers' listed prices, not quotes. Treat the estimate as triage and confirm with real quotes.

## v1.19: fixes from the 9 Nov logs
- **Telegram rate limit (429).** On 9 Nov two affiliate messages and the brief were rejected and lost. Sends are now paced (1.2 s apart) and retried after Telegram's "retry after N".
- **`BR_CATEGORIES` check.** Values with spaces ("solar light") are search words, not Amazon category slugs. They're now ignored, and the digest tells you to move them to `BR_SEED_KEYWORDS`. On 9 Nov they caused 18 blocked requests and about 10 extra minutes of captchas.
- **State keywords are generic** ("dog food", "travel bag", "wall decor", "hard water"). They're chosen as the words shared by many product titles, so brand names drop out. Seasonal items outside their window and firecrackers are skipped.
- **Sourcing** skips products out of their stock window, so Diwali items are no longer searched after Diwali, and skips firecrackers.
- The digest header shows the correct version.

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
