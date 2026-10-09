# BrightReach Missed-Call Text-Back: install status (MOCK mode)

> **Current status (2026-10-03 08:25 PT):** running repo commit **b0fbe90** (PR #10) in MOCK mode. All 9 workflows are imported unchanged from the repo and active on port 5679. The review-link 404 and the backup.ps1 UTF-8 bug are fixed and verified, and revenue_reports.status now becomes `sent`. No MCTB repo fixes are outstanding (see "Re-install on b0fbe90"). Earlier sections are kept for history.

Install and verification done 2026-10-03, 00:35–00:44 PT, on DESKTOP-BU7O72U (Windows mini PC).
Repo: C:\Users\Admin\cloud-ai-os @ adde87b (PR #5 merged). Mode: **TWILIO_MODE=mock**. No Twilio credentials, no paid services, no tunnel, nothing exposed publicly. Outreach tables and data were not touched (public schema still has 41 tables).

## What's installed and running
- **Git:** fast-forwarded b29940d → adde87b. Local uncommitted mini-PC ops changes were stashed and then restored: README.md, infra/docker/docker-compose.yml (restart policy and healthcheck), plus 6 untracked ops files. Nothing was discarded.
- **Postgres** (existing `cloudos-db-1`, db `cloudos`): applied `sql/001_schema.sql`, which created schema `mctb` with 9 tables: tenants, calls, conversations, messages, outbound_log, outbound_queue, owner_notifications, estimates, suppressions. I used the existing stack, not the dedicated compose.
- **n8n** (existing `cloudos-n8n-1`, n8n 2.41.6, host port **5679**, not 5678):
  - Added to the repo-root `.env`, which is untracked: `TWILIO_MODE=mock`, empty `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`, `MCTB_PUBLIC_BASE_URL=http://localhost:5679`, empty `OWNER_ALERT_WEBHOOK_URL`. Backup (kept outside the repo so it can't be committed): `C:\Users\Admin\mctb-install\env.bak-pre-mctb-20261003`. Compose already had `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and `GENERIC_TIMEZONE=America/Chicago`.
  - Recreated only the n8n container with `docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --no-deps n8n`, which is the form docs/MINI_PC_OPERATIONS.md uses. The `--env-file` matters: without it AGENT_API_TOKEN comes out blank. With it, n8n's token matches agent-api's.
  - Credential **`BrightReach Postgres`** (id `BrPgMctbCred0001`, host `db`:5432, db/user `cloudos`, SSL disabled) was created through `n8n import:credentials`.
  - Imported all 6 workflows with `n8n import:workflow`, published them with `n8n publish:workflow`, and restarted n8n. All 6 are active: Missed Call (`/webhook/mctb-voice`), Dial Result (`/webhook/mctb-dial-status`), Inbound SMS (`/webhook/mctb-sms`), Estimate Follow-ups (every minute plus `/webhook/mctb-followups-run`), Dashboard (`/webhook/mctb-dashboard`), Dashboard Actions (`/webhook/mctb-action`). The credential is attached to all 11 Postgres nodes.
- **Demo tenant:** slug `demo-plumbing`, business "Demo Plumbing Co", Twilio number +15555550100, owner "Demo Owner" at +15555550199, America/Chicago, forward mode. Bash/psql/openssl aren't on the Windows host, so I inserted it with the same SQL as setup_tenant.sh via `docker exec psql`, using a 24-byte random token.
- **Demo dashboard (localhost only):** http://localhost:5679/webhook/mctb-dashboard?token=17aac3da08764deb4b5a0071bde1f6d264e9572749087dbf
  (This link works as a password. Don't commit it. It's a fake demo tenant.)

## Product bug found and patched in the installed copies (repo is NOT changed)
**The workflows as merged don't work in real n8n.** Each Postgres node runs `SELECT jsonb_build_object('ctx', fn(...))`. Real n8n returns that row as `{"jsonb_build_object": {...}}`, but the Code nodes read `$json.ctx`, `$json.work`, `$json.result`, `$json.snap` and `$json.tenant_pack`. The first missed call therefore returned an empty `<Response></Response>` and wrote no rows. The n8n execution still showed "success", so the failure was silent.
The repo test simulator (`tests/sim.js`) hides this because it parses the psql value directly, so `npm test` passes.
**Fix applied to the installed copies:** each of the 11 queries now reads `SELECT fn(...) AS "<key>"`, for example `SELECT mctb.load_voice_context($1::text,$2::text,$3::text) AS "ctx"`. Patched JSONs are saved at `C:\Users\Admin\mctb-install\patched-workflows\`.
**Proposed repo fix (not pushed):** in `scripts/build_workflows.js`, emit `SELECT <fn>(...) AS "<key>"`. In `tests/sim.js`, wrap Postgres-node SQL as `SELECT row_to_json(q) FROM (<query>) q` so the simulator returns n8n's row shape. Then rebuild the workflows.

## Simulated end-to-end result (real HTTP POSTs to the local n8n webhooks, Twilio form fields)
1. **Missed call** from +15555550123 to +15555550100 at `/webhook/mctb-voice` returned 200 `text/xml`: `<Say>Thanks for calling Demo Plumbing Co. Sorry we missed you…</Say><Message to="+15555550123">Hi, this is Demo Plumbing Co. Sorry we missed your call… Reply STOP to opt out.</Message><Message to="+15555550199">Missed call at Demo Plumbing Co from +15555550123. We texted them back.</Message><Hangup/>`. That wrote a `mctb.calls` row (missed=t, text_sent=t), two `outbound_log` rows (provider/status `twiml`), and an owner_notifications row.
2. **Caller replies** at `/webhook/mctb-sms`:
   - "My water heater is leaking in the basement" → asks for the ZIP.
   - "60601" → asks how urgent.
   - "today" → "Got it. Demo Owner… will follow up shortly", and the owner gets "Qualified lead… Need/Area: 60601/Urgency: today/Reply: REPLY +15555550123 …".
   The `mctb.conversations` row ends `handed_off` with need, location and urgency filled in.
3. **STOP:** the caller gets the unsubscribe confirmation and the owner is told they opted out. A `mctb.suppressions` row (reason `stop`) was written. **A second call from the same number** got no text to the caller; only the owner alert went out ("…opted out of texts. Call them by phone. Do not text them."). `calls.suppressed_reason=opted_out`, `text_sent=f`.
4. **Estimate follow-up (mock_sent):** the owner texted `ESTIMATE Pat Keller | 5555550124 | water heater replacement | 1850` and got a confirmation. To avoid waiting until 8:00 am CT, I moved the demo estimate's next_send_at into the past and triggered `/webhook/mctb-followups-run` with a simulated daytime `now`. Result: `outbound_log` has the follow-up "Hi Pat Keller, this is Demo Plumbing Co. Following up on your water heater replacement estimate for $1,850.00…" with **provider=mock, status=mock_sent**. The estimate moved to step 1 with its next text at 2026-10-05 10:00 CT.
5. **Dashboard:** with the right token, `/webhook/mctb-dashboard` returns 200 HTML showing missed calls 2, recovered replies 1, texts sent 13, open estimates 1, opt-outs 1, plus calls, leads, estimates and owner-feed tables. A bad token gets "This dashboard link is not valid." The dashboard "Text a customer" action produced a `mock_sent` owner_reply row.
6. n8n executions: every one succeeded (voice 3, sms 5, followups 7+, dashboard 3, actions 2).
7. `node --test tests/engine.test.js` on the mini PC: 16/16 pass. I couldn't run `tests/product.test.js` on Windows because it needs `sudo -u postgres` on Linux.

## Open issues to fix before a real client
- **Compliance bug:** manual replies (dashboard "Text a customer" and the owner SMS `REPLY` command) do **not** check `mctb.suppressions`. In the test, a reply to the opted-out +15555550123 was recorded as `mock_sent`. engine.js L1063 (dashboard action) and L625 `pushIfAllowed` (owner REPLY) need a suppression check. Twilio's default Advanced Opt-Out would probably reject this in live mode (error 21610), but the product shouldn't rely on that.
- The bug above (Postgres output shape) has to be fixed in the repo (build script plus sim) so a fresh import works without the local patch.
- Minor: "Qualified leads, 30 days" shows 0 after a qualified lead later opts out, because it counts the current conversation state.

## What remains for a real client go-live (all need Matt or the client; nothing here buys anything)
1. Merge the repo fix for the query shape and the suppression check, then re-import the workflows. Or reuse the patched copies plus the suppression fix.
2. The client's Twilio account: buy one local number (about $1.15/mo) and register an A2P 10DLC brand and campaign (Low-Volume Standard or Sole Prop) using the RUNBOOK §7 text. Wait for approval.
3. Public HTTPS: a free Cloudflare named tunnel on a hostname Matt controls, forwarding only `/webhook/`. Set `MCTB_PUBLIC_BASE_URL` to it and recreate n8n. Use a strong n8n owner password.
4. Point the Twilio number's voice webhook at `https://HOST/webhook/mctb-voice` and SMS at `https://HOST/webhook/mctb-sms` (POST).
5. Add the real shop (setup_tenant or the equivalent SQL) and text the owner the dashboard link. Optionally deactivate or delete the demo tenant (`UPDATE mctb.tenants SET active=false WHERE slug='demo-plumbing'`).
6. Set `TWILIO_MODE=live` plus the SID and token in `.env` only (never in git), then recreate n8n with `--env-file .env`.
7. Set up conditional call forwarding (no-answer/busy) on the shop's carrier, keeping ring time under the voicemail timer. Then run the RUNBOOK §8 live proof: a real missed call, reply, STOP, and estimate follow-up.

## Files and rollback
- Patched workflow JSONs: `C:\Users\Admin\mctb-install\patched-workflows\`. Dashboard snapshot: `C:\Users\Admin\mctb-install\demo-dashboard-snapshot.html`.
- Rollback: unpublish the 6 workflows (`n8n unpublish:workflow --id=…`), `DROP SCHEMA mctb CASCADE` (affects only the product), restore `.env` from `C:\Users\Admin\mctb-install\env.bak-pre-mctb-20261003`, then recreate n8n.


## Re-install on ae06b06 (2026-10-03, about 00:47-01:10 PT)
Clock note: the mini PC's Windows and Docker clocks run about 9-10 minutes behind the box. DB timestamps come from the mini PC clock.

**Steps**
1. **Git:** stashed the tracked local changes (README.md, infra/docker/docker-compose.yml) and ran `git merge --ff-only origin/master`: adde87b -> **ae06b06**. Then popped the stash. Nothing was discarded. INSTALL_STATUS.md and the 6 mini-PC ops files stay untracked. The stash list is empty.
2. **Schema:** reviewed the diff first. It's additive: it adds `qualified_at` with a backfill, adds `suppressed_phones` to the context functions, makes `apply_outbound` skip opted-out numbers except for stop/help/start confirmations, and bases the leads count on `qualified_at`. Re-applied `sql/001_schema.sql` to `cloudos-db-1` with ON_ERROR_STOP and exit 0. Public schema is unchanged (41 tables). The only schemas are `public` and `mctb`. The demo tenant and dashboard token are unchanged.
3. **Workflows:** copied the repo `workflows/` folder byte-for-byte into the container (md5 matches the repo files) and ran `n8n import:workflow --separate`, which overwrote the same 6 ids in place. n8n automatically relinked the repo's placeholder credential id to the existing **BrightReach Postgres** credential (`BrPgMctbCred0001`) by name. I checked all 11 Postgres nodes in the n8n DB and every one has that credential and the new `SELECT fn(...) AS "ctx"/"work"/"snap"/"tenant_pack"/"result"` queries. Then ran `n8n publish:workflow` for all 6 and `docker restart cloudos-n8n-1`. All 6 are active and healthy on host port 5679. `TWILIO_MODE=mock` and `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` are still set in the process env.
4. **E2E test:** the script is saved at `C:\Users\Admin\mctb-install\e2e_ae06b06.ps1`. It takes fresh caller and estimate numbers as parameters. I ran it with caller +15555550131 / estimate +15555550132, then again with +15555550141 / +15555550142.

**Results** (from run 2 with +15555550141, cross-checked against run 1)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Missed call -> TwiML text to caller, call row | **PASS** | Caller TwiML "Hi, this is Demo Plumbing Co...". `calls` missed=t, text_sent=t. (The script printed FAIL for two reasons: my boolean format, and the owner SMS being capped in run 2. See issue A.) |
| 2 | Qualifying replies (need -> ZIP -> urgency) -> handed off | **PASS** | conversation handed_off, need "Kitchen sink is clogged and backing up", ZIP 75201, urgency today, `qualified_at` set. The owner "Qualified lead" SMS came through in run 1. In run 2 it went only to the dashboard Owner feed because of issue A. |
| 3 | STOP -> confirmation + suppression row | **PASS** | "You are unsubscribed...", `suppressions.reason=stop` |
| 4 | Dashboard "Text a customer" to opted-out number -> refused, not logged | **PASS** | Page: "+15555550141 opted out of texts. Not sent. Call them by phone. Do not text them." 0 outbound_log rows, 0 outbound_queue rows |
| 5 | Owner `REPLY +1555...` to opted-out number -> refused, not logged | **PASS** | Owner TwiML: "+15555550141 opted out of texts. Not sent...". No message to the caller. outbound_log rows to the caller stayed at 5 before and after |
| 6 | Second call after STOP -> no caller text, owner told | **PASS** | `text_sent=f`, `suppressed_reason=opted_out`, and no caller message in the TwiML |
| 7 | Qualified-leads count stays > 0 after opt-out | **PASS** | 3 before STOP, 3 after (run 1: 2 -> 2) |
| 8 | Control: dashboard reply to a number that has NOT opted out | **PASS** | provider/status = mock/mock_sent |
| 9 | Estimate follow-up in mock mode | **PASS** | Owner "ESTIMATE Jordan Reyes / 5555550142 / sewer line repair / 2400" was saved. I moved that demo estimate's send time into the past and ran `/webhook/mctb-followups-run` with a daytime `now`. Follow-up logged as mock/mock_sent, estimate moved to step 1 |
| 10 | Dashboard loads; bad token refused | **PASS** | 200 HTML. Stats: missed calls 6, recovered replies 3, qualified leads 3, texts 39, open estimates 2, opt-outs 3. A bad token gets "This dashboard link is not valid." |
| U1 | `node --test tests/engine.test.js` (mini PC, Node 24.19) | **PASS** | 17/17, including "manual sends to opted-out numbers are refused" |
| U2 | `tests/product.test.js` | **PASS (on box)** | Can't run on Windows (needs Linux `sudo -u postgres`). On the box (Linux, fresh ae06b06 clone), `npm test` gave 19/20: test 20 passes all its assertions, then fails writing to the hardcoded `/opt/cursor/artifacts`. With that path pointed at /tmp it gives **20/20**. Throwaway clone and `mctb_test` DB were deleted afterwards |

**Still broken / new findings**
- **A. Owner SMS alerts share the 12-per-number daily cap (`per_number_daily_limit`).** Today the owner phone received 12 `owner_notify` texts, and after that missed-call, reply and qualified-lead alerts stopped going to the owner by SMS. They still appear in the dashboard Owner feed (`mctb.owner_notifications`). Refusal and estimate confirmations (`owner_confirm`) still went through. Each lead produces about 4 owner alerts (missed call, 2 reply relays, qualified lead), so **a shop with 3 or more leads in a day stops getting owner SMS alerts without any warning**. Suggested fix: exempt the owner phone from the per-number cap or give it its own higher cap (e.g. `owner_daily_limit` 100), and send one "alerts paused, check the dashboard" text when it's reached. Stopgap: `UPDATE mctb.tenants SET per_number_daily_limit = 50 WHERE slug = '...'`. That also raises the per-customer cap, so it isn't ideal.
- **B.** `tests/product.test.js` line 427 hardcodes `/opt/cursor/artifacts`. It should use an env var or the OS temp dir.
- **C.** Leftover test data: the earlier pre-fix run left one mock_sent owner_reply row to the opted-out +15555550123. It's mock only, nothing was sent, and I kept it for history.
- **D.** The mini PC's clock is about 9-10 minutes slow. That matters for quiet hours and follow-up timing. Turn on Windows time sync before go-live.

**Sales demo:** live at https://mattumali579.github.io/office-agent-demo/missed-call/ (office-agent-demo PR #1 merged as 277f000).

**Final go-live checklist** (needs Matt or the client; nothing here was bought or enabled)
- [x] Schema `mctb` applied (ae06b06)
- [x] Six workflows imported from the repo, BrightReach Postgres credential attached, all active (mock)
- [x] Mock E2E proven: missed call, qualify, STOP, opt-out refusals (dashboard and owner REPLY), follow-up, dashboard
- [ ] Fix or configure issue A (owner alert cap) before a real shop depends on owner SMS
- [ ] Turn on Windows time sync on the mini PC (issue D)
- [ ] The client's Twilio account: buy one local number (about $1.15/mo, PAID)
- [ ] A2P 10DLC brand and campaign (Low-Volume Standard or Sole Prop; about $4 + $15 one-time plus a monthly campaign fee, PAID), using the RUNBOOK section 7 text. Wait for approval before calling it live
- [ ] Free Cloudflare named tunnel on a hostname Matt controls, forwarding only /webhook/ to port 5679. Set `MCTB_PUBLIC_BASE_URL`, run `docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --force-recreate n8n`, and use a strong n8n owner password
- [ ] Twilio number: voice webhook -> https://HOST/webhook/mctb-voice, SMS -> https://HOST/webhook/mctb-sms (POST)
- [ ] Add the real shop with `scripts/setup_tenant.ps1`, text the owner the dashboard link, and deactivate the demo tenant (`UPDATE mctb.tenants SET active=false WHERE slug='demo-plumbing'`)
- [ ] Put `TWILIO_MODE=live`, `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in `.env` only (never git), recreate n8n, and check with `docker exec cloudos-n8n-1 printenv TWILIO_MODE`
- [ ] Turn on conditional call forwarding (no-answer/busy) at the carrier, keeping ring time under the voicemail timer
- [ ] RUNBOOK section 8 live proof: a real missed call, reply, STOP that suppresses the next text, and an estimate moving to follow-up sent


## Re-install on 882287b (2026-10-03, about 01:14-01:22 PT)
Clock note: the mini PC and Docker clocks are still **543 s (about 9 min) slow**. DB timestamps come from that clock. Time sync could not be fixed without admin rights (see T below).

**Steps**
1. **Git:** stashed the tracked local changes and ran `git merge --ff-only origin/master`: ae06b06 -> **882287b**. Then popped the stash. Nothing was discarded. INSTALL_STATUS.md and the mini-PC ops files stay untracked. The stash list is empty.
2. **Schema:** reviewed the diff first. It's additive: it adds `tenants.owner_daily_sms_limit` (default 60, CHECK > 0), and the context functions now return `owner_daily_sms_limit`, `dashboard_path` and `owner_cap_notice_sent`. Re-applied with ON_ERROR_STOP, exit 0. Public schema is unchanged (41 tables). Demo tenant: per-customer cap 12, owner cap 60, token unchanged.
3. **Workflows:** copied the repo files into the container (md5 matches the repo working tree), ran `n8n import:workflow --separate` over the same ids, and checked the n8n DB: **11/11 Postgres nodes** have credential `BrPgMctbCred0001` (BrightReach Postgres) and the `AS "<key>"` queries. Then ran `n8n publish:workflow` x6 and `docker restart cloudos-n8n-1`. All 6 are active, n8n is healthy on 5679, `TWILIO_MODE=mock`, and `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.
4. **E2E:** new script `C:\Users\Admin\mctb-install\e2e_882287b.ps1`. I fixed the old script's formatting mistake: booleans are now rendered as explicit 't'/'f' text in SQL, and TwiML recipients are matched with an escaped helper. Fresh numbers: caller +15555550151, estimate customer +15555550152, owner-cap test callers +15555550161..163 and +15555550169.

**Results**

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Missed call -> caller text + owner alert (TwiML), call row | **PASS** | missed=t, text_sent=t. The owner had already received 17 texts in the last 24h before the run |
| 2 | Qualifying replies -> handed_off, qualified_at, owner lead alert | **PASS** | handed_off / "Water heater is not making hot water" / 75204 / today / qualified_at set. Owner got the "Qualified lead" TwiML message |
| 3 | STOP -> confirmation + suppression | **PASS** | suppression reason=stop |
| 4 | Dashboard "Text a customer" to opted-out number -> refused, not logged | **PASS** | "+15555550151 opted out of texts. Not sent. Call them by phone..." 0 log rows, 0 queue rows |
| 5 | Owner REPLY to opted-out number -> refused, owner told, not logged | **PASS** | rows to the caller 5 before, 5 after |
| 6 | Call after STOP -> no caller text, owner alerted | **PASS** | text_sent=f, suppressed_reason=opted_out |
| 7 | Qualified-leads count stays > 0 after opt-out | **PASS** | 4 before STOP, 4 after |
| 8 | Control: dashboard reply to a number that has NOT opted out | **PASS** | mock/mock_sent |
| 9 | Estimate follow-up (mock) | **PASS** | I moved that demo estimate's send time into the past and triggered the run with a daytime `now`: mock/mock_sent, estimate at step 1 |
| 10 | Dashboard loads; bad token refused | **PASS** | 200 HTML. Stats: missed 8, recovered 4, qualified 4, texts 54, open estimates 3, opt-outs 4 |
| A | Owner alerts keep arriving past 12/day | **PASS** | Owner texts in the last 24h went from 17 to 25 during the run. owner_notify in 24h = 18. This run's 6 owner alerts all went out. Note: owner alerts ride the webhook's TwiML reply, so they're logged as **`twiml`**, not `mock_sent`. In mock mode, `mock_sent` only applies to REST-API sends (follow-ups and dashboard/owner manual replies). `twiml` is the "sent" status for these. |
| B | Owner cap -> exactly one "Alerts paused" text, then dashboard only, cap restored | **PASS** | Set `owner_daily_sms_limit` to the current count + 1 (26). Call 1: owner got "Missed call at Demo Plumbing Co...". Call 2: owner got **"Alerts paused for today, see your dashboard: http://localhost:5679/webhook/mctb-dashboard?token=..."**. Call 3: nothing to the owner. Exactly **1** new `owner_cap_notice`, and 2 owner-feed rows marked `dashboard`. Callers were still texted every time. Limit restored to **60** |
| C | After restoring 60, owner alerts resume | **PASS** | next missed call -> owner "Missed call..." TwiML |
| U1 | Engine unit tests on the mini PC (Node 24.19) | **PASS** | 19/19, including "owner alerts are not cut off by the customer per-number cap" and "owner cap sends one pause text and then stays quiet" |
| U2 | Full `npm test` (engine + product, real Postgres) | **PASS 22/22** | The box shell still fails to spawn (`/usr/bin/bash ENOENT`), so I ran it in a throwaway `postgres:16-alpine` container on the mini PC (free, `--rm`, nothing kept), using Node 24.18 and Postgres 16.15. It passes 22/22 both with `MCTB_ARTIFACT_DIR` set and with the default OS temp dir. The copy needed LF normalization first. See issue W |
| T | Windows time sync (`w32tm /resync`) | **FAIL: needs admin** | The agent shell runs as `DESKTOP-BU7O72U\Admin` **not elevated**. The w32time service is Stopped/Manual. `Start-Service` and `sc start w32time` both fail with **Access is denied (5)**, and `w32tm /resync` says "service has not been started". I didn't try a UAC elevation prompt because nobody is at the machine. The clock is still 543 s slow |

**Still open**
- **T. Clock about 9 minutes slow** (Windows host and Docker). Fix it from an **elevated** PowerShell ("Run as administrator"):
  ```
  sc.exe config w32time start= auto
  net start w32time
  w32tm /config /manualpeerlist:"time.windows.com,0x9 pool.ntp.org,0x9" /syncfromflags:manual /update
  w32tm /resync /force
  w32tm /query /status
  ```
  Docker Desktop's VM usually follows the host clock after a resync. If it doesn't, restart Docker Desktop. This matters for quiet hours, follow-up timing and the 24-hour caps.
- **W. Windows checkout uses CRLF** (`core.autocrlf=true`, no `.gitattributes`). `scripts/setup_tenant.sh` has a `bash\r` shebang in this checkout, so it can't run from WSL or Docker straight off this clone. On Windows use `setup_tenant.ps1`. The workflow JSONs import fine either way: the md5-identical import worked and every n8n check passed. Suggested repo fix: add `.gitattributes` with `*.sh text eol=lf`, plus `products/missed-call-textback/** text eol=lf` for the engine/JSON files that are hash-checked.
- Design note: the "Alerts paused" text contains the dashboard token link. That's by design, since the link is the owner's password, but it means the link travels over SMS.
- Test data: the demo tenant now holds about 9 fake callers' worth of rows from several test runs, plus one `owner_cap_notice` in the last 24h. If the owner hits 60 again within 24h, no second pause text will go out, which is correct per-day behaviour. Deactivate the demo tenant before real use.

**Remaining go-live checklist** (the PAID items need Matt's approval)
- [x] Schema, 6 workflows from repo 882287b, credential on 11/11 nodes, all active (mock)
- [x] Mock E2E: missed call, qualify, STOP, opt-out refusals, owner alerts past 12, single pause notice, follow-up, dashboard. Full test suite 22/22
- [ ] Time sync from an elevated PowerShell (commands above). Needs Matt at the machine or an admin session
- [ ] Optional repo hygiene: `.gitattributes` for LF
- [ ] **PAID, needs Matt's approval:** a local number in the client's Twilio account (about $1.15/mo)
- [ ] **PAID, needs Matt's approval:** A2P 10DLC brand and campaign (Low-Volume Standard or Sole Prop; about $4 + $15 one-time plus a monthly campaign fee), using the RUNBOOK section 7 text. Wait for approval
- [ ] Free Cloudflare named tunnel (hostname Matt controls) forwarding only /webhook/ to port 5679. Set `MCTB_PUBLIC_BASE_URL` and recreate n8n with `--env-file .env --force-recreate`. Strong n8n owner password
- [ ] Twilio number webhooks: voice -> https://HOST/webhook/mctb-voice, SMS -> https://HOST/webhook/mctb-sms (POST)
- [ ] Add the real shop with `scripts/setup_tenant.ps1`, text the owner the dashboard link, and deactivate the demo tenant
- [ ] Put `TWILIO_MODE=live` plus the SID and token in `.env` only, recreate n8n, and check `printenv TWILIO_MODE`
- [ ] Turn on conditional call forwarding (no-answer/busy) at the carrier, with ring time under the voicemail timer
- [ ] RUNBOOK section 8 live proof with a real phone


## Re-install on deeefaa (2026-10-03, about 07:57-08:10 PT)
Clock note: the mini PC and Docker clocks are still about 9 min slow (no admin rights for w32time). DB and file timestamps use that clock.

**Steps**
1. **Git:** stash, then `git merge --ff-only origin/master` (882287b -> **deeefaa**), then stash pop. Nothing was discarded. INSTALL_STATUS.md and the ops files stay untracked. Stash list is empty.
2. **Schema:** reviewed the diff first. It's additive and mctb-only: new tenant columns `revenue_report_enabled` and `google_review_url`, and new tables `revenue_reports`, `review_requests`, `workflow_heartbeats` and `health_state`, plus functions. Re-applied with ON_ERROR_STOP, exit 0. Public schema still has 41 tables. The only schemas are public and mctb.
3. **Env:** appended `MCTB_ADMIN_PHONE=+15555550199` (the demo owner, so mock health alerts are visible) and an empty `MCTB_ADMIN_EMAIL=` to the repo-root `.env`. Backup: `C:\Users\Admin\mctb-install\env.bak-pre-deeefaa-20261003`. No SMTP variables and no passwords. Recreated n8n with `docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --force-recreate --no-deps n8n`. Confirmed TWILIO_MODE=mock, MCTB_ADMIN_PHONE set, N8N_BLOCK_ENV_ACCESS_IN_NODE=false, and AGENT_API_TOKEN still matches agent-api.
4. **Workflows:** copied all 9 repo files into n8n (md5 identical to the repo) and ran `n8n import:workflow --separate`. n8n relinked the credential by name: **19/19 Postgres nodes** are on `BrightReach Postgres` (BrPgMctbCred0001) with `AS "<key>"` queries. The **Send Health Email** node keeps its unresolved `BrightReach SMTP` placeholder because no SMTP credential exists in n8n, and I didn't create one. I ran `n8n publish:workflow` for all 9 and restarted n8n. **All 9 are active.**
5. **Demo tenant:** set `google_review_url = 'https://g.page/r/demo-plumbing/review'`. Token and owner cap (60) unchanged.
6. **Scripts:** `C:\Users\Admin\mctb-install\e2e_deeefaa.ps1` covers the core flow (callers +15555550181/182). `C:\Users\Admin\mctb-install\e2e_deeefaa_features.ps1` covers the new features (review phones +15555550193/194, plus +15555550195 for the corrected-URL check).

**Results**

| # | Check | Result | Evidence |
|---|---|---|---|
| 1-10 | Core e2e: missed call, qualify, STOP, dashboard and owner-REPLY opt-out refusals, post-STOP call, qualified count, control reply, estimate follow-up, dashboard | **PASS (10/10)** | Same checks as the 882287b run. Qualified count 5 -> 5 after STOP. Follow-up mock/mock_sent |
| A | Owner alerts past 12/day | **PASS** | Owner texts in the last 24h went 28 -> 36. owner_notify in 24h = 26. All delivered as `twiml` |
| a | GET /webhook/mctb-health | **PASS** | HTTP 200 `application/json`. Before revenue's first run it returned `{"ok":false,"db":"ok","version":"mctb-engine-1"}`, which is expected per the RUNBOOK. After that, `{"ok":true,"db":"ok","version":"mctb-engine-1"}` |
| b1 | Owner `DONE <phone>` schedules a review | **PASS** | review_requests row `scheduled`, send_at = now + 120 min (11:53 CT). Owner told "Review request scheduled for +15555550193." |
| b2 | Review text after send_at: mock_sent with tracking link | **PASS** | "Thanks for choosing Demo Plumbing Co. If we earned it, a Google review helps neighbors find us: http://localhost:5679/webhook/mctb-r/<64-hex token> Reply STOP to opt out." mock/mock_sent. reminder_at = sent_at + 3.00 days |
| b3 | GET the link -> 302 to review URL, click logged | **FAIL (repo bug)** | The link **as sent in the SMS** (`/webhook/mctb-r/<token>`) returns **HTTP 404**. n8n registers a webhook whose path has a `:token` parameter under its webhookId, so the live URL is **`/webhook/mctb-review-hook/mctb-r/<token>`**. That URL returns **HTTP 302 -> https://g.page/r/demo-plumbing/review**, sets `clicked_at` and status `clicked` (verified with curl on +15555550195). An unknown token returns 404 "Unknown review link". |
| b4 | No reminder after a click | **PASS on the correct URL / FAIL end-to-end** | With the click logged via the working URL, forcing reminder_at into the past sent nothing (texts 1 -> 1, status stays clicked, reminded_at null). Because the SMS link 404s, a real customer's click is never logged, so they **do** get the reminder (+15555550193 was reminded after its 404 "click") |
| b5 | Control: unclicked request gets exactly one reminder | **PASS** | +15555550194: review_request then review_reminder, both mock/mock_sent. Status `reminded`, no third text |
| b6 | Second REVIEW within 90 days is skipped | **PASS** | No new row. Owner told "A review request already went to +15555550193 in the last 90 days." |
| b7 | REVIEW to an opted-out number is skipped | **PASS** | No review row for +15555550181. Owner told "...opted out of texts. Not sent..." |
| c1 | Manual revenue report | **PASS** | POSTed `/webhook/mctb-revenue-run` with simulated `now` = Mon 2026-10-05 08:30 CT. That created `revenue_reports` week 2026-W40 and queued the owner text for that Monday time. I moved the queued row's send_at into the past and ran follow-ups: **mock/mock_sent, 215 chars**: "Demo Plumbing Co Last week / Missed 14, texted 9 / Recovered 5, leads 5 / Estimates 5, won 0 ($0.00) / Est. recovered $0.00 / <dashboard link>". The counts match the dashboard |
| c2 | Dashboard This week / This month block and review stats | **PASS** | "This week / This month": Missed calls 14/14, Callers texted 9/9, Conversations recovered 5/5, Qualified leads 5/5, Estimates sent 5/5, Jobs won 0/0, Won $0.00, Estimated revenue recovered $0.00. Plus "Review requests sent" and "Review links clicked" tiles |
| d1 | Normal health run | **PASS** | `/webhook/mctb-health-run`: health_state ok, alert_open=false. The 5-minute trigger also ran successfully |
| d2 | Simulated failure (safe and reversible) | **PASS** | I didn't stop Postgres, because the outreach system and agent-api share it. Instead I backdated the `followups` heartbeat by 3h (mctb-only; the every-minute workflow re-stamps it). Health went `alerted`, alert_open=true, and a mock/mock_sent admin text went to +15555550199: "BrightReach alert: Follow-up workflow has not succeeded in the last 10 minutes" |
| d3 | Recovery | **PASS** | After follow-ups re-stamped (65 s), the next run gave health ok, alert closed, and mock/mock_sent "BrightReach recovered. Postgres and the scheduled workflows are answering again." |
| e | `scripts/backup.ps1` | **PASS (one bug, below)** | `C:\mctb-backups\mctb-20261003-075640.sql`, **134,679 bytes**, 13 mctb tables with 13 COPY blocks. **Restore test:** loaded it into a throwaway database `mctb_restore_test` in the same container: 0 errors, row counts identical to live (1 tenant, 14 calls, 90 outbound_log, 4 reviews). Database dropped afterwards. I used `-OutDir C:\mctb-backups` because the default (`products/missed-call-textback/backups/`) is inside the repo (gitignored, but better kept outside) |
| 6 | Nightly backup task (no admin) | **PASS** | `schtasks /Create /TN "MCTB Postgres backup" /SC DAILY /ST 02:30 /RL LIMITED` succeeded **without admin**. It runs as Admin, "Interactive only" (Docker Desktop needs the user session anyway). Next run 2026-10-04 02:30 by the mini PC clock, which is about 9 min slow. A test run via `schtasks /Run` gave Last Result 0 and wrote `C:\mctb-backups\mctb-20261003-075718.sql` (134,679 bytes) |
| U1 | Engine unit tests (mini PC, Node 24.19) | **PASS** | 24/24 |
| U2 | Full `npm test` (engine + product + upgrades) | **PASS 31/31** | The box shell is still down, so I used a throwaway `postgres:16-alpine` container on the mini PC with an LF-normalized copy and GNU coreutils. The first try gave 30/31 because BusyBox `touch` doesn't accept `-d '15 days ago'` (environment only) |
| n8n | Execution health | **PASS** | Since the restart every mctb execution has succeeded: voice, sms, actions, dashboard, follow-ups (trigger and webhook), health (trigger and webhook), revenue, review |

**Repo fixes needed**
1. **Review link 404 (must fix before review texts go live).** Either (a) build the link as `{MCTB_PUBLIC_BASE_URL}/webhook/mctb-review-hook/mctb-r/<token>`, or, cleaner and shorter for SMS, (b) change the Review Click webhook to the static path `mctb-r` and read the token from a query string (`/webhook/mctb-r?t=<token>`). Then update the RUNBOOK (the tunnel must forward that path too) and teach `tests/sim.js` n8n's routing rule (a path with `:param` registers as `/webhook/<webhookId>/<path>`) so a test catches it. The 31 tests passed without catching this.
2. **`backup.ps1` corrupts non-ASCII text.** It captures `docker exec pg_dump` stdout into a PowerShell variable, which decodes with the console code page on Windows PowerShell 5.1. A probe through the same path turned `José — Café` (UTF-8) into mojibake bytes `e2 94 9c e2 8c 90...`. Today's dumps are fine only because all current data is ASCII. A real customer name like "Peña", or any message with an em dash, would be corrupted in the backup. It also writes CRLF. Fix: `docker exec <c> pg_dump ... -f /tmp/mctb.sql`, then `docker cp <c>:/tmp/mctb.sql $file`, then delete the temp file. That's byte-exact. Alternatively set `[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)` before the capture. Add a test with a non-ASCII tenant name.
3. **Minor:** `revenue_reports.status` stays `queued` after its owner text is actually sent (the outbound_queue row is `sent`). Mark the report sent in `mark_outbound`, or document that `queued` is final.
4. **Minor / test portability:** `tests/upgrades.test.js` uses GNU `touch -d '15 days ago'`, which fails on BusyBox/Alpine and macOS. Use `fs.utimesSync` instead.
5. **Still open from before:** add `.gitattributes` (`*.sh text eol=lf` plus the product files). This Windows clone still has a CRLF `setup_tenant.sh`.

**Test data left in the demo tenant (all mock):** one review request for +15555550191 is still `scheduled` (from the first, aborted run) and will be mock-sent by the scheduler at about 11:53 CT today. The week 2026-W40 revenue report is already recorded, so the real Monday 2026-10-05 08:00 CT run won't send another one for the demo tenant (by design: no repeats).

**Remaining go-live checklist** (the PAID items need Matt's approval)
- [x] deeefaa installed: schema, 9 workflows unchanged from the repo, 19/19 Postgres nodes linked, all active, mock e2e and new features tested, full test suite 31/31
- [x] Nightly 02:30 backup task to C:\mctb-backups (works without admin)
- [ ] Repo fix 1 (review link 404) before turning on `google_review_url` for a real shop. Repo fix 2 (backup encoding) before real customer data accumulates
- [ ] Time sync from an elevated PowerShell (commands in the 882287b section)
- [ ] Optional: a BrightReach SMTP credential plus N8N_SMTP_* variables for email health alerts (a free SMTP account is fine). Set `MCTB_ADMIN_PHONE` to Matt's real phone at go-live
- [ ] **PAID, Matt's approval:** a local number in the client's Twilio account (about $1.15/mo)
- [ ] **PAID, Matt's approval:** A2P 10DLC brand and campaign (about $4 + $15 one-time plus a monthly fee). Wait for approval
- [ ] Free Cloudflare named tunnel forwarding only /webhook/ to 5679. Set MCTB_PUBLIC_BASE_URL and recreate n8n with `--env-file .env --force-recreate`. Optionally add a free UptimeRobot check on /webhook/mctb-health with keyword `"ok":true`
- [ ] Twilio webhooks: voice -> /webhook/mctb-voice, SMS -> /webhook/mctb-sms (POST)
- [ ] Add the real shop with setup_tenant.ps1 (plus its google_review_url), text the owner the dashboard link, deactivate the demo tenant
- [ ] TWILIO_MODE=live plus SID and token in .env only, recreate n8n
- [ ] Conditional call forwarding at the carrier, then the RUNBOOK section 8 live proof

## Re-install on b0fbe90 (2026-10-03, about 08:15-08:25 PT)
PR #10 merged to master as b0fbe90:
- Review texts use the static path `/webhook/mctb-r?t=<token>`.
- backup.ps1 runs `pg_dump -f` inside the container, then `docker cp`.
- `revenue_reports.status` is set to sent or failed.
- `.gitattributes` forces LF (ps1 files stay CRLF).

master also carries outreach v4 changes (src/cloudos/outreach/*, config/*). I only pulled them; they aren't part of the mini-PC product install. Mode: **TWILIO_MODE=mock**. Nothing paid, nothing public.

### Steps
1. **Git:** stashed README.md and infra/docker/docker-compose.yml, ran `git merge --ff-only b0fbe90` (26 files), then `git stash pop`. The stash list is empty. The 7 untracked ops files and this INSTALL_STATUS.md were untouched, since none of their paths collide with incoming files. Nothing was discarded.
2. **Line endings:** after the merge, files the merge didn't touch were still CRLF in the worktree. Plain `git checkout -- <file>` does nothing here because the index stat is clean. So for the 7 product files that showed `attr/text eol=lf` with `w/crlf` and had no local changes, I deleted the worktree copy and then ran `git checkout -- <file>`. The files: README.md, demo/demo.js, docker-compose.yml, package.json, scripts/backup.sh, scripts/install_into_n8n.sh, scripts/setup_tenant.sh. `git ls-files --eol` now shows `i/lf w/lf` for all 7, **including setup_tenant.sh**. backup.ps1 and setup_tenant.ps1 are CRLF as intended. The other ~233 CRLF files elsewhere in the repo (outreach, docs, migrations) are outside the product install, so I left them alone. They'll normalize on their next change or on a deliberate `git add --renormalize`.
3. **Schema:** re-applied `sql/001_schema.sql` with ON_ERROR_STOP (exit 0). It adds `mctb.outbound_queue.revenue_report_id`. mctb has 13 tables; public still has 41.
4. **Workflows:** all 9 workflow JSONs changed in this PR, so I re-imported all 9 unchanged (the md5 inside the container matches the repo), published them, and restarted n8n. Results:
   - The BrightReach Postgres credential is on 19/19 Postgres nodes.
   - Send Health Email still has the unresolved `BrightReach SMTP` placeholder: no SMTP credential exists and no passwords were added.
   - All 9 workflows are active.
   - .env didn't change, so n8n wasn't recreated.

### Results
| Check | Result | Detail |
|---|---|---|
| Git fast-forward to b0fbe90, stash restored | PASS | nothing discarded |
| setup_tenant.sh is LF after a fresh checkout | PASS | `i/lf w/lf` (7 unmodified product files refreshed) |
| Schema re-applied | PASS | exit 0, public 41 tables |
| 9 workflows imported unchanged, credentials, active | PASS | 19/19 Postgres nodes, 9/9 active |
| GET /webhook/mctb-health | PASS | 200 `{"ok":true,"db":"ok","version":"mctb-engine-1"}` |
| Owner `DONE +15555550175` | PASS | review scheduled at +120 min |
| Review text | PASS | `mock_sent`, link `http://localhost:5679/webhook/mctb-r?t=980afa41...` (static path) |
| GET that exact link | PASS | 302, Location https://g.page/r/demo-plumbing/review; request status `clicked`, clicked_at set (curl.exe) |
| No reminder after the click | PASS | texts to the customer before/after = 1/1, reminded=false |
| Control: unclicked request (+15555550176) | PASS | exactly one reminder, mock_sent |
| Second REVIEW within 90 days | PASS | skipped ("already went ... in the last 90 days") |
| REVIEW to an opted-out number (+15555550173) | PASS | skipped, no row |
| Core e2e, fresh numbers +15555550173/+15555550174 | PASS | 11/11 (missed call, qualify, STOP, 2 opt-out refusals, call after STOP, qualified count, control send, estimate follow-up, dashboard, owner alerts past 12) |
| Revenue report: status becomes `sent` | PASS | W39 report (now=Mon 2026-09-28 08:30 CT): queued, then mock_sent (214 chars, all counts 0 because that past week has no demo data), then `revenue_reports.status=sent`, queue row `sent` |
| Dashboard This week / This month block and review tiles | PASS | Review requests sent=6, Review links clicked=3 |
| Health workflow normal run | PASS | health_state ok, no alert |
| backup.ps1 UTF-8 | PASS | see below |
| Nightly task with the new script | PASS | see below |
| Full test suite (throwaway container) | PASS | 32/32 |
| n8n executions since the re-import | PASS | all `success` |

**Backup UTF-8 test:**
- I inserted the probe row `utf8 probe José — Café` (outbound_log id 127, a fake number, status `probe`) and ran `scripts\backup.ps1 -OutDir C:\mctb-backups`.
- Output: `C:\mctb-backups\mctb-20261003-081234.sql`, **158,209 bytes**.
- The UTF-8 bytes `...4a6f73c3a920e2809420436166c3a9` are identical in the DB, the dump, and a scratch restore (0 restore errors). The dump has 0 CR bytes and no BOM, and the container's /tmp is clean.
- Cleanup: the probe row is deleted and the scratch DB is dropped.

**Nightly task:** "MCTB Postgres backup" already runs the repo script, so it picks up the new version automatically.
- A test run gave Last Result 0 and wrote `mctb-20261003-081254.sql` (158,052 bytes, 0 CR).
- Next run is 2026-10-04 02:30.
- File-name stamps use the mini-PC clock, which is about 9 minutes slow.

### Notes and leftovers
- **Test-script issues, not product bugs:**
  - The first core run with the default numbers failed checks 1 and 2 because +15555550181 was already opted out from the last round. Re-running with fresh numbers passed.
  - The first feature run crashed at the click step: PS 5.1 `Invoke-WebRequest -MaximumRedirection 0` returns no response object. The click itself was logged (+15555550171, request 5). I switched the script to curl.exe.
- **Owner cap:** repeated e2e runs put the demo owner at 66 texts in 24h, over the limit of 60. The revenue queue row was therefore deferred by 1 hour, which is correct behavior. I temporarily set `owner_daily_sms_limit=200`, released the row, and set it back to 60.
- **Old W40 report:** the W40 report from the deeefaa round stays `queued` because its queue row predates the `revenue_report_id` link. This is cosmetic.
- Review request 1 (+15555550191) is still scheduled for about 11:53 CT and will use the new link format.
- Still open: the clock is about 9 minutes slow (time sync needs admin). The box shell still can't spawn bash.
- Scripts: `C:\Users\Admin\mctb-install\e2e_deeefaa.ps1` (core) and `e2e_b0fbe90_features.ps1`.
- **No repo fixes are outstanding for MCTB.**
