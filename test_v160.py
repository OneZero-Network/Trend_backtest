"""v1.60 growth machine: tracking IDs per channel, X agent (OAuth, caps, link-free), content agent + fact check,
share loop footers, weekly formats, growth ledger + kill-line review, bot index; end-to-end drip and growth runs."""
import json, os, re, sys, tempfile
from datetime import datetime, timedelta, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import trend_fetcher as tf
import tags as T, xpost, content, growth, radar_index, picks

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info)[:500] if not ok else ""))
    if not ok: FAILS.append(name)

IST = timezone(timedelta(hours=5, minutes=30))

# ══ 1. tracking IDs ═══════════════════════════════════════════════════
tg = T.Tags("onezeronetwor-21", "telegram=oztg-21, whatsapp=ozwa-21; x=ozx-21\nbot=ozbot-21, telegram.durable=oztgdur-21, bad=nope")
check("tags: channel IDs parsed from commas, semicolons and new lines", tg.tag("whatsapp") == "ozwa-21" and tg.tag("x") == "ozx-21" and tg.tag("bot") == "ozbot-21")
check("tags: '<channel>.<arm>' beats '<channel>'", tg.tag("telegram", "durable") == "oztgdur-21" and tg.tag("telegram", "momentum") == "oztg-21")
check("tags: unknown channel or malformed ID → the default store ID, and the problem is reported",
      tg.tag("site") == "onezeronetwor-21" and T.Tags("d-21", "x=not a tag").tag("x") == "d-21" and "bad=nope" in tg.problems)
check("tags: for_pick reads the arm from a pool row (Series or dict; 'nan' = no arm)",
      tg.for_pick("telegram")(pd.Series({"arm": "durable"})) == "oztgdur-21" and tg.for_pick("telegram")({"arm": "nan"}) == "oztg-21")
check("tags: reverse lookup for the Associates report", tg.channel_of("OZWA-21") == "whatsapp" and tg.channel_of("onezeronetwor-21") == "default"
      and T.Tags("onezeronetwor-21", "").channel_of("onezeronetwor-21") == "telegram" and tg.channel_of("zz-21") == "other")

# ══ 2. X agent ════════════════════════════════════════════════════════
_, sig = xpost.oauth1_header("POST", "https://api.twitter.com/1.1/statuses/update.json",
                             {"status": "Hello Ladies + Gentlemen, a signed OAuth request!", "include_entities": "true"},
                             "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                             "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
                             nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp=1318622958)
check("X OAuth 1.0a signature matches Twitter's documented example", sig == "hCtSmYh+iHYCEqBWrE7C7hYmtUk=", sig)
check("X: a link costs $0.20, plain text $0.015 (t.me and bare domains count as links)",
      xpost.cost_of("hi") == 0.015 and xpost.cost_of("see https://x.y") == 0.20 and xpost.has_url("join t.me/+abc") and xpost.has_url("amzn.in/d/x"))
day = "2026-11-21"
check("X guard: off means off", xpost.guard([], "hello", day, day[:7], mode="off")[0] is False)
check("X guard: link-free mode refuses a text with a link", not xpost.guard([], "go https://a.in", day, day[:7], mode="linkfree")[0])
cap_rows = [{"posted_at": f"{day} 0{i}:00", "status": "posted", "est_cost": 0.015, "text": f"t{i}"} for i in range(xpost.MAX_PER_DAY)]
check("X guard: daily cap from the log", "daily cap" in xpost.guard(cap_rows, "new text", day, day[:7], mode="linkfree")[1])
spent = [{"posted_at": f"2026-11-0{i % 9 + 1} 10:00", "status": "posted", "est_cost": 2.5, "text": f"x{i}"} for i in range(4)]
check("X guard: monthly budget from the log ($10 default)", "monthly budget" in xpost.guard(spent, "new", day, day[:7], mode="linkfree")[1])
check("X guard: refuses a text already posted (X bans duplicates), ignoring punctuation and case",
      "already posted" in xpost.guard([{"posted_at": "2026-11-20 10:00", "status": "posted", "est_cost": 0.015, "text": "Hello, World!"}],
                                      "hello world", day, day[:7], mode="linkfree")[1])
