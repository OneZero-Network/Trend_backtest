"""
Compliance "REVIEW REQUIRED" flags + competition notes.

THIS IS NOT LEGAL CLEARANCE. Keyword/category matching is brittle by nature:
  * a flag means "a certification regime probably applies — check before buying stock"
  * NO flag means "nothing matched" — NOT "safe". The output says so explicitly.
Lists of certified products (BIS Compulsory Registration Scheme, BIS QCOs for toys,
FSSAI, CDSCO cosmetics/medical devices) change by notification. Verify on the regulator's
site (bis.gov.in, fssai.gov.in, cdsco.gov.in) or with a compliance consultant.

Category-based rules catch items keywords miss (e.g. a "wooden block set" listed under
Toys is flagged because of the category, even though it has no electronics keyword).
"""
import re

# category slug → regimes that apply to (almost) everything in it
CATEGORY_RULES = {
    "toys":        ["BIS (toys QCO)"],
    "baby":        ["BIS (toys QCO) — if a toy", "CDSCO/FSSAI — if baby care or food"],
    "grocery":     ["FSSAI"],
    "hpc":         ["FSSAI — if supplement/nutrition", "CDSCO — if drug/medical device/cosmetic"],
    "beauty":      ["CDSCO/Drugs & Cosmetics Act — cosmetics labelling, import registration if imported"],
    "electronics": ["BIS CRS — if listed electronic product"],
    "computers":   ["BIS CRS — if listed electronic product"],
}

KEYWORD_RULES = [
    (r"\b(charger|adapter|power ?bank|usb|led|lamp|bulb|speaker|earphone|earbud|headphone|"
     r"smart ?watch|fitness band|keyboard|mouse|monitor|router|cctv|camera|trimmer|shaver|"
     r"battery|batteries|inverter|extension (?:board|cord|socket|box)|spike guard|plug|socket|cable)\b", "BIS CRS (electronics) — check list"),
    (r"\b(toy|toys|puzzle|doll|blocks?|lego|rc car|kids?|children|baby|teether|rattle)\b",
     "BIS (toys QCO) — check"),
    (r"\b(protein|whey|supplement|vitamin|capsule|gummies|tea(?!\s*-?\s*lights?)|coffee|snack|masala|honey|"
     r"ghee|oil|spice|food|creatine|collagen)\b", "FSSAI — check"),
    (r"\b(serum|cream|lotion|shampoo|sunscreen|spf|face ?wash|lipstick|kajal|cosmetic|"
     r"hair ?oil|moisturi[sz]er|perfume|deo)\b", "CDSCO / cosmetics rules — check"),
    (r"\b(thermometer|oximeter|bp monitor|nebuli[sz]er|glucometer|mask|sanitizer|"
     r"orthopedic|brace|massager)\b", "CDSCO (medical device) — check"),
    (r"\b(pressure cooker|helmet|gas stove|lpg|stainless steel utensil)\b", "BIS (QCO) — check"),
    (r"\b(insect killer|insecticide|pesticide|mosquito (?:spray|repellent|coil|liquid|vaporizer)|cockroach|"
     r"termite|bed ?bug|fly killer|repellent|rat (?:poison|kill))\b",
     "Insecticides Act — registration (CIB&RC) & licence to sell — check"),
    (r"\b(fire ?crackers?|green crackers?|fireworks?|sparklers?|phuljhari|anar|chakri|sky ?shots?|"
     r"(?<!bath )bombs?|(?:bomb|cracker|patakha|pataka) ladi|ladi (?:bomb|cracker|patakha)|diwali line|pataka|patakha)\b",
     "EXPLOSIVES (firecrackers) — PESO licence; Amazon restricts sale — DO NOT promote or stock"),
    (r"\b(cat food|dog food|pet food|kitten food|puppy food|pet treats?|dog treats?|cat treats?)\b",
     "Pet food — check applicable BIS / FSSAI / labelling rules"),
]
_COMPILED = [(re.compile(p, re.I), tag) for p, tag in KEYWORD_RULES]

NOT_FLAGGED = "NOT FLAGGED (≠ cleared — verify)"


