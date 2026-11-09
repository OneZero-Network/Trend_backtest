#!/usr/bin/env python3
"""Offline tests: affiliate picks, post text, baseline sampling, forward-test tracking."""
import os, sys, shutil, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update({"BR_OUTPUT_DIR": TMP, "BR_CATEGORIES": "kitchen,toys"})
import pandas as pd
import picks as P, trend_fetcher as tf
_orig_send = tf.send_telegram
ok = True
def check(n, c, d=""):
    global ok; ok &= bool(c); print(f"{'PASS' if c else 'FAIL'}  {n}  {d}")

D0 = datetime(2026, 11, 1)
day = lambda i: (D0 + timedelta(days=i)).strftime("%Y-%m-%d")
# history: 30 days, 2 categories × 20 products; P1 climbs, B* random
rows = []
for i in range(30):
    for cat in ("kitchen", "toys"):
        for j in range(20):
            a = f"{cat[:3].upper()}{j:07d}"
            rank = j + 1
            if a == "KIT0000010":
                rank = max(1, 11 - i)                    # climbs → should be a hit
            if a == "KIT0000015" and i >= 5:
                continue                                  # drops out of top-100 → miss
            rows.append((day(i), "bestsellers", cat, a, rank))
H = pd.DataFrame(rows, columns=["date", "list", "category_slug", "asin", "rank"])

sig = pd.DataFrame([
    {"asin": "KIT0000010", "title": "Steel Lunch Box 3 Compartment", "category_slug": "kitchen", "trend_score": 90, "actionable": "True", "discount_driven": "False", "short_dip": "False", "seasonal": ""},
    {"asin": "KIT0000015", "title": "Glass Jar Set", "category_slug": "kitchen", "trend_score": 85, "actionable": "True", "discount_driven": "False", "short_dip": "False", "seasonal": ""},
    {"asin": "KIT0000016", "title": "Silicone Spatula", "category_slug": "kitchen", "trend_score": 84, "actionable": "True", "discount_driven": "False", "short_dip": "False", "seasonal": ""},
    {"asin": "KIT0000017", "title": "Flash Sale Kettle", "category_slug": "kitchen", "trend_score": 99, "actionable": "True", "discount_driven": "True", "short_dip": "False", "seasonal": ""},
    {"asin": "TOY0000003", "title": "Diwali Rangoli Kit for Kids", "category_slug": "toys", "trend_score": 80, "actionable": "True", "discount_driven": "False", "short_dip": "False", "seasonal": "SEASONAL: Diwali"},
    {"asin": "TOY0000004", "title": "Quarantined Puzzle", "category_slug": "toys", "trend_score": 98, "actionable": "False", "discount_driven": "False", "short_dip": "True", "seasonal": ""},
])
ch = P.select_picks(sig, n=8)
check("discount-driven and quarantined products excluded", not ({"KIT0000017", "TOY0000004"} & set(ch["asin"])))
check("max 2 per category", (ch["category_slug"] == "kitchen").sum() == 2, str(list(ch["asin"])))

txt = P.post_text(ch, "mytag-21", "week of 2026-11-01")
check("links carry the Associates tag", "https://www.amazon.in/dp/KIT0000010?tag=mytag-21" in txt)
check("post contains NO prices", "₹" not in txt and "Rs" not in txt)
check("disclosure always present", P.DISCLOSURE in txt)
check("seasonal picks labelled", "(Diwali)" in txt)

# ── one message per product
ch2 = sig[sig.asin.isin(["KIT0000010", "TOY0000003"])].copy()
ch2.loc[ch2.asin == "KIT0000010", "title"] = "Steel Lunch Box & Bag <3 Compartment>"
M = P.post_messages(ch2, H, "mytag-21", day(8), cat_names={"kitchen": "Home & Kitchen", "toys": "Toys & Games"},
                    week_label="week of " + day(8))
check("header + one message per product", len(M) == 3, f"{len(M)} messages")
check("biggest climber first, with 'Climbed #X → #Y' from our history",
      "Climbed <b>#10 → #3</b> in Home &amp; Kitchen" in M[1], M[1].split("\n\n")[1])
check("days-on-list line", "on the list 7 of the last 7 days" in M[1])
check("each product message: tagged link + #ad, no prices",
      all("?tag=mytag-21" in m and "#ad" in m and "₹" not in m for m in M[1:]))