check("X guard: dry runs count toward the daily cap in dry mode only",
      not xpost.guard([dict(r, status="dry") for r in cap_rows], "zz", day, day[:7], mode="dry")[0]
      and xpost.guard([dict(r, status="dry") for r in cap_rows], "zz", day, day[:7], mode="linkfree")[0])
class R:
    def __init__(self, code, body): self.status_code, self._b, self.text = code, body, json.dumps(body)
    def json(self): return self._b
seen = {}
def fake_post(url, headers=None, data=None, timeout=None):
    seen.update(url=url, auth=headers["Authorization"], body=json.loads(data)); return R(201, {"data": {"id": "1799"}})
ok, ref, code = xpost.post("hello", ("ck", "cs", "tk", "ts"), http_post=fake_post)
check("X post: OAuth header, JSON body {text}, id returned", ok and ref == "1799" and seen["url"] == xpost.API
      and seen["auth"].startswith("OAuth ") and 'oauth_signature="' in seen["auth"] and seen["body"] == {"text": "hello"})
ok2, ref2, code2 = xpost.post("x", ("a", "b", "c", "d"), http_post=lambda *a, **k: R(403, {"title": "Forbidden", "detail": "not permitted"}))
check("X post: a refusal comes back as a readable error", not ok2 and code2 == 403 and "not permitted" in ref2, ref2)

# ══ 3. content agent ══════════════════════════════════════════════════
check("Telegram footer: forward line + named join link when it exists", "Forward this" in content.telegram_footer({"tg-forward": "https://t.me/+f"})
      and "https://t.me/+f" in content.telegram_footer({"tg-forward": "https://t.me/+f"}) and "Join" not in content.telegram_footer({}))
check("WhatsApp footer: channel link + its own Telegram link", "Follow: https://wa.me/c" in content.wa_footer({"wa-channel": "https://t.me/+w"}, "https://wa.me/c")
      and "https://t.me/+w" in content.wa_footer({"wa-channel": "https://t.me/+w"}))
fd = {"rank_now": 2, "rank_then": 3, "arm": "durable", "dur_days": 14, "dur_window": 14}
fm = {"rank_now": 12, "rank_then": 40}
fn = {"rank_now": 7, "rank_then": float("nan")}
p = {"title": "Lifelong PVC Hex Dumbbells Pack of 2 | Home Gym", "asin": "B09W5PSTBP"}
txd, vd = content.x_linkfree(p, fd, "Sports", "s1", weight=picks.x_weight)
txm, vm = content.x_linkfree(p, fm, "Sports", "s1", weight=picks.x_weight)
txn, vn = content.x_linkfree(p, fn, "Sports", "s1", weight=picks.x_weight)
check("X text: durable says '#2 … 14 days', momentum '#40 → #12', new 'at #7'; none has a link",
      "#2" in txd and "14 days" in txd and "#40" in txm and "#12" in txm and "#7" in txn and not any(xpost.has_url(t) for t in (txd, txm, txn)), (txd, txm, txn))
check("X text: a fact we cannot support → nothing", content.x_linkfree(p, {"rank_now": 30, "rank_then": 20}, "Sports", "s")[0] == "")
variants = {content.x_linkfree(p, fd, "Sports", f"seed{i}", weight=picks.x_weight)[1] for i in range(40)}
check("X text: templates rotate (several variants), and the same seed repeats exactly",
      len(variants) >= 3 and content.x_linkfree(p, fd, "Sports", "same")[0] == content.x_linkfree(p, fd, "Sports", "same")[0], variants)
long_p = {"title": "A" * 300, "asin": "B0"}
_lt = content.x_linkfree(long_p, fd, "Sports", "s", weight=picks.x_weight)[0]
check("X text: a long title is cut to fit 280", _lt and picks.x_weight(_lt) <= 280, _lt)
check("X text: never a bare domain like Amazon.in (X would turn it into a paid link)",
      all("amazon.in" not in content.x_linkfree(p, f, "Sports", f"k{i}")[0].lower() for i in range(30) for f in (fd, fm, fn)))