def compliance_flags(title, category_slug):
    tags = list(CATEGORY_RULES.get((category_slug or "").strip(), []))
    for rx, tag in _COMPILED:
        if rx.search(title or "") and tag not in tags:
            tags.append(tag)
    return "REVIEW: " + "; ".join(tags) if tags else NOT_FLAGGED


def competition_note(sellers, bought_month=None):
    """Seller count on ONE listing, read together with demand.
    We only see products already on bestseller lists, so demand is proven: few sellers
    here usually means brand-owned / private label (you can't just join the listing),
    not "no demand"."""
    try:
        s = None if sellers is None or str(sellers) in ("", "nan") else int(float(sellers))
    except (TypeError, ValueError):
        s = None
    try:
        b = None if bought_month is None or str(bought_month) in ("", "nan") else int(float(bought_month))
    except (TypeError, ValueError):
        b = None
    dem = f" · {b:,}+ bought/mo" if b else ""
    if s is None:
        return "Sellers unknown" + dem
    if s >= 10:
        return (f"Crowded listing ({s} sellers): price war likely — only enter with a cost edge or own "
                f"variant; high demand can still justify it{dem}")
    if s <= 2:
        return (f"{s} seller(s) on a bestseller: likely brand-owned/private label — sell your own "
                f"variant/listing, not this ASIN{dem}")
    return f"Moderate competition ({s} sellers){dem}"


# ── SEASONAL demand: a trend that ends on a fixed date is the worst bulk buy ─────────
# Sourcing lead time (quote → sample → bulk → Amazon inbound) is typically weeks. A Diwali
# diya trending today will usually arrive after Diwali → dead stock until next year.
SEASONAL = [
    ("Diwali", r"\b(diwali|deepawali|diya|diyas|deepak|rangoli|laxmi pooja|lakshmi puja|dhanteras)\b"),
    ("Halloween", r"\b(halloween|haunted|pumpkin costume|skeleton costume)\b"),
    ("Christmas/New Year", r"\b(christmas|xmas|santa|new year|reindeer)\b"),
    ("Holi", r"\b(holi|gulal|pichkari)\b"),
    ("Rakhi", r"\b(rakhi|raksha bandhan)\b"),
    ("Navratri/Durga Puja", r"\b(navratri|garba|dandiya|durga puja)\b"),
    ("Ganesh Chaturthi", r"\b(ganesh chaturthi|ganpati)\b"),
    ("Eid", r"\b(eid|ramadan|ramzan)\b"),
    ("Karwa Chauth", r"\b(karwa chauth|karva chauth)\b"),
    ("Chhath", r"\b(chhath|chhat puja|soop|supli|daura)\b"),
    ("Makar Sankranti/Lohri/Pongal", r"\b(kite|kites|patang|manjha|manja|charkhi|firki|sankranti|lohri|pongal|uttarayan)\b"),
    ("Republic/Independence Day", r"\b(tiranga|tricolou?r|national flag|indian flag|independence day|republic day)\b"),
    ("Mother's/Father's Day", r"\b(mother'?s day|father'?s day)\b"),
    ("Valentine's", r"\b(valentine)\b"),
    # decor that peaks for Diwali / Christmas / weddings even when the title names no festival
    ("Festive decor", r"\b((string|fairy|curtain|pixel|rope|waterfall|serial|diwali|ladi|jhalar)\s+lights?|toran|bandhanwar|"
                      r"jhalar|led\s+(string|curtain|pixel|rope)|decorative\s+lights?)\b"),
    ("Monsoon", r"\b(raincoat|umbrella)\b"),
    ("Summer", r"\b(air cooler|neck fan|ice pack)\b"),
    ("Winter", r"\b(room heater|blower heater|thermal wear|electric blanket)\b"),
]
_SEAS = [(name, re.compile(rx, re.I)) for name, rx in SEASONAL]


def seasonal_flag(title):
    """→ '' or 'SEASONAL: Diwali, Halloween'. A flag means demand is tied to a date or season."""
    hits = [name for name, rx in _SEAS if rx.search(title or "")]
    return "SEASONAL: " + ", ".join(hits) if hits else ""
