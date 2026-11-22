/**
 * Bazaar Radar — Cloudflare Worker: on-time scheduler (v1.4x) + shopper bot and join counter (v1.60).
 *
 * 1. SCHEDULER. GitHub's own `schedule:` often starts runs 15–60 min late. A `workflow_dispatch` request starts a run
 *    within ~1 min, so this Worker fires on Cloudflare's punctual cron and asks GitHub to start the workflow.
 *    GitHub's schedule stays as a BACKUP; the Python side skips duplicates.
 * 2. SHOPPER BOT (v1.60, optional). Telegram webhook at /tg. Anyone can send the bot an Amazon.in link or ASIN and get
 *    back what we observed on the bestseller lists (days on the list, rank now and a week ago, climbing / steady /
 *    falling), a tagged link and the channel join link. Commands: /start /help /check /top /pro. It answers from the
 *    daily radar_index.json the Python run pushes to the data-archive branch. No prices, no forecasts.
 * 3. JOIN COUNTER (v1.60). With the webhook set, Telegram sends chat_member updates here: joins are counted per
 *    invite-link name in KV, and /stats serves them (and bot usage) to the Python growth job.
 * 4. OWNER DESK (v1.70). In a private chat with the bot, the OWNER (OWNER_CHAT_ID, else TG_CHAT_ID) runs the business:
 *      /floor            tonight's Live Floor report        /leads   sellers to contact, messages ready to copy
 *      /lead L1a2b call  move a lead (contacted, replied, call, no, stop, paid pilot, email a@b.in, note …)
 *      /pilot add B0… 210 · /order P1 2 499 · /cancel P1 1 · /return P1 1 · /pilot stop P1   drop-ship pilots
 *      /money ek-deals 3 140                                a programme with no report file
 *      /audit … · /quote … · /audits                       v1.80 done-for-you sourcing & margin audits
 *      /deal <link> <line> · /card <link> <name> | <line> · /queue    v1.80 savings & card offers (posted by the drip)
 *      send a CSV file   the Associates tracking-ID report (caption may carry its dates: 2026-11-16 to 2026-11-22)
 *      /refresh          run the Operator now instead of waiting for 22:00
 *    Commands and files are queued in KV (60 days); the Operator reads them from /events, applies them, and pushes
 *    the new /floor and /leads back through /push. Everyone else gets the shopper bot as before.
 *
 * Secrets (wrangler secret put …):
 *   GH_TOKEN   fine-grained PAT, this repo only: "Actions: Read and write" + (bot) "Contents: Read"
 *   optional:  TG_BOT_TOKEN + TG_CHAT_ID (admin chat) → alerts, and /pro sign-ups are forwarded there
 *              RUN_KEY → /run, /setup and /stats are protected by it
 *   bot:       BOT_TOKEN (the public bot, e.g. @OneZeroDeals_bot; falls back to TG_BOT_TOKEN),
 *              TG_WEBHOOK_SECRET (any random string; Telegram sends it back on every update)
 *   owner:     OWNER_CHAT_ID (your own Telegram user id; falls back to TG_CHAT_ID)
 * Vars (wrangler.toml): GH_OWNER, GH_REPO, GH_REF; bot: AFF_TAG_BOT (tracking ID for bot links), CHANNEL_JOIN_URL
 *   (the 'bot' invite link from GROWTH_LINKS), INDEX_PATH (default bot/radar_index.json), INDEX_REF (data-archive)
 * KV (optional): binding RADAR_KV — without it the bot still answers, but joins and usage are not counted.
 * Setup once:  https://<worker>.workers.dev/setup?key=<RUN_KEY>  → points the bot's webhook here.
 */

// cron expression (UTC, must match wrangler.toml exactly) → workflow file to start
const JOBS = {
  "15 2 * * *":       "trend_fetcher.yml",  // 07:45 IST daily fetch
  "30 3-15/2 * * *":  "drip.yml",           // 09:00, 11:00 … 21:00 IST affiliate posts
};

const MANUAL = ["operator.yml", "vendor.yml"];   // v1.70: started on demand (/refresh, /run), not by this cron

export async function dispatch(env, workflow) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GH_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bazaar-radar-scheduler",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: env.GH_REF || "main" }),
    });
    if (r.status === 204) return { ok: true, status: 204 };
    const text = (await r.text()).slice(0, 300);
    if (r.status < 500 || attempt === 3) return { ok: false, status: r.status, text };
    await new Promise(res => setTimeout(res, 2000 * attempt));   // retry GitHub 5xx only
  }
}

