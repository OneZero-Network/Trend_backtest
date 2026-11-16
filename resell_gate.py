"""
Resell gate (v1.50) — which trending products can a RESELLER actually source and sell?

The bestseller lists are dominated by things that are great for AFFILIATE posts and impossible for the
reselling lane (calculator, IndiaMART quotes, stock gate):

  brand        NIVEA, L'Oréal, Safari, HRX, Philips… buyers search for the NAME. A generic product from an
               IndiaMART supplier cannot capture that demand, and reselling the branded one needs an
               authorised-distributor deal. (36 of 60 SOURCING rows scored relevance 0.0 for this reason.)
  formulated   cosmetics, skincare, food, supplements, pet food: need a licensed manufacturer (CDSCO/FSSAI),
               not a wholesale generic. Beauty and grocery lists are excluded wholesale.
  hazmat       lighter gas, butane, aerosols, flammable liquids: FBA dangerous-goods approval and courier limits.
               (v1.49 flagged a toy "soft bomb" and dog ear-muffs as explosives while butane cans passed.)
  explosives   firecrackers — never.
  digital      bill payments, vouchers, gift cards, bookings (v1.51) — nothing to source, post or earn commission on.

Affiliate posts are NOT affected: a brand is exactly what people want to click.

This is a coarse, curated filter, not a legal opinion. Extend it with BR_BRANDS_EXTRA (comma list) and relax
it per category with BR_RESELL_ALLOW_CATS; BR_RESELL_GATE=0 turns it off.
"""
import os, re, unicodedata

ENABLED = (os.environ.get("BR_RESELL_GATE") or "1") != "0"
FORMULATED_CATS = {"beauty", "grocery"} - {c.strip() for c in (os.environ.get("BR_RESELL_ALLOW_CATS") or "").split(",")}

_BRANDS = """
nivea|dove|ponds|pond's|lakme|maybelline|loreal|l'oreal|garnier|himalaya|mamaearth|wow skin science|cetaphil|neutrogena|
biotique|dot & key|minimalist|bare anatomy|bombay shaving company|gillette|whisper|stayfree|pampers|huggies|johnson's|
sebamed|mee mee|chicco|nestle|cadbury|amul|tata|fortune|aashirvaad|maggi|kellogg's|muscleblaze|optimum nutrition|healthkart|
dabur|patanjali|zandu|colgate|pepsodent|sensodyne|oral-b|head & shoulders|pantene|sunsilk|tresemme|clinic plus|indulekha|
vaseline|parachute|parachute advansed|fiama|lifebuoy|cinthol|medimix|santoor|savlon|vicks|revital|aveeno|olay|
scotch-brite|scotchbrite|vim|harpic|lizol|colin|dettol|mortein|good knight|odonil|surf excel|ariel|domex|3m|
safari|skybags|american tourister|vip|aristocrat|nasher miles|mokobara|wildcraft|samsonite|tommy hilfiger|lavie|caprese|baggit|
hrx|fastrack|titan|sonata|casio|fossil|boat|fire-boltt|realme|redmi|xiaomi|mi|oneplus|samsung|apple|sony|jbl|bose|
philips|panasonic|lg|whirlpool|havells|syska|crompton|bajaj|usha|orient|prestige|pigeon|butterfly|hawkins|milton|cello|borosil|
tupperware|wonderchef|kent|eureka forbes|aquaguard|godrej|morphy richards|inalsa|hp|dell|lenovo|asus|acer|logitech|zebronics|
portronics|ambrane|anker|sandisk|seagate|wd|tp-link|d-link|canon|epson|brother|
nivia|yonex|cosco|decathlon|nike|adidas|puma|reebok|skechers|asics|under armour|wilson|spalding|sg|ss|mrf|levi's|wrangler|
lego|funskool|hot wheels|barbie|mattel|hasbro|fisher-price|nerf|play-doh|disney|hamleys|
pedigree|whiskas|royal canin|drools|purina|sheba|meat up|farmina|hill's|heads up for tails|me-o|
amazon basics|amazon brand|solimo|kindle|
"""
BRANDS = {b.strip() for b in _BRANDS.replace("\n", "").split("|") if b.strip()}
BRANDS |= {b.strip().lower() for b in (os.environ.get("BR_BRANDS_EXTRA") or "").split(",") if b.strip()}


