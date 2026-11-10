"""v1.30 independent discovery: phrase cleaning, link resolving, creator counting, agreement, seeds, lead test."""
import os, sys, shutil, tempfile
from datetime import datetime, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import discovery as D

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

# ── phrases
check("shop/budget words stripped", D.clean_phrase("air fryer flipkart under 3000") == "air fryer", D.clean_phrase("air fryer flipkart under 3000"))
check("gift-for words stripped", D.clean_phrase("gift for husband smart watch") == "gift smart watch", D.clean_phrase("gift for husband smart watch"))
check("news / service searches rejected", not D.is_product_phrase("flipkart big billion day sale date")
      and not D.is_product_phrase("amazon customer care number") and not D.is_product_phrase("ipl score today"))
check("product searches kept", D.is_product_phrase("hot lunch box for office") and D.is_product_phrase("meesho kurti set"))
check("Breakout counts as a big rise", D.rise_value("Breakout") >= 5000 and D.rise_value("+350%") == 350)
rows = D.trends_rows([("air fryer flipkart", "Breakout"), ("air fryer login", 900), ("air fryer basket", 100)], "air fryer", "2026-11-10")
check("trends rows: weak rises and non-products dropped", [r["phrase"] for r in rows] == ["air fryer"], rows)

# ── links: only the redirect ADDRESS is read
REDIRECTS = {"https://amzn.to/abc": "https://www.amazon.in/Prestige-Electric-Kettle-1-5L/dp/B01MQZ7J8K?tag=someone-21",
             "https://fkrt.it/xyz": "https://dl.flipkart.com/s/ZZ",
             "https://dl.flipkart.com/s/ZZ": "https://www.flipkart.com/boat-airdopes-141/p/itm123abc?pid=ACCG6ZNDHFGSGZQB"}
calls = []
def head(u):
    calls.append(u); return REDIRECTS.get(u)
p = D.resolve("https://amzn.to/abc", head)
check("amzn.to → ASIN + name from the address", p == ("amazon", "B01MQZ7J8K", "Prestige Electric Kettle 1 5L"), p)
p = D.resolve("https://fkrt.it/xyz", head)
check("fkrt.it → 2 hops → Flipkart pid + name", p == ("flipkart", "ACCG6ZNDHFGSGZQB", "boat airdopes 141"), p)
check("full product links need no request", D.resolve("https://www.amazon.in/dp/B0TEST1234", head) == ("amazon", "B0TEST1234", "") and
      "https://www.amazon.in/dp/B0TEST1234" not in calls)
check("non-shop links ignored", D.find_links("Follow me https://instagram.com/x and https://amzn.to/abc.") == ["https://amzn.to/abc"])

# ── creators: same product from 3 different channels counts 3; same channel twice counts 1
now = datetime(2026, 11, 10, tzinfo=timezone.utc)
vids = [{"id": f"v{i}", "channel": ch, "title": "Amazon finds", "description": "Kettle: https://amzn.to/abc",
         "views": 30000, "published": "2026-11-07T10:00:00Z"} for i, ch in enumerate(["c1", "c2", "c3", "c3"])]
vids.append({"id": "v9", "channel": "c4", "title": "haul", "description": "earbuds https://fkrt.it/xyz", "views": 900,
             "published": "2026-11-08T10:00:00Z"})
yt = D.youtube_rows(vids, head, "2026-11-10", now=now)
check("creators counted per channel, not per video", yt[0]["product_id"] == "B01MQZ7J8K" and yt[0]["creators"] == 3, yt[:1])
check("single-creator product kept but ranked lower", yt[1]["creators"] == 1)

# ── combine + agreement
tr = [{"date": "2026-11-10", "source": "gtrends", "phrase": "electric kettle", "query": "electric kettle flipkart", "seed": "flipkart", "rise": 400},
      {"date": "2026-11-10", "source": "gtrends", "phrase": "hot lunch box", "query": "hot lunch box for office", "seed": "under 1000", "rise": 900}]
c = D.combine(tr, yt, ["Milton Hot Lunch Box Steel 3 Containers"], [], "2026-11-10")
by = {r["item_key"]: r for r in c}
check("kettle: Trends + 3 creators agree → 2+ sources", by["q:electric kettle"]["status"] == "2+ sources"
      and by["q:electric kettle"]["sources"] == "gtrends,youtube", by["q:electric kettle"])