async function alert(env, msg) {
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return;
  await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TG_CHAT_ID, text: msg }),
  });
}

export default {
  async scheduled(event, env, ctx) {
    const wf = JOBS[event.cron];
    if (!wf) { await alert(env, `⏰ Scheduler: unknown cron "${event.cron}" — check JOBS in worker.js`); return; }
    const res = await dispatch(env, wf);
    console.log(`${event.cron} → ${wf}: ${res.status}`);
    if (!res.ok) {
      await alert(env, `⏰ Scheduler could not start ${wf} (GitHub ${res.status}). ` +
        (res.status === 401 || res.status === 403 ? "GH_TOKEN expired or lacks 'Actions: write'. " : "") +
        (res.status === 404 || res.status === 422 ? "Check GH_OWNER/GH_REPO/GH_REF and that the workflow has workflow_dispatch. " : "") +
        `The GitHub backup schedule will still run (later). ${res.text || ""}`);
    }
  },

  async fetch(request, env, ctx) {
    const u = new URL(request.url);
    if (u.pathname === "/tg") return handleTelegram(request, env, ctx);
    if (u.pathname === "/stats") return handleStats(u, env);
    if (u.pathname === "/setup") return handleSetup(u, env);
    if (u.pathname === "/push") return handlePush(request, u, env);
    if (u.pathname === "/events") return handleEvents(u, env);
    if (u.pathname !== "/run") return new Response("Bazaar Radar scheduler is running.\n", { status: 200 });
    // manual test: /run?wf=drip.yml&key=RUN_KEY
    if (!env.RUN_KEY || u.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
    const wf = u.searchParams.get("wf");
    if (!Object.values(JOBS).includes(wf) && !MANUAL.includes(wf)) return new Response("unknown workflow\n", { status: 400 });
    const res = await dispatch(env, wf);
    return new Response(JSON.stringify(res) + "\n", { status: res.ok ? 200 : 502, headers: { "Content-Type": "application/json" } });
  },
};

// ══════════════════════════════════════════════════════════════════════
// v1.60 SHOPPER BOT + JOIN COUNTER
// ══════════════════════════════════════════════════════════════════════
const DISCLOSURE = "#ad · As an Amazon Associate I earn from qualifying purchases.";
const ASIN_RX = /(?:\/dp\/|\/gp\/product\/|\/gp\/aw\/d\/|\/product\/|[?&]asin=)([A-Z0-9]{10})(?=[/?&#]|$)/i;
const BARE_ASIN = /^\s*(B0[A-Z0-9]{8}|\d{9}[\dX])\s*$/i;
const SHORT_HOSTS = /^(?:https?:\/\/)?(?:www\.)?(amzn\.in|amzn\.to|a\.co|amzn\.eu)\//i;
let INDEX = null, INDEX_AT = 0;

export function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

export function istDate(ts = Date.now()) { return new Date(ts + 19800000).toISOString().slice(0, 10); }

export async function extractAsin(text, fetchFn = fetch) {
  const t = String(text || "").trim();
  const bare = t.match(BARE_ASIN);
  if (bare) return bare[1].toUpperCase();
  const urlm = t.match(/https?:\/\/\S+/i);
  let url = urlm ? urlm[0] : "";
  if (!url) return null;
  for (let hop = 0; hop < 4; hop++) {
    const m = url.match(ASIN_RX);
    if (m) return m[1].toUpperCase();
    if (!SHORT_HOSTS.test(url) && !/amazon\./i.test(url)) return null;
    let r;
    try { r = await fetchFn(url, { method: "GET", redirect: "manual" }); } catch (e) { return null; }
    const loc = r.headers && (r.headers.get ? r.headers.get("location") : r.headers.location);
    if (!loc) return null;
    url = new URL(loc, url).toString();
  }
  return null;
}

async function loadIndex(env, fetchFn = fetch) {
  if (INDEX && Date.now() - INDEX_AT < 3600e3) return INDEX;
  const path = env.INDEX_PATH || "bot/radar_index.json";
  const ref = env.INDEX_REF || "data-archive";
  const r = await fetchFn(`https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/${path}?ref=${ref}`, {
    headers: { "Authorization": `Bearer ${env.GH_TOKEN}`, "Accept": "application/vnd.github.raw+json",
               "User-Agent": "bazaar-radar-bot", "X-GitHub-Api-Version": "2022-11-28" } });
  if (r.status !== 200) throw new Error(`index ${r.status}`);
  INDEX = await r.json(); INDEX_AT = Date.now();
  return INDEX;
}
export function _resetIndex() { INDEX = null; INDEX_AT = 0; }

function link(env, asin) {
  const tag = env.AFF_TAG_BOT || env.AFF_TAG || "";
  return `https://www.amazon.in/dp/${asin}` + (tag ? `?tag=${encodeURIComponent(tag)}` : "");
}

function footer(env) {
  return (env.CHANNEL_JOIN_URL ? `\n\n📣 A daily list of products that last: ${esc(env.CHANNEL_JOIN_URL)}` : "") + `\n\n<i>${esc(DISCLOSURE)}</i>`;
}

export function describe(ix, asin, env) {
  const it = ix.items && ix.items[asin];
  const cats = ix.cats || {};
  const view = `👉 <a href="${esc(link(env, asin))}">View on Amazon.in</a>`;
  if (!it) {
    return `We have not seen <b>${esc(asin)}</b> on the Amazon.in bestseller lists we track in the last ${ix.window || 14} days.\n` +
      `That does not mean it is bad — only that it is not a bestseller in those categories right now.\n\n${view}` + footer(env);
  }
  const cat = esc(cats[it.c] || it.c || "its category");
  const days = it.d === it.w ? `${it.d} days on the list` : `${it.d} of ${it.w} days on the list`;
  const lines = {
    durable: `🏆 #${it.r} in ${cat} · ${days}`,
    climbing: `📈 Climbed #${it.r7} → #${it.r} in ${cat} this week · ${days}`,
    falling: it.x ? `📉 Slipping in ${cat}: ${esc(it.x)} · ${days}`
                  : it.r7 ? `📉 Slipping: #${it.r7} → #${it.r} in ${cat} · ${days}` : `📉 Slipping in ${cat}, now #${it.r} · ${days}`,
    steady: `• #${it.r} in ${cat}` + (it.r7 ? ` (was #${it.r7} a week ago)` : "") + ` · ${days}`,
    new: `🆕 New on the ${cat} list at #${it.r}`,
    gone: `Last seen on the ${cat} list on ${esc(it.l)} · ${days}; not on it today`,
  };
  return `<b>${esc(it.t || "Product " + asin)}</b>\n${lines[it.s] || lines.steady}\n<i>Observed on Amazon.in bestseller lists, updated daily (${esc(ix.date)}). Not a forecast.</i>\n\n${view}` + footer(env);
}

export function topList(ix, query, env) {
  const cats = ix.cats || {};
  const q = String(query || "").trim().toLowerCase();
  if (!q) {
    const names = Object.keys(ix.top || {}).filter(c => (ix.top[c] || []).length).map(c => `• ${esc(cats[c] || c)}  →  /top ${esc(c)}`);
    return names.length ? `Pick a category:\n${names.join("\n")}` : "No lasting bestsellers in today's data yet.";
  }
  const slug = Object.keys(ix.top || {}).find(c => c.toLowerCase() === q || String(cats[c] || "").toLowerCase().includes(q));
  if (!slug) return `I don't know the category "${esc(query)}". Send /top to see the list.`;
  const rows = (ix.top[slug] || []).map((a, i) => {
    const it = ix.items[a] || {};
    const days = it.d === it.w ? `${it.d} days on the list` : `${it.d} of ${it.w} days`;
    return `${i + 1}. <a href="${esc(link(env, a))}">${esc(it.t || a)}</a>\n    🏆 #${it.r} · ${days}`;
  });
  return rows.length ? `🏆 <b>Lasting bestsellers — ${esc(cats[slug] || slug)}</b>\n\n${rows.join("\n")}` + footer(env)
                     : `Nothing in ${esc(cats[slug] || slug)} has lasted on the list this fortnight.`;
}

const HELP = "Send me any Amazon.in product link (or its ASIN) and I'll tell you what we saw on the bestseller lists: " +
  "how many of the last 14 days it was on the list, its rank now and a week ago, and whether it is climbing or slipping.\n\n" +
  "/top — lasting bestsellers by category\n/pro — sellers: join the Radar waitlist\n\nObserved data only: no prices, no forecasts.";

async function kvInc(env, key, ttl) {
  if (!env.RADAR_KV) return;
  const v = parseInt((await env.RADAR_KV.get(key)) || "0", 10) + 1;
  await env.RADAR_KV.put(key, String(v), ttl ? { expirationTtl: ttl } : undefined);
}

async function sha(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(s)));
  return [...new Uint8Array(b)].slice(0, 8).map(x => x.toString(16).padStart(2, "0")).join("");
}

async function countUse(env, userId, kind) {
  if (!env.RADAR_KV) return;
  const d = istDate();
  await kvInc(env, `b:${d}:requests`, 60 * 86400);
  const u = `u:${d}:${await sha(userId)}`;                 // hashed, expires in 9 days: counts people, keeps no IDs
  if (!(await env.RADAR_KV.get(u))) {
    await env.RADAR_KV.put(u, "1", { expirationTtl: 9 * 86400 });
    await kvInc(env, `b:${d}:users`, 60 * 86400);
  }
  if (kind === "pro") await kvInc(env, `b:${d}:pro`, 400 * 86400);
}

async function tgSend(env, chatId, text, fetchFn = fetch) {
  const token = env.BOT_TOKEN || env.TG_BOT_TOKEN;
  return fetchFn(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true }) });
}

