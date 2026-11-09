"""
Festival / season calendar — turns a "SEASONAL: Diwali" flag into a time decision.

Why: in week 1 the picks were posted ON Diwali day for Diwali goods. Our own data showed
Diwali-type products peaking ~4 days BEFORE Diwali (49 in top lists on 4–5 Nov, 32 by 8 Nov).
So a seasonal product is only worth promoting/stocking inside a window before the event.

Lunar festivals move every year — dates below are for 2026–27 (sources: hindupad.com,
shiphub.co). ADD NEXT YEAR'S DATES each October; an event with no future date is treated
as "off-season / date unknown" (never silently allowed).
"""
from datetime import date, timedelta

FESTIVALS = {
    "Diwali":              ["2026-11-08", "2027-10-29"],
    "Festive decor":       ["2026-11-08", "2026-12-25", "2027-10-29", "2027-12-25"],
    "Christmas/New Year":  ["2026-12-25", "2027-12-25"],
    "Halloween":           ["2026-10-31", "2027-10-31"],
    "Valentine's":         ["2027-02-14"],
    "Holi":                ["2027-03-22"],
    "Eid":                 ["2027-03-10"],          # tentative — moon sighting
    "Rakhi":               ["2027-08-17"],
    "Ganesh Chaturthi":    ["2027-09-04"],
    "Navratri/Durga Puja": ["2027-09-30"],
    "Karwa Chauth":        ["2027-10-18"],
}
SEASONS = {"Monsoon": (6, 9), "Summer": (3, 6), "Winter": (11, 2)}   # (first month, last month)

AFF_MIN_LEAD, AFF_MAX_LEAD = 3, 30      # affiliate: promote 30→3 days before (peak seen ~4 days before)
STOCK_MIN_LEAD = 45                     # bulk stock: quote→sample→bulk→inbound needs ~6 weeks
STOCK_MAX_LEAD = 120                    # earlier than this = capital locked for months


def _d(x):
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _in_season(m, first, last):
    return first <= m <= last if first <= last else (m >= first or m <= last)


def window(seasonal_flag, today):
    """→ dict(event, days_to, affiliate_ok, stock_ok, note) for the most binding event in the flag.
    Not seasonal → affiliate_ok & stock_ok True."""
    t = _d(today)
    raw = "" if seasonal_flag is None or str(seasonal_flag).strip().lower() in ("nan", "none") else str(seasonal_flag)
    names = [n.strip() for n in raw.replace("SEASONAL:", "").split(",") if n.strip()]
    if not names:
        return {"event": "", "days_to": None, "affiliate_ok": True, "stock_ok": True, "note": ""}
    worst = None
    for n in names:
        if n in SEASONS:
            first, last = SEASONS[n]
            ins = _in_season(t.month, first, last)
            last_month = t.month == last
            r = {"event": n, "days_to": None, "affiliate_ok": ins,
                 "stock_ok": ins and not last_month and _in_season((t + timedelta(days=STOCK_MIN_LEAD)).month, first, last),
                 "note": (f"{n} season" + ("" if ins else " — off-season") + (" (last month)" if last_month else ""))}
        else:
            fut = sorted(_d(x) for x in FESTIVALS.get(n, []) if _d(x) >= t)
            if not fut:
                r = {"event": n, "days_to": None, "affiliate_ok": False, "stock_ok": False,
                     "note": f"{n}: no future date in calendar — add it in season.py"}
            else:
                dt = (fut[0] - t).days
                aff = AFF_MIN_LEAD <= dt <= AFF_MAX_LEAD
                stock = STOCK_MIN_LEAD <= dt <= STOCK_MAX_LEAD
                if dt < AFF_MIN_LEAD:
                    note = f"{n} in {dt}d — too late (demand peaks before the day)"
                elif aff:
                    note = f"{n} in {dt}d — promote now; too late to stock" if not stock else f"{n} in {dt}d"
                else:
                    note = (f"{n} on {fut[0]} ({dt}d) — " +
                            (f"stock window open; promote from {fut[0] - timedelta(days=AFF_MAX_LEAD)}" if stock
                             else f"too early; stock window opens {fut[0] - timedelta(days=STOCK_MAX_LEAD)}"))
                r = {"event": n, "days_to": dt, "affiliate_ok": aff, "stock_ok": stock, "note": note}
        # most restrictive wins
        if worst is None or (r["affiliate_ok"], r["stock_ok"]) < (worst["affiliate_ok"], worst["stock_ok"]):
            worst = r
    return worst