def _toks(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", s)


_BY_FIRST = {}
for _b in BRANDS:
    _t = _toks(_b)
    if _t:
        _BY_FIRST.setdefault(_t[0], []).append(_t)
for _l in _BY_FIRST.values():
    _l.sort(key=len, reverse=True)          # longest brand first ("american tourister" before "american")

_LEAD = {"new", "the", "original", "genuine"}


def brand_of(title):
    """→ the matched big-brand name ('' if none). A brand counts only at the START of the title
    (Amazon titles lead with the brand), so 'dove-tail saw' or 'simple wall hook' do not match by accident."""
    t = _toks(title)
    if t and t[0] in _LEAD:
        t = t[1:]
    for cand in _BY_FIRST.get(t[0] if t else "", []):
        if t[:len(cand)] == cand:
            if len(cand) == 1 and re.match(r"^\W*(?:%s\W+)?%s-[a-z]" % ("|".join(_LEAD), re.escape(cand[0])),
                                           unicodedata.normalize("NFKD", str(title)).encode("ascii", "ignore").decode().lower()):
                return ""                       # 'Dove-tail Hand Saw': the brand word is part of a longer word
            return " ".join(cand)
    return ""


_FORMULATED = re.compile(
    r"\b(serum|face ?cream|body ?lotion|lotion|moisturi[sz]er|shampoo|(?<!air )conditioner|sunscreen|spf|face ?wash|lipstick|lip balm|"
    r"kajal|perfume|deodorant|deo|roll[- ]?on|body ?wash|bath bar|soap(?!\s*(?:dispenser|dish|holder|case|stand|box|pump))|"
    r"toothpaste|hair ?oil|hair colou?r|"
    r"(?:protein|whey)(?!\s+(?:shaker|bottle|mixer|container|box|tub))|supplement|vitamin|capsules?|gummies|creatine|collagen|"
    r"multivitamin|"
    r"dog food|cat food|pet food|puppy food|kitten food|pet treats?|dog treats?|cat treats?|"
    r"masala|ghee|honey)\b", re.I)

_HAZMAT = re.compile(
    r"\b(butane|lighter (?:gas|fluid|refill)|gas refill|gas (?:can|cartridge|canister)|matchstick lighter|kerosene|petrol|"
    r"aerosol|paint thinner|thinner|acetone|nail polish remover|spray paint|fire ?starter|"
    r"lpg|camping gas|fuel (?:cartridge|can))\b", re.I)

from compliance import explosives_in_head, non_product      # one rule each, owned by compliance.py


def check(title, category_slug=""):
    """→ {'ok': bool, 'kind': '' | brand | formulated | hazmat | explosives, 'reason': str, 'brand': str}.
    ok=True means 'nothing here stops it being a reselling candidate' — not a sourcing or compliance clearance."""
    if not ENABLED:
        return {"ok": True, "kind": "", "reason": "", "brand": ""}
    title, cat = str(title or ""), str(category_slug or "").strip()
    np_ = non_product(title)
    if np_:
        return {"ok": False, "kind": "digital", "reason": np_, "brand": ""}
    if explosives_in_head(title):
        return {"ok": False, "kind": "explosives", "reason": "firecracker / explosive — never", "brand": ""}
    if _HAZMAT.search(title):
        return {"ok": False, "kind": "hazmat", "brand": "",
                "reason": "dangerous goods (flammable / pressurised) — FBA hazmat approval and courier limits; affiliate only"}
    if cat in FORMULATED_CATS or _FORMULATED.search(title):
        return {"ok": False, "kind": "formulated", "brand": "",
                "reason": "formulated product (cosmetic / food / health) — needs a licensed manufacturer, "
                          "IndiaMART generics will not match; affiliate only"}
    b = brand_of(title)
    if b:
        return {"ok": False, "kind": "brand", "brand": b,
                "reason": f"big brand ({b.title()}) — buyers want the name, a generic cannot capture that demand; affiliate only"}
    return {"ok": True, "kind": "", "reason": "", "brand": ""}


def split(titles_cats):
    """[(title, category)] → (ok_count, {kind: count}) for the digest."""
    kinds = {}
    ok = 0
    for t, c in titles_cats:
        r = check(t, c)
        if r["ok"]:
            ok += 1
        else:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return ok, kinds