export async function reply(update, env, fetchFn = fetch) {
  // chat_member: someone joined the channel → count by invite-link name
  const cm = update.chat_member;
  if (cm) {
    const was = cm.old_chat_member && cm.old_chat_member.status, now = cm.new_chat_member && cm.new_chat_member.status;
    if (["left", "kicked"].includes(was) && ["member", "administrator", "creator", "restricted"].includes(now)) {
      const name = (cm.invite_link && cm.invite_link.name) || "(public link)";
      await kvInc(env, `j:${istDate(cm.date * 1000)}:${name}`, 400 * 86400);
    }
    return null;
  }
  const msg = update.message;
  if (!msg || (!msg.text && !msg.document) || (msg.chat && msg.chat.type !== "private")) return null;   // private chats only
  if (isOwner(env, msg)) {
    const own = await ownerDesk(msg, env, fetchFn);
    if (own) return { chat_id: msg.chat.id, text: own };
  }
  if (!msg.text) return { chat_id: msg.chat.id, text: "Send an Amazon.in product link or a 10-character ASIN. /help for more." };
  const text = msg.text.trim();
  const [cmd, ...rest] = text.split(/\s+/);
  const arg = rest.join(" ");
  let out, kind = "check";
  if (/^\/(start|help)\b/i.test(cmd)) { out = HELP; kind = "help"; }
  else if (/^\/top\b/i.test(cmd)) { out = topList(await loadIndex(env, fetchFn), arg, env); kind = "top"; }
  else if (/^\/pro\b/i.test(cmd)) {
    kind = "pro";
    out = "Thanks — you're on the Radar waitlist for sellers. We'll message you here to set it up; the Radar itself " +
          "is sent by email. (We keep your Telegram username for this only. Nothing is sold or paid inside Telegram.)";
    if (env.TG_CHAT_ID) {
      const who = msg.from && msg.from.username ? "@" + msg.from.username : (msg.from && msg.from.first_name) || "someone";
      await tgSend({ ...env, BOT_TOKEN: env.TG_BOT_TOKEN || env.BOT_TOKEN }, env.TG_CHAT_ID, `🧾 Radar waitlist: ${esc(who)} sent /pro`, fetchFn);
    }
  } else {
    const asin = await extractAsin(/^\/check\b/i.test(cmd) ? arg : text, fetchFn);
    out = asin ? describe(await loadIndex(env, fetchFn), asin, env)
               : "Send an Amazon.in product link or a 10-character ASIN (like B09W5PSTBP). /help for more.";
  }
  await countUse(env, msg.from ? msg.from.id : msg.chat.id, kind);
  return { chat_id: msg.chat.id, text: out };
}