facts = json.dumps({"rank_now": 2, "days_on_list": 14, "days_observed": 14})
check("fact check: a number not in our data is refused", "not in our data" in content.validate("Now #1 for 30 days!", facts))
check("fact check: hype, links, hashtags and mentions are refused",
      all(content.validate(t, facts) for t in ("Best dumbbells #2", "see t.me/+x", "#2 now #fitness", "#2 ask @amazon")))
check("fact check: a faithful rewrite passes", content.validate("Dumbbells: #2 in Sports for 14 days straight. Daily list, link in bio.", facts) == "")
os.environ["AFF_COPY_AGENT"] = ""
check("copy agent: off by default → the template is used", content.CopyAgent().rewrite("draft #2", {"r": 2}) == ("draft #2", "template"))
os.environ.update(AFF_COPY_AGENT="claude", ANTHROPIC_API_KEY="k")
good = lambda *a, **k: R(200, {"content": [{"type": "text", "text": "Fourteen straight days at #2 in Sports. Daily list: link in bio."}]})
invent = lambda *a, **k: R(200, {"content": [{"type": "text", "text": "Now #1 and 50 people bought it today!"}]})
boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
check("copy agent: a valid rewrite is used", content.CopyAgent(http_post=good).rewrite("draft #2", {"rank_now": 2}, weight=picks.x_weight)[1] == "claude")
txt, src = content.CopyAgent(http_post=invent).rewrite("draft #2", {"rank_now": 2}, weight=picks.x_weight)
check("copy agent: an invented number is caught and the template is kept", txt == "draft #2" and src.startswith("claude-rejected"), src)
check("copy agent: API down → template, never a crash", content.CopyAgent(http_post=boom).rewrite("d #2", {"r": 2}) == ("d #2", "claude-error"))
check("copy agent: monthly cap", content.CopyAgent(http_post=good).rewrite("d #2", {"r": 2}, used_this_month=10**6)[1] == "template")
os.environ.update(AFF_COPY_AGENT="", ANTHROPIC_API_KEY="")

sigs = pd.DataFrame([
    {"asin": "A1", "title": "Steel Lunch Box, 3 tier", "category_slug": "kitchen", "durable": "True", "durable_score": 95, "durable_days": 14, "durable_window": 14, "exit_flag": "", "stock_state": "in_stock", "non_product": ""},
    {"asin": "A2", "title": "Second Kitchen Item", "category_slug": "kitchen", "durable": "True", "durable_score": 94, "durable_days": 14, "durable_window": 14, "exit_flag": "", "stock_state": "in_stock", "non_product": ""},
    {"asin": "B1", "title": "Pull Up Bar", "category_slug": "sports", "durable": "True", "durable_score": 90, "durable_days": 13, "durable_window": 14, "exit_flag": "", "stock_state": "carried", "non_product": ""},
    {"asin": "C1", "title": "Falling Lamp", "category_slug": "home", "durable": "True", "durable_score": 99, "durable_days": 14, "durable_window": 14, "exit_flag": "falling", "stock_state": "in_stock", "non_product": ""},
    {"asin": "D1", "title": "Credit Card Bill", "category_slug": "luggage", "durable": "True", "durable_score": 99, "durable_days": 14, "durable_window": 14, "exit_flag": "", "stock_state": "in_stock", "non_product": "not a physical product"},
    {"asin": "E1", "title": "Sold out kettle", "category_slug": "garden", "durable": "True", "durable_score": 98, "durable_days": 14, "durable_window": 14, "exit_flag": "", "stock_state": "unavailable", "non_product": ""},
])
ru = content.roundup(sigs, lambda r: "oztg-21", picks.affiliate_link, {"kitchen": "Home & Kitchen"}, "Amazon Hidden Bestsellers",
                     links={"tg-forward": "https://t.me/+f"}, disclosure=picks.DISCLOSURE)
