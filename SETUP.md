# Cloudflare Worker scheduler: setup (about 10 minutes, free)

## 1. GitHub token (lets the Worker start your workflows)
GitHub → your profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
- Name: `bazaar-radar-scheduler` · Expiration: 1 year (set a reminder to renew it)
- Repository access: **Only select repositories → Trend_backtest**
- Permissions → Repository permissions → **Actions: Read and write**. Nothing else is needed.
- Copy the token. You'll paste it into Cloudflare in step 3, never into chat or code.

## 2. Create the Worker (in the browser, no installs)
dash.cloudflare.com → sign up (free) → **Workers & Pages → Create → Create Worker** → name it `bazaar-radar-scheduler` → **Deploy** → **Edit code** → delete the sample, paste all of `worker.js` → **Deploy**.

## 3. Settings for the Worker
Worker → **Settings → Variables and Secrets → Add**:
| Type | Name | Value |
|---|---|---|
| Text | GH_OWNER | OneZero-Network |
| Text | GH_REPO | Trend_backtest |
| Text | GH_REF | main *(or master; it's the branch shown on your repo's front page)* |
| Secret | GH_TOKEN | the token from step 1 |
| Secret | TG_BOT_TOKEN | your **admin** bot token (optional, for failure alerts) |
| Secret | TG_CHAT_ID | your admin chat id (optional) |
| Secret | RUN_KEY | any long random word (optional, for the test link) |

## 4. Schedules
Worker → **Settings → Trigger events (Cron Triggers) → Add**, twice, exactly:
- `15 2 * * *`: daily fetch at 07:45 IST
- `30 3-15/2 * * *`: posts at 9, 11, 1, 3, 5, 7, 9 IST

(Cloudflare cron times are UTC; IST is UTC+5:30.)

## 5. Test
Open `https://bazaar-radar-scheduler.<your-subdomain>.workers.dev/run?wf=drip.yml&key=<RUN_KEY>` in a browser.
- `{"ok":true,"status":204}` means it worked: GitHub → Actions shows a new "Affiliate drip posts" run within a minute.
- `401/403` means the token is wrong or lacks *Actions: write*. `404/422` means the owner, repo or branch name is wrong.

Worker → **Logs** shows each scheduled firing.