async function handleTelegram(request, env, ctx) {
  if (!env.TG_WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TG_WEBHOOK_SECRET)
    return new Response("forbidden\n", { status: 403 });
  let update;
  try { update = await request.json(); } catch (e) { return new Response("ok"); }
  try {
    const r = await reply(update, env);
    if (r) await tgSend(env, r.chat_id, r.text);
  } catch (e) {
    console.log("bot error", e && e.message);
    const chat = update.message && update.message.chat && update.message.chat.id;
    if (chat) await tgSend(env, chat, "Sorry — today's data is not available right now. Please try again in a while.");
  }
  return new Response("ok");                                     // always 200, or Telegram retries the same update
}

export async function statsJson(env, days = 60) {
  const out = { joins: {}, bot: {} };
  if (!env.RADAR_KV) return out;
  for (const [prefix, bucket] of [["j:", "joins"], ["b:", "bot"]]) {
    let cursor;
    do {
      const page = await env.RADAR_KV.list({ prefix, cursor });
      for (const k of page.keys) {
        const [, d, name] = k.name.split(":");
        if (!d || d < istDate(Date.now() - days * 86400e3)) continue;
        (out[bucket][d] ||= {})[name] = parseInt((await env.RADAR_KV.get(k.name)) || "0", 10);
      }
      cursor = page.list_complete ? undefined : page.cursor;
    } while (cursor);
  }
  return out;
}