check("full disclosure in header", P.DISCLOSURE in M[0])
check("title HTML-escaped (& < > can't break Telegram formatting)", "&amp;" in M[1] and "&lt;3" in M[1])
check("seasonal line on seasonal pick", "📅 Seasonal: Diwali" in M[2])
newin = P.trend_facts("KIT0000010", "kitchen", H, day(0))
check("first day in list → 'New in the top 100' wording", "New in the" in P.trend_line(newin, "Home & Kitchen"))

# ── share kit (X + WhatsApp)
from urllib.parse import unquote
long = ch2.copy(); long.loc[long.asin == "KIT0000010", "title"] = "Milton " + "Super Insulated Steel Lunch Box " * 12
K = P.share_kit(long, H, "mytag-21", day(8), {"kitchen": "Home & Kitchen", "toys": "Toys & Games"})
check("X text ≤ 280 even with a very long title (links count 23)", all(P.x_weight(k["x_text"]) <= 280 for k in K),
      str([P.x_weight(k["x_text"]) for k in K]))
check("X text keeps the reason, tagged link and #ad", all("📈" in k["x_text"] and "?tag=mytag-21" in k["x_text"]
      and "#ad" in k["x_text"] for k in K))
check("WhatsApp text: *bold* title, link, #ad", K[0]["wa_text"].startswith("*") and "?tag=mytag-21" in K[0]["wa_text"]
      and "#ad" in K[0]["wa_text"])
check("one-tap links decode back to exactly the post", unquote(K[0]["x_intent"].split("text=", 1)[1]) == K[0]["x_text"]
      and unquote(K[0]["wa_share"].split("text=", 1)[1]) == K[0]["wa_text"])
check("no prices in X or WhatsApp text", all("₹" not in k["x_text"] + k["wa_text"] for k in K))
class FakeX:
    def __init__(s, fail_at=None): s.n, s.fail_at = 0, fail_at
    def create_tweet(s, text):
        s.n += 1
        if s.fail_at and s.n >= s.fail_at:
            raise Exception("402 Payment Required: add credits")
        return type("R", (), {"data": {"id": f"t{s.n}"}})()
kit8 = K * 4
fx = FakeX(); res = P.post_to_x(kit8, {}, 3, client=fx)
check("X auto-post respects the spend cap", fx.n == 3 and all(t.startswith("t") for _, t in res))
fx = FakeX(fail_at=2); res = P.post_to_x(kit8, {}, 8, client=fx)
check("X billing error (402) stops posting immediately", fx.n == 2 and res[-1][1].startswith("ERROR"), str(res))

R = P.make_rows(ch, H, day(0))
picks_r, base_r = R[R.role == "pick"], R[R.role == "baseline"]
check("3 random baselines per pick", len(base_r) == 3 * len(picks_r), f"{len(picks_r)} picks, {len(base_r)} baselines")
same_cat = all(set(base_r[base_r.category_slug == c].asin).issubset(set(H[H.category_slug == c].asin)) for c in base_r.category_slug.unique())
check("baselines from the same category, never a pick", same_cat and not (set(base_r.asin) & set(picks_r.asin)))
check("baseline draw is deterministic per day", P.make_rows(ch, H, day(0)).equals(R))

T = P.update_tracking(R.astype(str), H[H.date <= day(16)])        # 16 days observed so far
g = T.set_index("asin")
check("climber is a 7d and 14d hit", g.loc["KIT0000010", "hit_d7"] == 1 and g.loc["KIT0000010", "hit_d14"] == 1)
check("product that left the top-100 = miss ('out')", g.loc["KIT0000015", "rank_d7"] == "out" and g.loc["KIT0000015", "hit_d7"] == 0)
check("28-day horizon not observed yet → pending (not a miss)", all(str(x) in ("", "nan", "None") for x in T["hit_d28"]))
Hgap = H[~((H.date == day(7)) & (H.category_slug == "kitchen"))]
Tg = P.update_tracking(R.astype(str), Hgap[Hgap.date <= day(16)]).set_index("asin")
check("category not fetched on horizon day → pending, not miss", str(Tg.loc["KIT0000010", "hit_d7"]) in ("", "nan", "None"))