check("roundup: one per category, best durability first, falling / sold out / non-products left out",
      ru.count("amazon.in/dp/") == 2 and "A1" in ru and "B1" in ru and not any(a in ru for a in ("A2", "C1", "D1", "E1")), ru)
check("roundup: claims only days on the list, with the join link and the disclosure",
      "14 days on the list" in ru and "13 of 14 days on the list" in ru and "https://t.me/+f" in ru and "Associate" in ru and "₹" not in ru)
check("roundup: nothing durable → no post", content.roundup(sigs.assign(durable="False"), lambda r: "", picks.affiliate_link, {}, "T") == "")
pl = content.poll(pd.DataFrame({"title": ["Steel Lunch Box, 3 tier", "Pull Up Bar | doorway", "Steel Lunch Box, 2 tier", "Yoga Mat", "Rope", "Ball"]}))
check("poll: 2–4 distinct short names, anonymous (channels require it)", pl and 2 <= len(pl["options"]) <= 4 and pl["is_anonymous"]
      and len(set(pl["options"])) == len(pl["options"]) and all(len(o) <= 100 for o in pl["options"]), pl)
check("poll: fewer than 2 products → no poll", content.poll(pd.DataFrame({"title": ["One"]})) is None)

# ══ 4. growth ledger, joins, Associates report, review ════════════════
ts = int(datetime(2026, 11, 20, 23, 30, tzinfo=IST).timestamp())
ups = [{"update_id": 10, "chat_member": {"chat": {"id": -100, "username": "onezerodeals"}, "date": ts,
        "old_chat_member": {"status": "left"}, "new_chat_member": {"status": "member"}, "invite_link": {"name": "x-bio"}}},
       {"update_id": 11, "chat_member": {"chat": {"id": -100, "username": "onezerodeals"}, "date": ts, "old_chat_member": {"status": "left"}, "new_chat_member": {"status": "member"}}},
       {"update_id": 12, "chat_member": {"chat": {"id": -100, "username": "onezerodeals"}, "date": ts, "old_chat_member": {"status": "member"}, "new_chat_member": {"status": "left"}, "invite_link": {"name": "x-bio"}}},
       {"update_id": 13, "chat_member": {"chat": {"id": -999}, "date": ts, "old_chat_member": {"status": "left"}, "new_chat_member": {"status": "member"}}}]
cnt, off = growth.joins_from_updates(ups, "@onezerodeals")
check("joins: counted per invite-link name, by IST date; leaves and other chats ignored; next offset",
      cnt == {"2026-11-20": {"x-bio": 1, "(public link)": 1}} and off == 14, (cnt, off))
class FakeTG:
    def __init__(self, webhook=""): self.calls, self.webhook = [], webhook
    def call(self, m, **p):
        self.calls.append((m, p))
        return {"getWebhookInfo": {"url": self.webhook}, "getUpdates": ups, "getChatMemberCount": 1234,
                "createChatInviteLink": {"invite_link": f"https://t.me/+{p.get('name', '')}"}}.get(m)
ft = FakeTG()
links, created = growth.ensure_links(ft, "@c", pd.DataFrame([{"name": "tg-forward", "link": "https://t.me/+old", "created": "2026-11-01"}]), today=day)
check("invite links: only the missing ones are created, existing ones kept", "tg-forward" not in created and len(links) == len(growth.LINK_NAMES)
      and links.set_index("name").loc["tg-forward", "link"] == "https://t.me/+old" and sum(c[0] == "createChatInviteLink" for c in ft.calls) == len(growth.LINK_NAMES) - 1)
check("joins: with the bot's webhook set, getUpdates is not called (the Worker counts instead)",
      growth.poll_joins(FakeTG("https://w.dev/tg"), "@c")[2] == "webhook")