check("YouTube product itself: youtube source", "youtube" in by["am:B01MQZ7J8K"]["sources"])
check("lunch box: Trends + already on Amazon list, but day 1 → still watch (needs day 2)",
      by["q:hot lunch box"]["sources"] == "amazon_list,gtrends" and by["q:hot lunch box"]["status"] == "watch", by["q:hot lunch box"])
c2 = D.combine(tr, [], ["Milton Hot Lunch Box Steel 3 Containers"], c, "2026-11-11")
b2 = {r["item_key"]: r for r in c2}
check("day 2 → lunch box promoted; first_seen kept", b2["q:hot lunch box"]["status"] == "2+ sources"
      and b2["q:hot lunch box"]["first_seen"] == "2026-11-10" and b2["q:hot lunch box"]["days_seen"] == 2)

# ── seeds grow and expire
s1 = D.update_seeds([], [{"phrase": "air fryer", "rise": 5000}, {"phrase": "mat", "rise": 5000}], "2026-11-10")
check("strong 2-word phrase becomes a seed; 1-word doesn't", [r["seed"] for r in s1] == ["air fryer"], s1)
check("active for 7 days", "air fryer" in D.active_seeds(s1, "2026-11-16", base=["x"]) and "air fryer" not in D.active_seeds(s1, "2026-11-19", base=["x"]))
check("growing seeds go first, then today's slice", D.active_seeds(s1, "2026-11-11", base=["lunch box", "mop"]) == ["air fryer", "lunch box", "mop"])

# ── lead test: found 10 Nov (not on Amazon), appears on Amazon 15 Nov → 5-day lead
disc = [{"date": "2026-11-10", "item_key": "q:neck fan", "kind": "phrase", "name": "neck fan", "product_id": "", "sources": "gtrends"},
        {"date": "2026-11-10", "item_key": "q:hot lunch box", "kind": "phrase", "name": "hot lunch box", "product_id": "", "sources": "amazon_list,gtrends"},
        {"date": "2026-11-10", "item_key": "q:sand timer", "kind": "phrase", "name": "sand timer", "product_id": "", "sources": "gtrends"}]
bsr = [{"date": f"2026-11-{d:02d}", "asin": "X", "title": "Steel bottle"} for d in range(10, 26)]
bsr.append({"date": "2026-11-15", "asin": "N1", "title": "Portable Neck Fan USB Rechargeable"})
lt = D.lead_test(disc, bsr)
check("lead test: already-on-Amazon items excluded; neck fan led by 5 days; sand timer missed",
      lt == {"eligible": 2, "appeared": 1, "rate": 0.5, "median_lead_days": 5}, lt)
check("lead test waits until the 14-day window is over", D.lead_test(disc, bsr[:5])["eligible"] == 0)

# ── v1.31 lanes
check("skills: course/language words stripped", D.clean_phrase("excel course in hindi free", "skills") == "excel")
check("apps: app words stripped", D.clean_phrase("best ai video editor app for android", "apps") == "ai video editor")
check("banned everywhere: betting, real-money games, loan apps, trading, piracy",
      not any(D.is_product_phrase(q, ln) for q, ln in [("rummy app", "apps"), ("instant loan app", "apps"),
              ("stock trading course", "skills"), ("capcut pro mod apk", "apps"), ("dream11 team", "products"),
              ("aviator game", "apps")]))
check("exam results / jobs aren't skills", not D.is_product_phrase("ssc cgl result", "skills")
      and not D.is_product_phrase("data entry job", "skills") and D.is_product_phrase("python course", "skills"))
sk = D.trends_rows([("python course for beginners", "Breakout")], "python", "2026-11-10", lane="skills") + \
     D.trends_rows([("power bi course", 600)], "power bi", "2026-11-10", lane="skills")
check("skill rows carry their lane", [(r["phrase"], r["lane"]) for r in sk] == [("python", "skills"), ("power bi", "skills")], sk)
ytp = D.yt_phrase_check({"python": [{"channel": c, "published": "2026-11-01T00:00:00Z", "views": 9000} for c in ("a", "b", "c")],
                         "power bi": [{"channel": "a", "published": "2026-11-01T00:00:00Z", "views": 9000}]}, now=now)
