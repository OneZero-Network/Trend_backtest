"""
Amazon.in seller fee model — v1.70. One place for the numbers the vendor and drop-ship lanes quote to people.

Why it exists: an external workbook charged a 10–12% referral fee on items under ₹1,000 (zero since 16 Mar 2026),
invented shipping and counted GST on fees as a cost. A seller spots that in the first minute of a call. Every figure
here is either PUBLISHED (with its source) or ASSUMED (labelled so, overridable), and the lanes never quote one as
the other.

Published (checked 22 Nov 2026):
  referral   0% below ₹1,000 in 1,800+ categories from 16 Mar 2026 (Amazon fee guide). Sports is not named in
             Amazon's list of examples → every result says "confirm the category in the fee preview".
  closing    fee groups differ (₹14–₹27 up to ₹300, ₹15–₹23 for ₹301–500, ₹75 above ₹1,000 for '*' categories,
             after the +₹1 / +₹3 rise of 7 Sep 2026; Zoutons, read from Amazon's fees page on 5 Nov 2026).
             We use the HIGHEST published figure in each band, so a margin is never flattered.
  cancel     since 17 Aug 2026 a seller-caused cancellation on Easy Ship / Self Ship costs 10% of the order value
             (below ₹10,000) + 18% GST. Drop shipping's main risk is a supplier stock-out, so it is priced in.
  shipping   v1.80: Amazon's weight-handling table, Standard STEP level, effective 16 Mar 2026, ex-GST (Zoutons, read
             from Amazon's fee schedule): Easy Ship ₹55 first 500 g · ₹75 to 1 kg · ₹112 to 2 kg · +₹34 per kg to 5 kg ·
             +₹18 per kg after. Fulfilment centre regional / national: ₹39/65 · 54/85 · 78/122 · +24/34 · +13/18.
             Billed on the higher of actual and volumetric weight (L × B × H cm ÷ 5000). Premium / Advanced STEP sellers
             pay less, Basic more. Self Ship pays its own courier (pass ship=…).
Assumed (override with AMZ_FEES='{"closing_501_1000": 40, "referral_above_1000": 0.10, "gst": 0.12}'):
  closing ₹501–1,000 (not published in our sources), referral at ₹1,000+ (category-specific), sale GST (HSN-specific).

GST on Amazon's fees is not a cost here: a GST-registered seller claims it back as input tax credit. Margins are worked
ex-GST on both sides (price ÷ (1+GST) against a supplier cost ex-GST).
"""
import json
import math
import os
import re

FEES_ASOF = "2026-11-22"
REFERRAL_ZERO_BELOW = 1000
CANCEL_FEE_PCT = 0.10            # of order value, seller-caused cancellation, orders below ₹10,000 (from 17 Aug 2026)
GST_ON_FEES = 0.18

DEFAULTS = {
    "closing_0_300": 27,          # published, highest group
    "closing_301_500": 23,        # published, highest group
    "closing_501_1000": 45,       # ASSUMED — not in the published table we found
    "closing_above_1000": 75,     # published for '*' categories
    "referral_above_1000": 0.12,  # ASSUMED — category-specific; Amazon cut many by 4–9.5 points in March 2026
    "gst": 0.18,                  # ASSUMED — set your product's HSN rate
    "rto": 0.08,                  # ASSUMED share of orders returned / undelivered (each costs ship out + back)
    "supplier_cancel": 0.03,      # ASSUMED share of drop-ship orders the supplier cannot fill
    "target_margin": 0.25,        # what "room" means in our verdicts
}
ASSUMED_KEYS = {"closing_501_1000", "referral_above_1000", "gst", "rto", "supplier_cancel"}


def config():
    c = dict(DEFAULTS)
    try:
        c.update({k: float(v) for k, v in json.loads(os.environ.get("AMZ_FEES") or "{}").items() if k in DEFAULTS})
    except (ValueError, TypeError, AttributeError):
        pass
    return c


# ── pieces ───────────────────────────────────────────────────────────
def referral_pct(price, cfg=None):
    """→ (rate, basis). Zero below ₹1,000 (published, most categories)."""
    cfg = cfg or config()
    if price < REFERRAL_ZERO_BELOW:
        return 0.0, "published: 0% below ₹1,000 (confirm your category in the fee preview)"
    return cfg["referral_above_1000"], "ASSUMED: category rate unknown — check the fee preview"


