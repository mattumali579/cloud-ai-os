# Install runbook — first paying client

Do this on the Windows mini PC that already runs Docker. You do not buy a server for this. You do not put Twilio passwords in git.

Two ways to host it:

1. **Use the n8n and Postgres you already run** for Cloud AI OS. The product lives in schema `mctb` and in six new workflows. Outreach tables are not modified.
2. **Start a separate stack** from `products/missed-call-textback/docker-compose.yml` if you want this client’s data in its own Postgres. Host ports are 5680 (n8n) and 54329 (Postgres) so they do not take the Cloud AI OS ports 5679 and 5432.

## 0. What you will spend

Nothing in this repo creates a paid resource. Money starts when you (or the client) do these things in the Twilio and Cloudflare consoles.

Use the client’s own Twilio account when you can. “They own it” is cleaner if the number, the brand registration, and the usage bill are in their name. Your account is fine for the first week if you are the one who will click the buttons, as long as you can hand the number over later.

Published Twilio US list prices, checked against Twilio’s pages in October 2026. Confirm in the console before you promise a number; Twilio changes them.

| Item | Published price | Notes |
|---|---|---|
| Local 10-digit number | $1.15 / month | One number per shop |
| Toll-free number | $2.15 / month | Alternate to 10DLC. Verification is a separate Twilio review. |
| SMS, each direction | $0.0083 per segment | Plus carrier fees. A typical shop text is 1–2 segments. Budget about **$0.01–$0.02 all-in per text** so the carrier fee does not surprise you. |
| Failed message | $0.001 | Only if Twilio marks it failed |
| Voice, inbound | About a cent a minute | Only matters in dial mode, while the owner’s phone is ringing. Check [Twilio voice pricing](https://www.twilio.com/en-us/pricing/voice). |
| A2P low-volume standard brand | About $4 one-time brand registration + $15 one-time campaign vetting + **$1.50–$10 / month** per campaign | For a business with an EIN sending under 6,000 segments/day |
| A2P standard brand | About $44 one-time ($4.50 TCR fee + $41.50 vetting) + $15 campaign vetting + monthly campaign fee | Only if they truly need high volume. A local shop does not. |
| Sole proprietor brand | About $4 brand + $15 campaign vetting + $2 / month | No EIN. One campaign, one number, low daily cap (about 1,000 T-Mobile segments/day). |

A shop that misses 15 calls a week and sends the 3-step estimate follow-up on a handful of jobs is usually **under $15–$25 / month** in Twilio usage after the registration fees, plus the $1.15 number. The $1,500 install is the one-time fee. Twilio is the client’s monthly bill, not yours, when the account is in their name.

Unregistered 10DLC traffic is filtered and surcharged. Do not skip registration and then tell the client the product is live.

Hosting on the mini PC they already power is $0. A Cloudflare Tunnel on the free plan is $0. You need a free Cloudflare account. A quick tunnel URL (`trycloudflare.com`) changes every restart. Use a named tunnel on a hostname you control before a shop depends on the webhook address.

## 1. Postgres schema

From the repo root, against the database you actually want. This only creates schema `mctb`.

Existing Cloud AI OS Postgres:

```bash
docker compose -f infra/docker/docker-compose.yml exec -T db \
  psql -U cloudos -d cloudos -v ON_ERROR_STOP=1 \
  < products/missed-call-textback/sql/001_schema.sql
```

Dedicated stack (first boot applies the schema itself via `docker-entrypoint-initdb.d`):

```bash
cd products/missed-call-textback
cp .env.example .env
# put a long random value in N8N_ENCRYPTION_KEY
docker compose up -d
```

Default dedicated database URL: `postgresql://mctb:mctb@127.0.0.1:54329/mctb`

Change that password before anyone else can reach the port. The published example password is for a machine that is not on the public internet.

## 2. Environment on the n8n container

Add these to the n8n service. Empty values keep the system in mock mode: workflows run, texts are stored, Twilio is not called.

```bash
N8N_BLOCK_ENV_ACCESS_IN_NODE=false
GENERIC_TIMEZONE=America/Chicago
TWILIO_MODE=mock
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
MCTB_PUBLIC_BASE_URL=https://your-tunnel-host
OWNER_ALERT_WEBHOOK_URL=
```

`N8N_BLOCK_ENV_ACCESS_IN_NODE` must stay false. The workflows read those variables with `$env`.

`OWNER_ALERT_WEBHOOK_URL` is optional. Point it at the Cloud AI OS `owner-notify` webhook, a Discord webhook, or anything that accepts `{"severity","code","message","meta"}`. Owner SMS still happens through the business number.

n8n listens on port **5678 inside the container**. The host port is whatever the compose file publishes, and it is not the same on every machine. The Cloud AI OS stack on the mini PC publishes **5679**. The dedicated compose file in this folder publishes **5680**. Before you open the editor or start a tunnel, read the published port from `docker ps`. Do not assume 5678 on the host.

Those Twilio variables have to be in the n8n process environment. Writing them into `.env` does not change a container that is already running, and `docker restart` keeps the old environment. Recreate the container so Docker loads the file:

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --force-recreate n8n
```

Run that from the repo root for the existing Cloud AI OS n8n. For the dedicated stack, run it from `products/missed-call-textback` and drop the `-f` path. If this n8n was started with `docker run` instead of compose, stop and remove that container and start it again with the same volume and `--env-file .env`. Then confirm the process can see the mode:

```bash
docker exec cloudos-n8n-1 printenv TWILIO_MODE
```

The container name comes from `docker ps`. On the mini PC it is usually `cloudos-n8n-1`.

## 3. Import the workflows

Files:

- `workflows/mctb_voice.json` — unanswered call
- `workflows/mctb_dial_status.json` — owner did not pick up
- `workflows/mctb_inbound_sms.json` — replies, STOP/HELP/START, owner commands
- `workflows/mctb_followups.json` — estimate sequence, once a minute
- `workflows/mctb_dashboard.json` — private results page
- `workflows/mctb_actions.json` — log an estimate, text a customer, mark won/lost

UI: n8n → Workflows → Import from File. Repeat for all six. Leave them inactive until the Postgres credential is attached.

CLI, from the repo root, with the Cloud AI OS compose project:

```bash
products/missed-call-textback/scripts/install_into_n8n.sh
```

The script applies the schema if `DATABASE_URL` is set, copies the JSON into the n8n container, and imports it. Imported workflows stay inactive.

### Postgres credential

n8n does not import database passwords from git. In n8n: Credentials → New → Postgres.

| Field | Existing Cloud AI OS stack | Dedicated compose |
|---|---|---|
| Name | `BrightReach Postgres` | `BrightReach Postgres` |
| Host | `db` | `db` |
| Port | `5432` | `5432` |
| Database | `cloudos` | `mctb` |
| User | `cloudos` | `mctb` |
| Password | the Postgres password | the `mctb` password |
| SSL | disable | disable |

The host name `db` works only inside the compose network. From the Windows host it is `localhost`.

Open each imported workflow. Every node that says Postgres needs this credential. There are 11 of them. Save.

If you already created an n8n owner login, this can click them for you:

```bash
N8N_BASE_URL=http://127.0.0.1:5679 \
N8N_EMAIL=you@example.com \
N8N_PASSWORD='the n8n password' \
MCTB_PG_HOST=db \
MCTB_PG_DATABASE=cloudos \
MCTB_PG_USER=cloudos \
MCTB_PG_PASSWORD='the postgres password' \
node products/missed-call-textback/scripts/assign_postgres_credential.mjs
```

Use port 5680 if you started the dedicated compose file. The script creates the credential, writes its id onto the Postgres nodes, and activates the six BrightReach workflows. If the n8n API shape does not match, it prints the error and you attach the credential by hand. That is a linking step, not a product bug.

Activate all six workflows. Production webhook URLs do not exist until the workflow is active. The editor “test URL” (`/webhook-test/...`) is the wrong URL to paste into Twilio.

## 4. Expose the webhooks

Twilio must reach n8n from the public internet. On the mini PC, with the Cloud AI OS n8n on host port 5679:

```bash
docker run --rm cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://host.docker.internal:5679
```

On Linux the mini PC may need `--network host` and `http://127.0.0.1:5679` instead of `host.docker.internal`.

The command prints an `https://....trycloudflare.com` URL. Set `MCTB_PUBLIC_BASE_URL` to that origin, with no trailing slash, and restart n8n.

For a URL that survives a reboot, create a named tunnel in the free Cloudflare Zero Trust dashboard, route a hostname you control at `http://n8n:5678` (the port inside the compose network) or at the host port, and run `cloudflared` as a service. The dedicated compose file has a `tunnel` profile that uses a quick tunnel:

```bash
docker compose --profile tunnel up -d
docker compose logs -f cloudflared
```

Paste these three URLs into the Twilio number. Method POST.

| Twilio field | URL |
|---|---|
| A call comes in | `https://HOST/webhook/mctb-voice` |
| (dial mode only) the Dial action is set by the workflow from `MCTB_PUBLIC_BASE_URL` | `https://HOST/webhook/mctb-dial-status` |
| A message comes in | `https://HOST/webhook/mctb-sms` |

Leave the status callback empty unless you want Twilio’s debugger as well.

n8n’s own login protects the editor. It does not protect webhooks. The dashboard is protected by the random token from the setup script. Do not put the n8n editor on the public hostname if you can avoid it; a tunnel that only forwards `/webhook/` is tighter. A quick tunnel forwards the whole port, so use a strong n8n owner password.

## 5. Twilio number and call forwarding

Buy one local number in the client’s area code. Do it in the Twilio console when you are ready to pay. This repo cannot and must not do it for you.

### Mode A — keep the number on the truck (`call_mode=forward`)

The shop keeps advertising their current number. In the phone admin or by dialing a carrier code, turn on **conditional** forwarding for no-answer and busy, to the Twilio number. Do not turn on unconditional forward-all, or the owner’s phone will never ring.

Codes differ by carrier. Confirm on the carrier’s site the day you install. Wrong codes are the usual way a shop accidentally forwards every call.

| Carrier | No answer | Busy | Cancel |
|---|---|---|---|
| AT&T | `*92*<10 digits>#` | `*90*<10 digits>#` | `*93#` and `*91#` |
| Verizon | `*71` then the 10-digit number | `*71` covers busy and no-answer on many Verizon plans | `*73` |
| T-Mobile | `**61*<number>#` | `**67*<number>#` | `##61#` and `##67#` |

Set the shop’s ring time shorter than the carrier’s voicemail pickup (often 20–25 seconds), or voicemail wins and Twilio never sees the call.

When the forwarded call hits Twilio, the workflow answers, speaks one sentence, texts the caller, texts the owner, and hangs up.

### Mode B — the Twilio number is the published number (`call_mode=dial`)

Twilio rings the owner’s cell for `ring_timeout_seconds` (default 20, max 40). If they answer, nothing is texted. If they do not, the caller and the owner get the missed-call texts.

`MCTB_PUBLIC_BASE_URL` must be the public origin. Dial mode builds the “they didn’t answer” URL from it. If that variable is empty, the workflow will not ring the owner’s phone.

## 6. Add the shop

```bash
DATABASE_URL='postgresql://cloudos:cloudos@127.0.0.1:5432/cloudos' \
PUBLIC_BASE_URL='https://HOST' \
products/missed-call-textback/scripts/setup_tenant.sh \
  --slug northline \
  --business-name 'Northline Heating & Air' \
  --twilio-number '+14145550100' \
  --owner-name Dana \
  --owner-phone '+14145550199' \
  --owner-email dana@northline.example \
  --hours 'Mon-Sat 7am-7pm' \
  --booking-link 'https://northline.example/book' \
  --timezone America/Chicago \
  --call-mode forward
```

The script prints the dashboard URL. That URL is the password. Text it to the owner. Do not commit it.

On the Windows mini PC, bash and a host `psql` are often missing. The same insert can run inside the Postgres container. From PowerShell, in the repo:

```powershell
powershell -ExecutionPolicy Bypass -File products\missed-call-textback\scripts\setup_tenant.ps1 `
  -Slug northline `
  -BusinessName "Northline Heating & Air" `
  -TwilioNumber "+14145550100" `
  -OwnerName Dana `
  -OwnerPhone "+14145550199" `
  -OwnerEmail dana@northline.example `
  -Hours "Mon-Sat 7am-7pm" `
  -BookingLink "https://northline.example/book" `
  -Timezone America/Chicago `
  -CallMode forward `
  -PublicBaseUrl "https://HOST" `
  -Container cloudos-db-1 `
  -PgUser cloudos `
  -PgDatabase cloudos
```

`cloudos-db-1` is the usual container name for the Cloud AI OS compose project. Dedicated stack: `-Container brightreach-mctb-db-1 -PgUser mctb -PgDatabase mctb`. Confirm the name with `docker ps`. The script pipes the insert through `docker exec` into `psql` in that container. It does not need bash or a Postgres client on Windows.

Templates can stay on the defaults. To change wording later:

```sql
UPDATE mctb.tenants
SET missed_call_template = '...'
WHERE slug = 'northline';
```

Placeholders: `{business_name}` `{hours}` `{booking_link}` `{owner_name}` `{customer_name}` `{job}` `{amount}`.

## 7. A2P 10DLC registration

Do this in the client’s Twilio console under Trust Hub / Messaging compliance, before you promise delivery. Twilio’s walkthrough: [A2P 10DLC](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc).

For almost every shop, pick **Low-Volume Standard** if they have an EIN, otherwise **Sole Proprietor**. Not Standard.

Brand details must match the legal business: legal name, EIN or sole-prop identity, website or social page, address. A rejected brand is a delay, not a code change.

Campaign:

| Field | What to enter |
|---|---|
| Use case | Customer Care, or Low Volume Mixed if the console asks for a mixed campaign under the low-volume brand |
| Description | Texts people who called the business and did not reach a person, and follows up on estimates the business logged for those customers. |
| Opt-in | The person calls the business phone number. The first text is sent because that call was not answered. Estimate follow-ups go only to a number the customer called from, or a number the customer gave the business. |
| Opt-out | Reply STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, or QUIT. |
| Help | Reply HELP or INFO. The reply includes the business name and the owner’s phone. |
| Sample 1 | `Hi, this is Northline Heating & Air. Sorry we missed your call. Reply with what you need and we will get you scheduled. Hours: Mon-Sat 7am-7pm. Book: https://northline.example/book. Reply STOP to opt out.` |
| Sample 2 | `Hi Pat Keller, this is Northline Heating & Air. Following up on your furnace replacement estimate for $4,500.00. Reply with any questions or book here: https://northline.example/book. Reply STOP to opt out.` |
| Sample 3 | `You are unsubscribed from Northline Heating & Air and will not receive more messages. Reply START to resubscribe.` |

Message frequency to tell the carrier: one text when a call is missed, up to three estimate follow-ups, plus replies to messages the customer sends.

Do not load purchased lead lists into this system. It is for inbound callers and estimates the shop already wrote. A campaign registered as customer care and then used for cold outreach gets filtered, and it puts the client’s brand at risk.

Approval often takes a few days and sometimes longer. Until the campaign shows approved and the number is attached to it, treat texts as unreliable. Use the browser demo on sales calls in the meantime. You can still place a test call; watch n8n’s execution list and the `mctb.outbound_log` table to prove the workflow ran.

Toll-free verification is the other path Twilio offers. It has its own form and its own wait. Pick one path per number.

## 8. Prove it before you hand over the keys

1. `TWILIO_MODE=mock`. Import and activate. Run `npm test` on a machine with Node and Postgres. That is the same graph Twilio will hit.
2. Set `TWILIO_MODE=live` and the account SID and auth token. Restart n8n.
3. Call the shop number, let it ring out. Your phone should get the missed-call text, and the owner phone should get the alert.
4. Reply with a job, a ZIP, and “today”. The owner text should contain all three.
5. Reply `STOP`. Call again. The call is on the dashboard and no new text goes to that phone.
6. Reply `START` from that phone if you want it back on the list.
7. On the dashboard, log an estimate. Within about a minute, `mctb.outbound_log` shows the first follow-up (`mock_sent` or `sent`).
8. Open the dashboard on your phone. The counts at the top are the guarantee numbers: missed calls, recovered replies, qualified leads, texts, open estimates, jobs won, won amount.

## 9. Owner commands

From the owner’s mobile, text the Twilio number:

```text
ESTIMATE Jane Doe | 4145550199 | furnace | 4500
WON 12
LOST 12
REPLY +14145550199 We can be there Thursday
HELP
```

The phone in `REPLY` is one token. `WON` and `LOST` use the estimate id on the dashboard.

## 10. Quiet hours, caps, STOP

- Quiet hours default to 9:00 p.m.–8:00 a.m. in `timezone` (default `America/Chicago`). Follow-ups and queued texts wait until 8:00 a.m. Missed-call texts go immediately unless `missed_call_respects_quiet_hours` is true.
- Default cap: 200 outbound texts per shop per rolling 24 hours, and 12 to any one customer number. The owner phone uses `owner_daily_sms_limit` (default 60), so lead alerts are not cut off by the customer cap. When the owner cap is reached, the owner gets one text, `Alerts paused for today, see your dashboard: <link>`, and later alerts that day stay on the dashboard only. Raise it with `UPDATE mctb.tenants SET owner_daily_sms_limit = 60 WHERE slug = 'northline';`. STOP, HELP, and START confirmations to the customer still go out over the cap. Those are the compliance replies.
- STOP / STOPALL / UNSUBSCRIBE / CANCEL / END / QUIT must be the whole message. “Stop by tomorrow” is a normal reply.
- A later missed call from an opted-out number is stored and the owner is told to call them. They are not texted.
- Dashboard “Text a customer” and an owner `REPLY` to an opted-out number are refused. The owner sees that the number opted out. The text is not sent and is not written to `outbound_log` as sent.
- Qualified leads stay on the 30-day count after that person replies STOP. The count is people who finished qualification in the last 30 days, not people whose thread is still open.

## 11. Sales demo on GitHub Pages

The demo is static. From the repo: open `products/missed-call-textback/demo/index.html`.

To publish it, push the branch and run the **Missed-call demo pages** GitHub Action (`workflow_dispatch`). The action uploads only the `demo/` folder. GitHub’s Pages environment has to be enabled on the repo once; the action does not run on ordinary pushes.

If Pages was already pointed at another folder, don’t run that action until you mean to replace the Pages site. Hosting the file locally is enough for a sales call.

## 12. Go-live checklist

- [ ] Schema `mctb` applied
- [ ] Six workflows imported, Postgres credential attached, workflows active
- [ ] `MCTB_PUBLIC_BASE_URL` is the public https origin and n8n was restarted
- [ ] Tunnel stays up when the laptop you sold from is closed (the mini PC is the host)
- [ ] Twilio number’s voice and SMS webhooks point at `/webhook/mctb-voice` and `/webhook/mctb-sms`
- [ ] Shop added with `setup_tenant.sh` (or `setup_tenant.ps1` on Windows), dashboard link in the owner’s hands
- [ ] Call forwarding is conditional, and a real missed call produced a real text
- [ ] A2P brand and campaign submitted, number attached, campaign approved before you call the line “in production”
- [ ] `TWILIO_MODE=live` only after the SID and token are in the environment, not in git
- [ ] You watched one estimate move from open → follow-up sent, and one STOP suppress a second text

## 13. When something is quiet

| What you see | What it usually is |
|---|---|
| Twilio debugger says 404 on the webhook | Workflow is not active, or the path is `/webhook-test/` |
| Execution errors on every Postgres node | Credential not attached, or host `db` is wrong from that container |
| Webhook execution is green, the response body is empty, and no row was written | Re-import the six workflows from this repo. Each Postgres query must be `SELECT fn(...) AS "ctx"` (or `work`, `snap`, `tenant_pack`, `result`). Real n8n returns that alias as the column name. A bare `jsonb_build_object(...)` column does not. |
| `$env.TWILIO_MODE` is empty inside a node after you edited `.env` | The container was restarted, not recreated. Use `docker compose --env-file .env up -d --force-recreate n8n` and check `docker exec <n8n-container> printenv TWILIO_MODE`. |
| Call hits voicemail, workflow never runs | Ring time is longer than the carrier voicemail timer, or forwarding was not actually saved |
| Text shows in `outbound_log` as `twiml` but the phone stays empty | The webhook response was not returned as XML to Twilio, or the A2P campaign is not approved |
| Follow-up row sits at `next_send_at` in the past | `mctb_followups` workflow is inactive, or it is waiting for quiet hours |
| Owner reply from the dashboard is `mock_sent` | `TWILIO_MODE` is not `live`, or the SID/token is empty. Missed-call texts can still be real, because those use TwiML rather than the REST API. |
| Dashboard says the link is not valid | Token typo, or the tenant row is `active = false` |