check("YouTube check counts channels with real views", ytp["python"][0] == 3 and ytp["power bi"][0] == 1, ytp)
cs = D.combine(sk, [], [], [], "2026-11-10", yt_phrase={("skills", k): v for k, v in ytp.items()})
bs_ = {r["item_key"]: r for r in cs}
check("skill confirmed by Trends + 3 YouTube channels → 2+ sources; own key prefix",
      bs_["sk:python"]["status"] == "2+ sources" and bs_["sk:power bi"]["status"] == "watch", bs_)
check("skills never matched against Amazon lists", all("amazon_list" not in r["sources"] for r in cs))
seeds_all = D.update_seeds([], [{"phrase": "power bi", "rise": 5000, "lane": "skills"},
                                {"phrase": "air fryer", "rise": 5000}], "2026-11-10")
check("growing seeds stay in their own lane", "power bi" in D.active_seeds(seeds_all, "2026-11-11", ["excel"], "skills")
      and "power bi" not in D.active_seeds(seeds_all, "2026-11-11"))

# ── v1.33: the 10 Nov noise must be rejected; the 10 Nov real products kept
noise = [("jailer 2 prime release date", "amazon", "products"), ("iphone 17 pro diwali", "flipkart", "products"),
         ("namjooning stanford", "course", "skills"), ("mehul sir tuition ahmedabad", "classes", "skills"),
         ("jobs hunt stripe engineer", "software", "apps")]
check("10 Nov noise rejected (not about its seed, or film/job/news)",
      not any(D.trends_rows([(q, 5000)], sd, "2026-11-10", lane=ln) for q, sd, ln in noise),
      [q for q, sd, ln in noise if D.trends_rows([(q, 5000)], sd, "2026-11-10", lane=ln)])
cel = D.combine(D.trends_rows([("cellebrite", 650)], "password manager", "2026-11-10", lane="apps"), [], [], [], "2026-11-10")
check("trade-off: a short brand name like 'cellebrite' passes Trends, but stays 'watch' without YouTube confirmation",
      cel and cel[0]["status"] == "watch", cel)
real = [("hot lunch box for office", "lunch box", "products"), ("self heating lunch box", "lunch box", "products"),
        ("folding storage box", "storage box", "products"), ("excel course in hindi", "excel", "skills"),
        ("capcut alternative", "video editor", "apps"), ("ai video editor free", "video editor", "apps")]
kept = {q: D.trends_rows([(q, 5000)], sd, "2026-11-10", lane=ln) for q, sd, ln in real}
check("real product / skill / app searches kept", all(kept.values()), [q for q, v in kept.items() if not v])
# v1.34 — from the 2nd run on 10 Nov
old_noise = [{"seed": q, "added": "2026-11-10", "last_rising": "2026-11-10", "lane": ln} for q, ln in
             [("jobs hunt stripe engineer", "apps"), ("namjooning stanford", "skills"), ("iphone 17 pro diwali", "products")]]
check("yesterday's noise can't return as growing seeds",
      "jobs hunt stripe engineer" not in D.active_seeds(old_noise, "2026-11-11", ["vpn"], "apps")
      and "namjooning stanford" not in D.active_seeds(old_noise, "2026-11-11", ["excel"], "skills")
      and "iphone 17 pro diwali" not in D.active_seeds(old_noise, "2026-11-11", ["mop"], "products"))
check("skills need a learning word: 'burmese python…' rejected, 'python course' kept",
      not D.trends_rows([("burmese python concrete pipe nest", 18050)], "python", "2026-11-10", lane="skills")
      and D.trends_rows([("python course free", 900)], "python", "2026-11-10", lane="skills"))
check("apps: short brand searches allowed ('capcut pc'), YouTube still has to confirm",
      D.trends_rows([("capcut pc", 900)], "video editor", "2026-11-10", lane="apps"))
st = {}
D.trends_rows([("diwali gift hamper same day", 550), ("corporate gift hamper", 900), ("gift hamper login", 900),
               ("gift hamper", 50)], "gift hamper", "2026-11-10", stats=st)
check("after Diwali: 'diwali gift hamper' dropped as out of season; counts explain every drop",
      st == {"raw": 4, "out_of_season": 1, "kept": 1, "blocked": 1, "weak": 1}, st)