sc = P.scorecard(T)
check("scorecard has pick + baseline rows per horizon", set(sc.role) == {"pick", "baseline"} and set(sc.horizon_days) == {7, 14, 28})
check("verdict refuses to judge with few resolved picks", "too early" in P.verdict(sc), P.verdict(sc))
big = pd.DataFrame([{"horizon_days": 14, "role": "pick", "resolved": 60, "hits": 36, "hit_rate": .6},
                    {"horizon_days": 14, "role": "baseline", "resolved": 180, "hits": 54, "hit_rate": .3}])
check("verdict reports lift vs random when enough data", "2.00×" in P.verdict(big) and "beat random" in P.verdict(big), P.verdict(big))

# ── end to end (local files)
H.to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
sig.to_csv(os.path.join(TMP, "TREND_SIGNALS.csv"), index=False)
tf.LOCAL_OUT_DIR = TMP; tf.AFFILIATE_POST_GAP_SEC = 0; tf.TODAY = day(29); tf.AFFILIATE_TAG = ""; tf.AFFILIATE_CHAT_ID = ""
msgs = []; tf.send_telegram = msgs.append
tf.main_affiliate()
check("no channel set → DRAFT preview sent to your private chat, one message per product",
      any("DRAFT" in m for m in msgs) and sum("Amazon Associate link" in m and "wa.me" not in m for m in msgs) == 2)
# v1.18: run date is 30 Nov — after Diwali — so the Diwali kit is held back and the reason reported
check("post-festival seasonal pick held back and explained", any("Not picked" in m and "Diwali" in m for m in msgs))
check("missing tag warned (links would earn nothing)", any("AFFILIATE_TAG not set" in m for m in msgs))
pk = pd.read_csv(os.path.join(TMP, "PICKS.csv"))
check("picks + baselines recorded with pick date", (pk.pick_date == day(29)).all() and set(pk.role) == {"pick", "baseline"})
n1 = len(pk); msgs.clear(); tf.main_affiliate()
check("second run same date does not duplicate picks", len(pd.read_csv(os.path.join(TMP, "PICKS.csv"))) == n1)
H.iloc[0:0].to_csv(os.path.join(TMP, "HISTORY.csv"), index=False); msgs.clear(); tf.main_affiliate()
check("refuses to pick with < 7 days of history", any("no picks yet" in m for m in msgs))
# ── two chats: admin gets everything internal, public gets only product posts
H.to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
os.remove(os.path.join(TMP, "PICKS.csv"))
# v1.18: 30 Nov run date → Diwali kit held back (season window) → 2 picks, not 3
sent = []
class _R:
    status_code, text = 200, "ok"
def fake_post(url, data=None, timeout=None):
    sent.append((data["chat_id"], data["text"])); return _R()
import requests as _rq
_orig = _rq.post; _rq.post = fake_post
tf.send_telegram = _orig_send
tf.TELEGRAM_BOT_TOKEN, tf.TELEGRAM_CHAT_ID = "tok", "ADMIN"
tf.AFFILIATE_CHAT_ID, tf.AFFILIATE_TAG = "PUBLIC", "mytag-21"
tf.main_affiliate()
pub = [t for c, t in sent if c == "PUBLIC"]; adm = [t for c, t in sent if c == "ADMIN"]
check("public channel gets header + one message per product only", len(pub) == 1 + 2 and all("#ad" in t for t in pub), f"{len(pub)} public msgs")
check("public channel never sees scorecard/admin text", not any("Forward test" in t or "Posted" in t or "⚠" in t for t in pub))
check("share kit (one-tap X/WhatsApp buttons) goes to ADMIN only",
      any("Share kit" in t for t in adm) and sum("wa.me/?text=" in t for t in adm) == 2
      and not any("Share kit" in t or "wa.me" in t for t in pub))
check("admin chat gets the confirmation + forward-test line", any("Posted 2 picks" in t for t in adm))
check("after posting, admin chat id is restored", tf.TELEGRAM_CHAT_ID == "ADMIN")
sent.clear(); os.remove(os.path.join(TMP, "PICKS.csv"))
tf.AFFILIATE_CHAT_ID = "ADMIN"
tf.main_affiliate()
check("same ID in both secrets → refuses to post publicly, warns admin",
      all(c == "ADMIN" for c, _ in sent) and any("SAME as TELEGRAM_CHAT_ID" in t for _, t in sent))
_rq.post = _orig

shutil.rmtree(TMP)
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