async function handleStats(u, env) {
  if (!env.RUN_KEY || u.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
  return new Response(JSON.stringify(await statsJson(env)), { headers: { "Content-Type": "application/json" } });
}

async function handleSetup(u, env) {
  if (!env.RUN_KEY || u.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
  if (!env.TG_WEBHOOK_SECRET || !(env.BOT_TOKEN || env.TG_BOT_TOKEN))
    return new Response("set BOT_TOKEN and TG_WEBHOOK_SECRET first\n", { status: 400 });
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN || env.TG_BOT_TOKEN}/setWebhook`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: `${u.origin}/tg`, secret_token: env.TG_WEBHOOK_SECRET,
                           allowed_updates: ["message", "chat_member"], drop_pending_updates: false }) });
  return new Response(await r.text(), { status: r.status, headers: { "Content-Type": "application/json" } });
}

// ══════════════════════════════════════════════════════════════════════
// v1.70 OWNER DESK — the owner runs the business from the bot; the Operator (GitHub Actions) does the work
// ══════════════════════════════════════════════════════════════════════
const OWNER_QUEUE = /^\/(lead|pilot|order|cancel|return|money|audit|quote|deal|card)\b/i;
const OWNER_HELP = "🛠 <b>Owner desk</b>\n/floor — tonight's Live Floor report\n/leads — sellers to contact, messages ready\n" +
  "/lead L1a2b contacted | replied | call | no | stop | paid pilot | email a@b.in | note …\n" +
  "/pilot add B0XXXXXXXX 210 · /pilot ready · /pilot stop P1\n/order P1 2 499 · /cancel P1 1 · /return P1 1\n" +
  "/money ek-deals 3 140 · /money ek-cards 1 1200 — EarnKaro earnings (orders, ₹)\n" +
  "/audit new Client | product or ASIN | price · /audit email A1 x@y.in · /audit fee A1 · /audit paid A1\n" +
  "/quote A1 Supplier Name price=210 moq=100 gst=18 freight=1500 kg=0.45 dims=30x10x5 lead=7 written=yes\n" +
  "/audit hours A1 2 · /audit build A1 · /audit deliver A1 · /audits — open audits with RFQ text\n" +
  "/deal <EarnKaro link> <one line> · /card <link> <card name> | <one line> · /queue — what is waiting\n" +
  "Send the Associates tracking-ID report as a CSV file (dates in the caption)\n" +
  "/refresh — run the Operator now";
const MAX_UPLOAD = 2 * 1024 * 1024;

export function isOwner(env, msg) {
  const o = env.OWNER_CHAT_ID || env.TG_CHAT_ID;
  return !!(o && msg && msg.chat && String(msg.chat.id) === String(o));
}

async function queue(env, kind, obj) {
  if (!env.RADAR_KV) return null;
  const k = `${kind}:${new Date().toISOString()}:${Math.random().toString(36).slice(2, 7)}`;
  await env.RADAR_KV.put(k, JSON.stringify(obj), { expirationTtl: 60 * 86400 });
  return k;
}

async function readUpload(env, doc, fetchFn) {
  const token = env.BOT_TOKEN || env.TG_BOT_TOKEN;
  if ((doc.file_size || 0) > MAX_UPLOAD) throw new Error("file is over 2 MB");
  const meta = await (await fetchFn(`https://api.telegram.org/bot${token}/getFile?file_id=${encodeURIComponent(doc.file_id)}`)).json();
  if (!meta.ok) throw new Error(meta.description || "getFile failed");
  const r = await fetchFn(`https://api.telegram.org/file/bot${token}/${meta.result.file_path}`);
  return await r.text();
}

export async function ownerDesk(msg, env, fetchFn = fetch) {
  if (msg.document) {
    if (!env.RADAR_KV) return "Uploads need the RADAR_KV namespace (see wrangler.toml).";
    const name = msg.document.file_name || "report";
    if (!/\.(csv|tsv|txt)$/i.test(name)) return `I read CSV, TSV or TXT reports. ${esc(name)} was not saved.`;
    try {
      const text = await readUpload(env, msg.document, fetchFn);
      await queue(env, "up", { name, caption: msg.caption || "", text });
      return `📥 Got ${esc(name)} (${text.split("\n").length} lines). The Operator reads it tonight, or now with /refresh.`;
    } catch (e) { return `Could not read ${esc(name)}: ${esc(e.message)}`; }
  }
  const text = (msg.text || "").trim();
  if (/^\/(owner|desk)\b/i.test(text)) return OWNER_HELP;
  if (/^\/floor\b/i.test(text)) {
    const f = env.RADAR_KV && await env.RADAR_KV.get("push:floor");
    return f ? f.slice(0, 4000) : "No Live Floor report yet. It arrives after the 22:00 run, or now with /refresh.";
  }
  for (const [rx, key, empty] of [[/^\/audits\b/i, "audits", "No open audits yet."],
                                  [/^\/queue\b/i, "queue", "Nothing queued. /deal <link> <line> adds a savings offer."]]) {
    if (rx.test(text)) {
      const f = env.RADAR_KV && await env.RADAR_KV.get(`push:${key}`);
      return f ? f.slice(0, 4000) : empty;
    }
  }
  if (/^\/leads\b/i.test(text)) {
    const f = env.RADAR_KV && await env.RADAR_KV.get("push:leads");
    return f ? f.slice(0, 4000) : "No leads yet. They come with Monday's vendor run.";
  }
  if (/^\/pilot\s+ready\b/i.test(text)) {
    const f = env.RADAR_KV && await env.RADAR_KV.get("push:ready");
    return f || "The readiness checklist arrives with the first Operator run (/refresh).";
  }
  if (/^\/refresh\b/i.test(text)) {
    const res = await dispatch(env, "operator.yml");
    return res.ok ? "⏳ Running the Operator now — the Live Floor report arrives in about 2 minutes."
                  : `Could not start the Operator (GitHub ${res.status}). The 22:00 run still happens.`;
  }
  if (OWNER_QUEUE.test(text)) {
    if (!env.RADAR_KV) return "Commands need the RADAR_KV namespace (see wrangler.toml).";
    await queue(env, "ev", { text });
    return `✍️ Queued: ${esc(text.slice(0, 120))}\nApplied tonight, or now with /refresh.`;
  }
  return null;                                                 // anything else: the normal shopper bot answers
}

export async function eventsJson(env) {
  const out = { events: [], uploads: [] };
  if (!env.RADAR_KV) return out;
  for (const [prefix, bucket] of [["ev:", "events"], ["up:", "uploads"]]) {
    let cursor;
    do {
      const page = await env.RADAR_KV.list({ prefix, cursor });
      for (const k of page.keys) {
        const v = await env.RADAR_KV.get(k.name);
        if (!v) continue;
        try { out[bucket].push({ k: k.name, ...JSON.parse(v) }); } catch (e) { /* skip a damaged entry */ }
      }
      cursor = page.list_complete ? undefined : page.cursor;
    } while (cursor);
  }
  return out;
}

async function handleEvents(u, env) {
  if (!env.RUN_KEY || u.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
  return new Response(JSON.stringify(await eventsJson(env)), { headers: { "Content-Type": "application/json" } });
}

export async function pushJson(env, body) {
  if (!env.RADAR_KV) return { ok: false, why: "no KV" };
  const saved = [];
  for (const key of ["floor", "leads", "ready", "audits", "queue"]) {
    if (typeof body[key] === "string") { await env.RADAR_KV.put(`push:${key}`, body[key].slice(0, 12000)); saved.push(key); }
  }
  return { ok: true, saved };
}

async function handlePush(request, u, env) {
  if (!env.RUN_KEY || u.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
  if (request.method !== "POST") return new Response("POST only\n", { status: 405 });
  let body;
  try { body = await request.json(); } catch (e) { return new Response("bad json\n", { status: 400 }); }
  return new Response(JSON.stringify(await pushJson(env, body)), { headers: { "Content-Type": "application/json" } });
}
