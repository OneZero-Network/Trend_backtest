"""v1.27 drip posting: pool built once per data day, spread over the day's slots, no repeats, forward test once."""
import os, sys, shutil, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

TMP = tempfile.mkdtemp()
tf.LOCAL_OUT_DIR = TMP
D0 = datetime(2026, 11, 1)
CATS = ["kitchen", "garden", "sports", "luggage", "pet-supplies", "home-improvement"]
days = [(D0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(10)]
H = []
for i, d in enumerate(days):
    for c in CATS:
        for k in range(1, 101):
            H.append({"date": d, "list": "bestsellers", "category_slug": c, "asin": f"{c[:1].upper()}{k:08d}",
                      "rank": k, "price_inr": "199", "rating": "", "reviews": "", "title": f"{c} item {k}"})
pd.DataFrame(H).to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
sig = pd.DataFrame([{"asin": f"{c[:1].upper()}{k:08d}", "title": f"{c} item {k}", "category_slug": c,
                     "trend_score": 100 - k - j * 0.1, "rank_change": 30, "price_inr": "199", "seasonal": "",
                     "compliance": "", "actionable": "True"} for k in range(1, 8) for j, c in enumerate(CATS)])
sig.to_csv(os.path.join(TMP, "TREND_SIGNALS.csv"), index=False)

sent = []
tf.send_telegram = lambda m: sent.append((tf.TELEGRAM_CHAT_ID, tf.TELEGRAM_BOT_TOKEN, m))
tf.TELEGRAM_CHAT_ID, tf.TELEGRAM_BOT_TOKEN = "ADMIN", "admintok"
tf.AFFILIATE_CHAT_ID, tf.AFFILIATE_BOT_TOKEN, tf.AFFILIATE_TAG = "@onezerodeals", "pubtok", "onezeronetwor-21"
tf.AFFILIATE_GROUPS, tf.AFFILIATE_SHEET_ID = "", ""
tf.BR_POOL_SIZE, tf.DRIP_SLOTS_IST = 12, [9, 11, 13, 15, 17, 19, 21]
IST = timezone(timedelta(hours=5, minutes=30))

def run_at(day, hour):
    tf.NOW_IST = datetime.strptime(day, "%Y-%m-%d").replace(hour=hour, minute=35, tzinfo=IST)
    tf.TODAY = day
    tf.main_drip()

per_slot = []
for h in tf.DRIP_SLOTS_IST:
    before = len([1 for c, *_ in sent if c == "@onezerodeals"])
    n_log = len(pd.read_csv(os.path.join(TMP, "AFF_POST_LOG.csv"))) if os.path.exists(os.path.join(TMP, "AFF_POST_LOG.csv")) else 0
    run_at("2026-11-11", h)
    n_after = len(pd.read_csv(os.path.join(TMP, "AFF_POST_LOG.csv")))
    per_slot.append(n_after - n_log)
check("all 12 pool picks posted over 7 slots, 1–2 per slot", sum(per_slot) == 12 and all(1 <= x <= 2 for x in per_slot), per_slot)
pub = [(t, m) for c, t, m in sent if c == "@onezerodeals"]
check("one public message per slot, via the PUBLIC bot", len(pub) == 7 and all(t == "pubtok" for t, _ in pub), len(pub))
check("public messages carry the tag and no price", all("tag=onezeronetwor-21" in m and "₹" not in m for _, m in pub))
check("admin gets the day's pool list once", sum("Today's post pool" in m for c, _, m in sent if c == "ADMIN") == 1)
log_df = pd.read_csv(os.path.join(TMP, "AFF_POST_LOG.csv"))
check("no product posted twice in a day", log_df["asin"].is_unique)
check("best picks first (morning gets the top scores)", log_df["asin"].iloc[0] == "K00000001", log_df["asin"].iloc[0])
pk = pd.read_csv(os.path.join(TMP, "PICKS.csv"))
check("forward test recorded once for the day (12 picks + baselines)", (pk["role"] == "pick").sum() == 12, (pk["role"] == "pick").sum())
run_at("2026-11-11", 21)
check("extra run after the pool is done posts nothing", len([1 for c, *_ in sent if c == "@onezerodeals"]) == 7)

# next data day: yesterday's posted products are not repeated
H2 = pd.DataFrame(H)
H2 = pd.concat([H2, H2[H2["date"] == days[-1]].assign(date="2026-11-11")])
H2.to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
run_at("2026-11-12", 9)
pool = pd.read_csv(os.path.join(TMP, "AFF_POOL.csv"), dtype=str)
p2 = set(pool.loc[pool["date"] == "2026-11-11", "asin"]); p1 = set(log_df["asin"])
check("next day's pool excludes products posted in the last 7 days", p2 and not (p1 & p2), sorted(p1 & p2)[:3])

# no public chat → nothing posted, admin told
sent.clear(); tf.AFFILIATE_CHAT_ID = ""
run_at("2026-11-12", 11)
check("no public chat → admin warned, nothing public", sent and all(c == "ADMIN" for c, *_ in sent))

shutil.rmtree(TMP)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