def closing_fee(price, cfg=None):
    """→ (₹, basis). Highest published fee in the band."""
    cfg = cfg or config()
    if price <= 300:
        return cfg["closing_0_300"], "published (highest group, ≤ ₹300)"
    if price <= 500:
        return cfg["closing_301_500"], "published (highest group, ₹301–500)"
    if price <= 1000:
        return cfg["closing_501_1000"], "ASSUMED (₹501–1,000 not published in our sources)"
    return cfg["closing_above_1000"], "published ('*' categories, above ₹1,000)"


# Weight-handling fee, Standard STEP, from 16 Mar 2026, ex-GST: (≤0.5 kg, ≤1 kg, ≤2 kg, per kg 2–5, per kg >5)
SHIP_TABLE = {
    "easy_ship":   (55, 75, 112, 34, 18),
    "fc_regional": (39, 54, 78, 24, 13),
    "fc_national": (65, 85, 122, 34, 18),
}
SHIP_BASIS = "published: Amazon weight-handling fee, Standard STEP, from 16 Mar 2026, ex-GST"


def chargeable_kg(kg=None, dims_cm=None):
    """Amazon bills the higher of actual and volumetric weight (L × B × H ÷ 5000), at least 500 g. None if neither known."""
    vol = None
    if dims_cm:
        try:
            l, b, h = (float(x) for x in dims_cm)
            vol = l * b * h / 5000.0
        except (TypeError, ValueError):
            vol = None
    vals = [v for v in (kg, vol) if v is not None and v == v and v > 0]
    if not vals:
        return None
    return max(0.5, max(vals))


def ship_estimate(kg, cfg=None, channel="easy_ship"):
    """Amazon weight-handling fee (₹, ex-GST) for a chargeable weight, from the published table. None if unknown.
    channel: easy_ship | fc_regional | fc_national. Self Ship has no Amazon fee — pass your courier cost as ship=."""
    if kg is None or kg != kg or kg <= 0:
        return None
    a, b, c, d, e = SHIP_TABLE.get(channel, SHIP_TABLE["easy_ship"])
    if kg <= 0.5:
        return a
    if kg <= 1:
        return b
    if kg <= 2:
        return c
    if kg <= 5:
        return c + d * math.ceil(kg - 2 - 1e-9)
    return c + d * 3 + e * math.ceil(kg - 5 - 1e-9)