c2, n2, src2 = growth.poll_joins(FakeTG(), "@onezerodeals")
check("joins: without a webhook, getUpdates is read and confirmed", src2 == "getUpdates" and n2 == 14)
L = growth.add_rows(None, [("2026-11-20", "members", "telegram", 100), ("2026-11-20", "joins", "x-bio", 1)])
L = growth.add_rows(L, [("2026-11-20", "members", "telegram", 105)])
check("ledger: a later value for the same date·metric·key replaces the earlier", len(L) == 2 and growth.series(L, "members", "telegram").iloc[-1] == 105)
rep = pd.DataFrame([{"Tracking Id": "oztg-21", "Clicks": "1,200", "Items Ordered": "40", "Items Shipped": "38", "Advertising Fees (₹)": "₹1,520.50"},
                    {"Tracking Id": "ozwa-21", "Clicks": "300", "Items Ordered": "9", "Items Shipped": "9", "Advertising Fees (₹)": "₹410"},
                    {"Tracking Id": "Total", "Clicks": "1500", "Items Ordered": "49", "Items Shipped": "47", "Advertising Fees (₹)": "₹1,930.50"}])
asc = growth.parse_assoc(rep, tg)
check("Associates report: loose headers, ₹ and commas, totals row skipped, split by channel",
      asc["telegram"]["clicks"] == 1200 and asc["whatsapp"]["earnings"] == 410 and asc["telegram"]["earnings"] == 1520.5 and "other" not in asc, asc)
check("Associates report: a table without a tracking-ID column → nothing", growth.parse_assoc(pd.DataFrame({"a": [1]}), tg) == {})
base = datetime(2026, 10, 1)
rows = []
for i in range(52):
    d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
    rows += [(d, "members", "telegram", int(1000 * 1.01 ** i)), (d, "joins", "tg-forward", 3), (d, "bot", "users", 2)]
    if i >= 10:
        rows += [(d, "x_posts", "posted", 2), (d, "joins", "x-bio", 1)]
Lr = growth.add_rows(None, rows)
today = (base + timedelta(days=51)).strftime("%Y-%m-%d")
rr, lines = growth.review(Lr, today, asc, {"paying_sellers": "2", "waitlist_opened": "2026-11-15"})
st = {r["bet"]: r["status"] for r in rr}
check("review: X line crosses its kill line after 30 days: 41 x-bio joins, pass needs 50 → switch X off",
      st["X link-free funnel"].startswith("KILL") and "switch X off" in st["X link-free funnel"], st)
check("review: lines before their check date say 'collecting'", st["Shopper bot"].startswith("collecting") and st["Radar for sellers"].startswith("collecting"), st)
check("review: Telegram growth ~7%/week passes; share loop 2.1 per 100 members passes", st["Telegram growth"] == "PASS" and st["Share loop"] == "PASS", st)
check("review: WhatsApp clicks per month from the weekly report (300 × 30/7 ≈ 1,286 ≥ 100) passes — but only after week 8",
      st["WhatsApp (assisted)"] == "collecting (day 51 of 56)", st)
goal_line = [l for l in lines if l.startswith("🎯")][0]
check("review: goal worked backwards with MEASURED earnings when a report exists, plus weeks to go at current growth",
      "measured" in goal_line and "members" in goal_line and "weeks away" in goal_line, goal_line)
_, lines0 = growth.review(Lr, today, {}, {})
check("review: without a report the goal math says ASSUMED", any("ASSUMED" in l for l in lines0))
_, lines_e = growth.review(None, day, {}, {})
check("review: an empty ledger does not crash; every line 'not started'", all("not started" in l or "Goal" in l or l == "" for l in lines_e), lines_e)

