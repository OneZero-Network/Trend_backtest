// node test_worker.mjs — v1.60 shopper bot + join counter, with Telegram, GitHub and KV mocked.
import worker, { extractAsin, describe, topList, reply, statsJson, istDate, _resetIndex } from "./worker.js";

let fails = 0;
const check = (name, ok, info = "") => { console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  " + JSON.stringify(info).slice(0, 400))); if (!ok) fails++; };

const IX = { date: "2026-11-21", window: 14, cats: { sports: "Sports, Fitness & Outdoors", kitchen: "Home & Kitchen" },
  items: {
    B09W5PSTBP: { t: "Lifelong PVC Hex Dumbbells Pack of 2", c: "sports", r: 2, r7: 3, d: 14, w: 14, s: "durable", l: "2026-11-21" },
    B0CLIMB001: { t: "Foam Roller", c: "sports", r: 12, r7: 40, d: 9, w: 14, s: "climbing", l: "2026-11-21" },
    B0FALL0001: { t: "Steel Bottle", c: "kitchen", r: 45, r7: 9, d: 14, w: 14, s: "falling", l: "2026-11-21" },
    B0GONE0001: { t: "Old Lamp <b>", c: "kitchen", r: null, r7: 30, d: 6, w: 14, s: "gone", l: "2026-11-18" },
    B0FALL0002: { t: "", c: "kitchen", r: 30, r7: null, d: 14, w: 14, s: "falling", l: "2026-11-21", x: "fell #15 → #30 in 3 days" },
    B0FALL0003: { t: "Mug", c: "kitchen", r: 50, r7: null, d: 5, w: 14, s: "falling", l: "2026-11-21" },
  },
  top: { sports: ["B09W5PSTBP"], kitchen: [] } };
const env0 = { GH_OWNER: "o", GH_REPO: "r", GH_TOKEN: "t", AFF_TAG_BOT: "ozbot-21", CHANNEL_JOIN_URL: "https://t.me/+bot123",
  BOT_TOKEN: "B", TG_WEBHOOK_SECRET: "s3cret", RUN_KEY: "k", TG_CHAT_ID: "999", TG_BOT_TOKEN: "A" };

function kv() {
  const m = new Map();
  return { m, async get(k) { return m.has(k) ? m.get(k) : null; }, async put(k, v) { m.set(k, String(v)); },
    async list({ prefix }) { return { keys: [...m.keys()].filter(k => k.startsWith(prefix)).map(name => ({ name })), list_complete: true }; } };
}
const sent = [];
const fakeFetch = async (url, opts = {}) => {
  url = String(url);
  if (url.includes("api.github.com")) return { status: 200, json: async () => IX };
  if (url.includes("api.telegram.org")) { sent.push({ url, body: JSON.parse(opts.body || "{}") }); return { status: 200, json: async () => ({ ok: true }), text: async () => "{}" }; }
  if (url.startsWith("https://amzn.in/d/abc")) return { status: 301, headers: { get: () => "https://www.amazon.in/Lifelong-Dumbbells/dp/B09W5PSTBP?ref=x" } };
  if (url.startsWith("https://amzn.to/loop")) return { status: 301, headers: { get: () => "https://amzn.to/loop" } };
  return { status: 404, headers: { get: () => null } };
};
globalThis.fetch = fakeFetch;

// ASIN extraction
check("ASIN from a full product link", await extractAsin("look https://www.amazon.in/Lifelong/dp/B09W5PSTBP/ref=sr_1?x=1", fakeFetch) === "B09W5PSTBP");
check("ASIN from /gp/product/", await extractAsin("https://www.amazon.in/gp/product/B0CLIMB001", fakeFetch) === "B0CLIMB001");
check("ASIN typed on its own", await extractAsin("  b09w5pstbp ", fakeFetch) === "B09W5PSTBP");
check("ASIN behind an amzn.in short link (redirect followed, not fetched as a page)", await extractAsin("https://amzn.in/d/abc", fakeFetch) === "B09W5PSTBP");
check("a redirect loop gives up instead of hanging", await extractAsin("https://amzn.to/loop", fakeFetch) === null);
check("a non-Amazon link is not followed", await extractAsin("https://example.com/dp/B09W5PSTBP1", fakeFetch) === null);
check("plain words → no ASIN", await extractAsin("is this good?", fakeFetch) === null);

// answers
const dur = describe(IX, "B09W5PSTBP", env0);
check("durable answer: rank, category, '14 days on the list', tagged link, join link, disclosure",
  dur.includes("🏆 #2 in Sports, Fitness &amp; Outdoors · 14 days on the list") && dur.includes("tag=ozbot-21") &&
  dur.includes("https://t.me/+bot123") && dur.includes("As an Amazon Associate"), dur);
check("climbing / falling / gone answers say what was observed", describe(IX, "B0CLIMB001", env0).includes("Climbed #40 → #12")
  && describe(IX, "B0FALL0001", env0).includes("Slipping: #9 → #45") && describe(IX, "B0GONE0001", env0).includes("Last seen"));
const f2 = describe(IX, "B0FALL0002", env0), f3 = describe(IX, "B0FALL0003", env0);
check("falling uses the 3-day exit note; never prints '#?' when the week-ago rank is missing",
  f2.includes("fell #15 → #30 in 3 days") && !f2.includes("#?") && f3.includes("now #50") && !f3.includes("#?"), [f2, f3]);
check("a product with no title shows 'Product <ASIN>', never an empty name", f2.startsWith("<b>Product B0FALL0002</b>"), f2.slice(0, 60));
check("titles are HTML-escaped", describe(IX, "B0GONE0001", env0).includes("Old Lamp &lt;b&gt;"));
const miss = describe(IX, "B0NOTSEEN1", env0);
check("unknown product: honest 'not seen', still a link, no invented rank", miss.includes("have not seen") && !/#\d/.test(miss) && miss.includes("tag=ozbot-21"));
check("no answer ever contains a price or a forecast word", ![dur, miss, describe(IX, "B0CLIMB001", env0)].some(t => /₹|\bwill\b|\bbest\b/i.test(t)));
check("/top lists categories, then a category's lasting bestsellers", topList(IX, "", env0).includes("/top sports") && topList(IX, "sports", env0).includes("Lifelong PVC Hex Dumbbells"));
check("/top with an unknown category explains itself", topList(IX, "cars", env0).includes("don't know the category"));

// update handling + counters
_resetIndex();
const env = { ...env0, RADAR_KV: kv() };
const r1 = await reply({ message: { chat: { id: 5, type: "private" }, from: { id: 77 }, text: "https://amzn.in/d/abc" } }, env, fakeFetch);
check("a private message with a link gets the product answer", r1 && r1.chat_id === 5 && r1.text.includes("🏆 #2"), r1);
await reply({ message: { chat: { id: 5, type: "private" }, from: { id: 77 }, text: "/top sports" } }, env, fakeFetch);
await reply({ message: { chat: { id: 6, type: "private" }, from: { id: 78, username: "seller1" }, text: "/pro" } }, env, fakeFetch);
const d = istDate();
check("usage counted: 3 requests, 2 distinct people, 1 waitlist sign-up (no user IDs stored)",
  env.RADAR_KV.m.get(`b:${d}:requests`) === "3" && env.RADAR_KV.m.get(`b:${d}:users`) === "2" && env.RADAR_KV.m.get(`b:${d}:pro`) === "1"
  && ![...env.RADAR_KV.m.keys()].some(k => k.includes("77") || k.includes("78")), [...env.RADAR_KV.m.entries()]);
check("/pro sign-up is forwarded to the admin chat with the username", sent.some(s => s.body.chat_id === "999" && s.body.text.includes("@seller1")));
check("group messages are ignored (the bot only talks in private)", (await reply({ message: { chat: { id: -100, type: "supergroup" }, from: { id: 1 }, text: "B09W5PSTBP" } }, env, fakeFetch)) === null);
const ts = Math.floor(Date.now() / 1000);
await reply({ chat_member: { chat: { id: -1001 }, date: ts, old_chat_member: { status: "left" }, new_chat_member: { status: "member" }, invite_link: { name: "x-bio" } } }, env, fakeFetch);
await reply({ chat_member: { chat: { id: -1001 }, date: ts, old_chat_member: { status: "left" }, new_chat_member: { status: "member" } } }, env, fakeFetch);
await reply({ chat_member: { chat: { id: -1001 }, date: ts, old_chat_member: { status: "member" }, new_chat_member: { status: "left" }, invite_link: { name: "x-bio" } } }, env, fakeFetch);
check("joins counted per invite-link name; a leave is not a join", env.RADAR_KV.m.get(`j:${d}:x-bio`) === "1" && env.RADAR_KV.m.get(`j:${d}:(public link)`) === "1");
const st = await statsJson(env);
check("/stats JSON: joins and bot usage by date", st.joins[d]["x-bio"] === 1 && st.bot[d].requests === 3 && st.bot[d].pro === 1, st);

// webhook endpoint security
const bad = await worker.fetch(new Request("https://w.dev/tg", { method: "POST", body: "{}", headers: { "X-Telegram-Bot-Api-Secret-Token": "wrong" } }), env);
check("webhook refuses a request without the secret token", bad.status === 403);
const okr = await worker.fetch(new Request("https://w.dev/tg", { method: "POST", body: JSON.stringify({ message: { chat: { id: 9, type: "private" }, from: { id: 9 }, text: "/help" } }), headers: { "X-Telegram-Bot-Api-Secret-Token": "s3cret" } }), env);
check("webhook with the secret answers 200 and replies", okr.status === 200 && sent.some(s => s.body.chat_id === 9 && s.body.text.includes("/top")));
const stx = await worker.fetch(new Request("https://w.dev/stats?key=nope"), env);
check("/stats needs the key", stx.status === 403);
const home = await worker.fetch(new Request("https://w.dev/"), env);
check("the scheduler's home page still answers", home.status === 200);

console.log(fails ? `\n${fails} FAILED` : "\nALL PASS");
process.exit(fails ? 1 : 0);