def parse_dims(text):
    """'30x10x5' / '30 × 10 × 5 cm' → (30.0, 10.0, 5.0) or None."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)", str(text or ""), re.I)
    return tuple(float(g) for g in m.groups()) if m else None


_W = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(kg|kgs|kilograms?|g|gm|gms|grams?)\b", re.I)
_MULT = re.compile(r"^\s*(?:x|×|\*)\s*(\d{1,2})\b|^\s*(?:pack|set)\s+of\s+(\d{1,2})\b", re.I)
_PAIR = re.compile(r"\b(?:pack of 2|set of 2|pair|2\s*(?:pcs|pieces))\b", re.I)
_CAPACITY = re.compile(r"(?:up\s*to|upto|capacity(?:\s+of)?|load(?:\s+of)?|supports?|holds?|bears?|max(?:imum)?|withstands?)\W{0,3}$", re.I)


def weight_kg(title):
    """Shipping weight read from an Amazon title, or None. '5 kg × 2' → 10, '1 kg x 2' → 2, '500 g' → 0.5.
    Load ratings ('up to 200 kg', '150 kg capacity') are not weights and are skipped, as is anything over 40 kg."""
    t = str(title or "")
    for m in _W.finditer(t):
        before = t[max(0, m.start() - 22):m.start()]
        after = t[m.end():m.end() + 14]
        if _CAPACITY.search(before) or re.match(r"\s*(capacity|load|max)", after, re.I):
            continue
        v = float(m.group(1))
        unit = m.group(2).lower()
        kg = v if unit.startswith("k") else v / 1000
        if kg <= 0 or kg > 40:
            continue
        mm = _MULT.search(after)
        n = int(mm.group(1) or mm.group(2)) if mm else (2 if _PAIR.search(t) else 1)
        return round(kg * max(1, n), 3)
    return None


def heavy_for_price(kg, price):
    """True when shipping is likely to eat the margin: more than 0.4 kg per ₹100 of price."""
    try:
        return kg is not None and price and kg / (float(price) / 100) > 0.4
    except (TypeError, ValueError, ZeroDivisionError):
        return False


# ── unit economics ───────────────────────────────────────────────────
def economics(price, cost=None, kg=None, ship=None, cfg=None, dropship=False, ads_pct=0.0, channel="easy_ship", dims=None):
    """Per-unit picture for one product at `price` (₹, GST-inclusive, what the buyer pays).
    cost: supplier cost ex-GST (None → we report the most you can pay instead of a margin).
    → dict with every input labelled, fees, break-even shipping, max supplier cost for the target margin, and a verdict."""
    cfg = cfg or config()
    price = float(price)
    gst = cfg["gst"]
    price_ex = price / (1 + gst)
    ref, ref_basis = referral_pct(price, cfg)
    clo, clo_basis = closing_fee(price, cfg)
    fees = price * ref + clo                                        # ex-GST; GST on fees is claimable
    ship_basis = "given"
    ckg = chargeable_kg(kg, dims)
    if ship is None:
        ship = ship_estimate(ckg, cfg, channel)
        ship_basis = (f"{SHIP_BASIS} ({channel.replace('_', ' ')}, {ckg:g} kg chargeable)" if ship is not None
                      else "unknown (no weight in the title)")
    rto = cfg["rto"]
    ship_mult = 1 + 2 * rto / (1 - rto)                              # each return costs shipping out and back
    cancel = cfg["supplier_cancel"] * CANCEL_FEE_PCT * price if dropship else 0.0
    ads = ads_pct * price_ex
    target = cfg["target_margin"]
    before_ship = price_ex - fees - cancel - ads                     # what is left for cost + shipping
    out = {"price": round(price), "price_ex_gst": round(price_ex, 2), "referral_pct": ref, "referral_basis": ref_basis,
           "closing": clo, "closing_basis": clo_basis, "fees": round(fees, 2), "ship": ship, "ship_basis": ship_basis,
           "ship_with_returns": round(ship * ship_mult, 2) if ship is not None else None,
           "cancel_cost": round(cancel, 2), "kg": kg, "chargeable_kg": ckg, "heavy": heavy_for_price(ckg, price), "gst": gst,
           "target_margin": target, "cost": cost}
    out["max_cost_before_ship"] = round(before_ship - target * price_ex, 2)
    if ship is not None:
        out["max_cost"] = round(before_ship - ship * ship_mult - target * price_ex, 2)
    else:
        out["max_cost"] = None
    if cost is not None:
        out["breakeven_ship"] = round(max(0.0, (before_ship - float(cost) - target * price_ex) / ship_mult), 2)
        if ship is not None:
            profit = before_ship - float(cost) - ship * ship_mult
            out["profit"] = round(profit, 2)
            out["margin"] = round(profit / price_ex, 4)
    out["verdict"] = verdict(out)
    out["assumed"] = sorted(k for k in ASSUMED_KEYS if k != "supplier_cancel" or dropship)
    return out


def verdict(e):
    m = e.get("margin")
    if m is not None:
        return "room" if m >= e["target_margin"] else ("thin" if m >= 0.10 else "no")
    mc = e.get("max_cost")
    if mc is None:
        return "unknown"
    return "no" if mc <= 0 else ("tight" if mc < 0.25 * e["price_ex_gst"] else "open")


def line(e):
    """One plain line a seller can check: '₹549 · fees ₹45 · ship ~₹76 · a supplier price under ₹212 keeps 25%'."""
    bits = [f"₹{e['price']:,}", f"fees ₹{e['fees']:,.0f}"]
    if e["ship"] is not None:
        bits.append(f"ship ~₹{e['ship']:,.0f}" + (" (heavy for its price)" if e["heavy"] else ""))
    else:
        bits.append("weight not in the title: weigh a sample for shipping")
    if e.get("margin") is not None:
        bits.append(f"margin {e['margin'] * 100:.0f}% at ₹{e['cost']:,.0f}")
    elif e.get("max_cost") is not None:
        bits.append(f"a supplier price under ₹{max(e['max_cost'], 0):,.0f} keeps {e['target_margin'] * 100:.0f}%"
                    if e["max_cost"] > 0 else "no supplier price leaves a 25% margin")
    elif e.get("max_cost_before_ship", 0) > 0:
        bits.append(f"before shipping, a supplier price under ₹{e['max_cost_before_ship']:,.0f} keeps {e['target_margin'] * 100:.0f}%")
    return " · ".join(bits)