# ══ 5. bot index ══════════════════════════════════════════════════════
D0 = datetime(2026, 11, 1)
dd = [(D0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(16)]
H = []
for i, d in enumerate(dd):
    H.append({"date": d, "list": "bestsellers", "category_slug": "sports", "asin": "DUR0000001", "rank": 2, "title": "Hex Dumbbells, pair"})
    H.append({"date": d, "list": "bestsellers", "category_slug": "sports", "asin": "UP00000001", "rank": 40 if i < 10 else 12, "title": "Foam Roller"})
    if i < 12: H.append({"date": d, "list": "bestsellers", "category_slug": "sports", "asin": "GONE000001", "rank": 30, "title": "Old Lamp"})
    if i == 15: H.append({"date": d, "list": "bestsellers", "category_slug": "sports", "asin": "NEW0000001", "rank": 5, "title": "New Bottle"})
    H.append({"date": d, "list": "bestsellers", "category_slug": "luggage", "asin": "BILL000001", "rank": 1, "title": "Credit Card Bill"})
H = pd.DataFrame(H)
H = pd.concat([H, pd.DataFrame([{"date": d, "list": "bestsellers", "category_slug": "sports", "asin": "FALL000001", "rank": 10 if i < 13 else 30, "title": "Steel Mug"} for i, d in enumerate(dd)])])
S = pd.DataFrame([{"asin": "DUR0000001", "category_slug": "sports", "durable": "True", "durable_score": 95, "exit_flag": "", "stock_state": "in_stock", "non_product": ""},
                  {"asin": "FALL000001", "category_slug": "sports", "durable": "True", "durable_score": 80, "exit_flag": "falling", "exit_note": "fell #10 → #30 in 3 days", "non_product": ""},
                  {"asin": "UP00000001", "category_slug": "sports", "durable": "False", "exit_flag": "", "non_product": ""},
                  {"asin": "BILL000001", "category_slug": "luggage", "durable": "False", "exit_flag": "", "non_product": "not a physical product"}])
ix = radar_index.build(S, H, dd[-1], {"sports": "Sports"}, version="1.60")
it = ix["items"]
check("bot index: durable / climbing / new / gone states with observed numbers",
      it["DUR0000001"]["s"] == "durable" and it["DUR0000001"]["d"] == 14 and it["DUR0000001"]["w"] == 14
      and it["UP00000001"]["s"] == "climbing" and it["UP00000001"]["r7"] == 40 and it["NEW0000001"]["s"] == "new"
      and it["GONE000001"]["s"] == "gone" and it["GONE000001"]["l"] == dd[11], it)
check("bot index: a falling product carries its exit note and is never in /top",
      it["FALL000001"]["s"] == "falling" and it["FALL000001"]["x"] == "fell #10 → #30 in 3 days" and "FALL000001" not in ix["top"].get("sports", []))
check("bot index: non-products are left out; /top holds the durable ones", "BILL000001" not in it and ix["top"].get("sports") == ["DUR0000001"])
tmp = tempfile.mkdtemp()
path = radar_index.write(ix, tmp)
check("bot index: written to bot/ (not inside the dated archive folders) for the archive step to push",
      path.endswith(os.path.join("bot", "radar_index.json")) and "archive" not in path.split(tmp)[1]
      and json.load(open(path))["items"]["DUR0000001"]["r"] == 2 and os.path.getsize(path) < 5000)

# ══ 6. end to end: drip with tracking IDs, footer, X dry run, weekly formats ══
TMP = tempfile.mkdtemp()
tf.LOCAL_OUT_DIR = TMP
CATS = ["kitchen", "garden", "sports", "luggage", "pet-supplies", "home-improvement"]
days = [(datetime(2026, 11, 8) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)]
HH = []
for i, d in enumerate(days):
    for c in CATS:
        for k in range(1, 61):
            HH.append({"date": d, "list": "bestsellers", "category_slug": c, "asin": f"{c[:1].upper()}{k:08d}",
                       "rank": k if (k > 6 or i >= 7) else min(k + 30, 100), "price_inr": "199", "rating": "4", "reviews": "10",
                       "title": f"{c} item {k}"})
pd.DataFrame(HH).to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
sg = []
for j, c in enumerate(CATS):
    for k in range(1, 13):
        durable = k > 6
        sg.append({"asin": f"{c[:1].upper()}{k:08d}", "title": f"{c} item {k}", "category_slug": c, "trend_score": 100 - k - j * 0.1,
                   "rank_change": 30 if not durable else 0, "price_inr": "199", "seasonal": "", "compliance": "", "actionable": "True",
                   "durable": str(durable), "durable_score": 90 - k - j * 0.1, "durable_days": 14, "durable_window": 14,
                   "exit_flag": "", "exit_note": "", "stock_state": "in_stock", "non_product": "", "days_on_list_14d": 14, "bs_rank_today": k})
pd.DataFrame(sg).to_csv(os.path.join(TMP, "TREND_SIGNALS.csv"), index=False)
pd.DataFrame([{"name": n, "link": f"https://t.me/+{n}", "created": "2026-11-01"} for n in growth.LINK_NAMES]).to_csv(os.path.join(TMP, "GROWTH_LINKS.csv"), index=False)
sent, polls = [], []
tf.send_telegram = lambda m: sent.append((tf.TELEGRAM_CHAT_ID, m))
tf._tg_post = lambda url, **kw: (polls.append((url, kw.get("json"))), R(200, {"ok": True}))[1]
tf.TELEGRAM_CHAT_ID, tf.TELEGRAM_BOT_TOKEN = "ADMIN", "admintok"
tf.AFFILIATE_CHAT_ID, tf.AFFILIATE_BOT_TOKEN, tf.AFFILIATE_TAG = "@onezerodeals", "pubtok", "onezeronetwor-21"
tf.AFFILIATE_GROUPS, tf.AFFILIATE_SHEET_ID = "", ""
tf.BR_POOL_SIZE, tf.DRIP_SLOTS_IST, tf.DRIP_MIN_GAP_MIN = 12, [9, 11, 13, 15, 17, 19, 21], 75
os.environ["AFF_TAGS"] = "telegram=oztg-21, whatsapp=ozwa-21, x=ozx-21"
xpost.MODE = "dry"

def run_at(dstr, hour, fn=None):
    tf.NOW_IST = datetime.strptime(dstr, "%Y-%m-%d").replace(hour=hour, minute=35, tzinfo=IST)
    tf.TODAY = dstr
    (fn or tf.main_drip)()

sun = days[-1]                                         # 2026-11-21 is a Saturday → use the next day for Sunday
assert datetime.strptime(sun, "%Y-%m-%d").weekday() == 5
run_at(sun, 9)
pub = [m for c, m in sent if c == "@onezerodeals"]
check("drip: public post links carry the Telegram tracking ID, not the default", pub and "tag=oztg-21" in pub[0] and "onezeronetwor-21" not in pub[0], pub[:1])
check("drip: public post ends with the forward line + tg-forward join link, before the disclosure",
      "Forward this" in pub[0] and "https://t.me/+tg-forward" in pub[0] and pub[0].index("Forward this") < pub[0].index("Associate"))
wa = [m for c, m in sent if c == "ADMIN" and "OneZero Deals —" in m]
check("daily WhatsApp text uses the WhatsApp tracking ID and carries the wa-channel join link",
      wa and "tag=ozwa-21" in wa[0] and "https://t.me/+wa-channel" in wa[0] and "tag=oztg-21" not in wa[0], wa[:1])
xdry = [m for c, m in sent if c == "ADMIN" and "X dry run" in m]
xl = pd.read_csv(os.path.join(TMP, "X_POST_LOG.csv"))
check("X agent (dry): admin sees the link-free post; X_POST_LOG records it with its cost, no link",
      len(xdry) == 1 and xl.iloc[-1]["status"] == "dry" and not bool(xl.iloc[-1]["has_url"]) and float(xl.iloc[-1]["est_cost"]) == 0.015
      and "https://" not in xdry[0].split("<pre>")[1], (xdry, xl.tail(1).to_dict("records")))
check("X agent picks the durable pick of the slot when there is one", xl.iloc[-1]["arm"] == "durable")
plog = pd.read_csv(os.path.join(TMP, "AFF_POST_LOG.csv"))
check("AFF_POST_LOG records channel and tracking ID per post", set(plog["channel"]) == {"telegram"} and set(plog["tag"]) <= {"oztg-21"})
run_at(sun, 9)
check("a second run in the same slot posts nothing and makes no second X post", len(pd.read_csv(os.path.join(TMP, "X_POST_LOG.csv"))) == 1)
sunday = (datetime.strptime(sun, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
pd.DataFrame(HH).assign(date=lambda d: d["date"]).to_csv(os.path.join(TMP, "HISTORY.csv"), index=False)
sent.clear()
run_at(sunday, tf.BR_ROUNDUP_HOUR)
rup = [m for c, m in sent if c == "@onezerodeals" and "stayed on Amazon India's bestseller lists all week" in m]
check("Sunday roundup: posted once to the channel at its hour, with Telegram tracking IDs", len(rup) == 1 and "tag=oztg-21" in rup[0], [m[:80] for c, m in sent])
sent.clear()
tf.DRIP_MIN_GAP_MIN = 0
run_at(sunday, tf.BR_ROUNDUP_HOUR)
check("Sunday roundup: not repeated in the same day", not any("all week" in m for c, m in sent if c == "@onezerodeals"))
wed = "2026-11-25"
polls.clear()
run_at(wed, tf.BR_POLL_HOUR)
check("Wednesday poll: sendPoll to the channel with 2–4 options", any("sendPoll" in u and j["chat_id"] == "@onezerodeals" and 2 <= len(j["options"]) <= 4 for u, j in polls), polls)

# ══ 7. end to end: growth job ═════════════════════════════════════════
os.remove(os.path.join(TMP, "GROWTH_LINKS.csv"))
growth.TG = lambda tok, http_post=None: FakeTG()
sent.clear()
run_at("2026-11-23", 22, tf.main_growth)                # a Monday → weekly review too
G = pd.read_csv(os.path.join(TMP, "GROWTH.csv"))
check("growth: member count and X dry-run counts written to the ledger",
      ((G["metric"] == "members") & (G["value"].astype(float) == 1234)).any() and ((G["metric"] == "x_posts") & (G["key"] == "posted")).any())
check("growth: joins per invite link from Telegram updates", ((G["metric"] == "joins") & (G["key"] == "x-bio")).any())
check("growth: missing invite links created and listed for you to place", os.path.exists(os.path.join(TMP, "GROWTH_LINKS.csv"))
      and any("New invite links" in m and "x-bio" in m for c, m in sent))
rev = [m for c, m in sent if c == "ADMIN" and "Growth review" in m]
check("growth: Monday review in the admin chat with every kill line, the goal and the tracking IDs",
      rev and all(b in rev[0] for b in ("X link-free funnel", "Share loop", "Telegram growth", "Shopper bot", "Radar for sellers", "Goal", "Tracking IDs")), rev[:1])
check("growth: GROWTH_REPORT keeps one row per kill line per review", len(pd.read_csv(os.path.join(TMP, "GROWTH_REPORT.csv"))) == len(growth.KILL_LINES))
before = float(G[(G["metric"] == "joins") & (G["key"] == "x-bio")]["value"].iloc[0])
run_at("2026-11-24", 22, tf.main_growth)
G2 = pd.read_csv(os.path.join(TMP, "GROWTH.csv"))
after = float(G2[(G2["metric"] == "joins") & (G2["key"] == "x-bio") & (G2["date"] == "2026-11-20")]["value"].iloc[0])
check("growth: getUpdates joins ADD to the day's count (a day can span two runs)", after == before + 1, (before, after))

check("VERSION is 1.60 or later", float(tf.VERSION) >= 1.60)
here = os.path.dirname(os.path.abspath(__file__))
wf = {n: open(os.path.join(here, ".github", "workflows", n)).read() for n in ("drip.yml", "growth.yml")}
check("workflows: the archive step pushes output/bot to the data-archive branch",
      "output/bot" in open(os.path.join(here, ".github", "workflows", "trend_fetcher.yml")).read())
check("workflows: drip passes the X / tags / copy-agent settings; growth.yml runs BR_MODE=growth",
      all(k in wf["drip.yml"] for k in ("AFF_X_MODE", "AFF_TAGS", "X_API_KEY", "AFF_COPY_AGENT")) and "BR_MODE: growth" in wf["growth.yml"].replace("  ", " ").replace("BR_MODE:  ", "BR_MODE: ")
      or re.search(r"BR_MODE:\s+growth", wf["growth.yml"]) is not None)
print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
