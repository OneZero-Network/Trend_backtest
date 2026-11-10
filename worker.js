/**
 * Bazaar Radar — on-time scheduler (Cloudflare Worker).
 *
 * GitHub's own `schedule:` often starts runs 15–60 min late. A `workflow_dispatch` request starts a run
 * within ~1 min, so this Worker fires on Cloudflare's punctual cron and asks GitHub to start the workflow.
 * GitHub's schedule stays as a BACKUP; the Python side skips duplicates (daily: data already in; drip: posted
 * < 75 min ago).
 *
 * Secrets (wrangler secret put …):  GH_TOKEN  (fine-grained PAT: this repo only, "Actions: Read and write")
 *   optional: TG_BOT_TOKEN + TG_CHAT_ID (admin chat) → alert if GitHub refuses a dispatch
 *             RUN_KEY → lets you test with  https://<worker>.workers.dev/run?wf=drip.yml&key=<RUN_KEY>
 * Vars (wrangler.toml): GH_OWNER, GH_REPO, GH_REF
 */

// cron expression (UTC, must match wrangler.toml exactly) → workflow file to start
const JOBS = {
  "15 2 * * *":       "trend_fetcher.yml",  // 07:45 IST daily fetch
  "30 3-15/2 * * *":  "drip.yml",           // 09:00, 11:00 … 21:00 IST affiliate posts
};

async function dispatch(env, workflow) {
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

  // manual test: /run?wf=drip.yml&key=RUN_KEY
  async fetch(request, env) {
    const u = new URL(request.url);
    if (u.pathname !== "/run") return new Response("Bazaar Radar scheduler is running.\n", { status: 200 });
    if (!env.RUN_KEY || u.searchParams.get("key") !== env.RUN_KEY) return new Response("forbidden\n", { status: 403 });
    const wf = u.searchParams.get("wf");
    if (!Object.values(JOBS).includes(wf)) return new Response("unknown workflow\n", { status: 400 });
    const res = await dispatch(env, wf);
    return new Response(JSON.stringify(res) + "\n", { status: res.ok ? 200 : 502, headers: { "Content-Type": "application/json" } });
  },
};