check("rotation: different slice daily, whole list covered",
      D.lane_seeds("products", "2026-11-10") != D.lane_seeds("products", "2026-11-11")
      and set().union(*[D.lane_seeds("products", f"2026-11-{d:02d}") for d in range(10, 16)]) == set(D.PRODUCT_SEEDS))
nm = D.youtube_rows([{"id": "v1", "channel": "c1", "title": "10 Amazon Kitchen Gadgets Under 500",
                      "description": "https://www.amazon.in/dp/B0NONAME01", "views": 100, "published": "2026-11-08T00:00:00Z"}],
                    lambda u: None, "2026-11-10", now=now)
check("unnamed YouTube product gets the video title as a label", nm[0]["name"].startswith("(unnamed — from video: 10 Amazon Kitchen"), nm)

# ── release gates
hist_rows = []
for i in range(25):                      # 25 skills confirmed on 1 Nov; 15 seen rising again on 12 Nov
    hist_rows.append({"date": "2026-11-01", "item_key": f"sk:s{i}", "lane": "skills", "status": "2+ sources"})
    if i < 15:
        hist_rows.append({"date": "2026-11-12", "item_key": f"sk:s{i}", "lane": "skills", "status": "watch"})
pers = D.lane_persistence(hist_rows, "skills", "2026-11-16")
check("persistence: 15 of 25 rose again 10+ days later", pers == {"agreed": 25, "judged": 25, "persisted": 15, "rate": 0.6}, pers)
leads = [{"seller": f"S{i}", "date_sent": "2026-11-10", "replied": "yes" if i < 3 else "no",
          "price_inr_month": "499" if i < 3 else ""} for i in range(10)]
g = {n: ok for n, ok, _ in D.readiness(hist_rows, {"eligible": 40, "appeared": 10, "rate": 0.25}, leads, "2026-11-16")}
check("gates: skills ✅ (25 items, 60%), apps ⏳ (no data), products ⏳ (25% < 30%), B2B ✅ (10 sent, 3 yes, ₹499)",
      g == {"Product discovery (Flipkart lane)": False, "Skills & courses": True, "Apps & software": False,
            "Seller data (B2B brief)": True}, g)
check("example row in SELLER_LEADS doesn't count", not dict((n, ok) for n, ok, _ in D.readiness([], {}, [
      {"seller": "(example — replace)", "date_sent": "x", "replied": "yes", "price_inr_month": "999"}] * 12, "2026-11-16"))["Seller data (B2B brief)"])

# ── brief: new sections
import brief as BR
page, _ = BR.build(pd.DataFrame(columns=["asin", "title", "trend_score", "category_slug"]), None, "", pd.DataFrame(),
                   "2026-11-10", None, {}, 0, early=[("neck fan", "gtrends + youtube")], ready=[("Steel bottle 1L", "✅")])
check("brief shows 'Early signals' (labelled unproven) and 'Held up for 2–3 weeks'",
      "Early signals" in page and "Unproven" in page and "Held up for 2–3 weeks" in page and "neck fan" in page)

# ── full offline run of main_discover (Trends + YouTube mocked)
import trend_fetcher as tf
TMP = tempfile.mkdtemp(); tf.LOCAL_OUT_DIR = TMP; tf.TODAY = "2026-11-10"
pd.DataFrame([{"date": "2026-11-09", "list": "bestsellers", "category_slug": "kitchen", "asin": "B0X", "rank": 1,
               "title": "Steel bottle", "price_inr": "199"}]).to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
tf.disc_trends = lambda seeds, today: (tr, 0)
tf.disc_youtube = lambda today: (yt, 5)
sent = []; tf.send_telegram = sent.append
tf.archive_today = lambda name, df: None
os.environ["BR_READINESS"] = "1"
tf.main_discover()
dd = pd.read_csv(os.path.join(TMP, "DISCOVERY.csv"))
check("DISCOVERY tab written with today's items", len(dd) == 4 and set(dd["date"]) == {"2026-11-10"}, len(dd))
check("admin summary lists agreement + creators + searches",
      sent and "2+ sources agree" in sent[0] and "Most-linked" in sent[0] and "Rising shopping searches" in sent[0], sent[:1])
check("weekly readiness report sent (forced)", any("Release readiness" in m for m in sent), [m[:40] for m in sent])
check("no strong (≥1000%) phrase today → no new seeds written", not os.path.exists(os.path.join(TMP, "DISC_SEEDS.csv")))
shutil.rmtree(TMP)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
