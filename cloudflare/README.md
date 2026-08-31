# Cloudflare — ingress worker + Workers AI notes (Agent 7)

Contract: `contracts/ARCHITECTURE.md` §13 (this component), §6 (error shape),
§0 (zero-cost prime directive), §9 (where Workers AI is actually called from).

**Free plan only. Nothing here may enable a paid feature.** The worker is a
stateless ingress: no Queues, no Durable Objects, no KV, no D1, no Workers AI
binding, trivial CPU per request (one HMAC + one proxied fetch).

## What the worker does

`ingress-worker/` — plain-JS ES module, no build step, no npm dependencies.

| Route | Auth | Behavior |
|---|---|---|
| `GET /ping` | none | `200 {"status":"ok"}` |
| `POST /heartbeat` | `Authorization: Bearer ${AGENT_API_TOKEN}` | Send `Cloud heartbeat OK — YYYY-MM-DD HH:mm America/Chicago` directly to Discord and return Discord's created message ID. |
| `POST /hook/*` | `X-Signature`: hex sha256 HMAC of the **raw body**, keyed with `WEBHOOK_SECRET` (constant-time verify via WebCrypto) | Forward raw body to `${ORIGIN_URL}/v1/webhooks/n8n` with `Authorization: Bearer ${AGENT_API_TOKEN}`; relay the origin's status code. Bad/missing signature → `401`, origin never contacted. |
| `ANY /api/<origin-path>` | `Authorization: Bearer ${AGENT_API_TOKEN}` (constant-time compare) | Proxy method + body + query string to `${ORIGIN_URL}<origin-path>`. Only origin paths starting with `/v1/jobs`, `/v1/runs`, `/v1/quota`, `/v1/agent/invoke` are reachable; anything else → `404`. |
| anything else | — | `404` |
| cron `*/30 * * * *` | — | Send the timestamped heartbeat directly to `DISCORD_WEBHOOK_URL`, then `GET ${ORIGIN_URL}/healthz` with a 10 s timeout. On non-200 or failure, `POST` `{"severity":"critical","code":"DEPENDENCY_UNAVAILABLE","message":"origin health check failed","meta":{...}}` to `NOTIFY_WEBHOOK_URL` (the n8n `health_alert` webhook). The notification body is HMAC-signed (`X-Signature`, keyed with `WEBHOOK_SECRET`), so `NOTIFY_WEBHOOK_URL` may point either directly at n8n or at this worker's own `/hook/health-alert` front. |

All error responses use the contract §6 shape:
`{"error":{"code":"<ErrorCode>","message":"...","details":{}}}`.
Mapping used here (the §6 enum has no auth/not-found members): `401` and `404`
carry `VALIDATION_ERROR`; origin unreachable carries `DEPENDENCY_UNAVAILABLE`
(`502`); unhandled/unconfigured carries `INTERNAL_ERROR` (`500`). Secrets never
appear in any error body or notification payload (the health notification
deliberately classifies failures as `timeout` / `fetch_failed` / `non_200`
instead of embedding raw error text, which could contain `ORIGIN_URL`).

## Deploy

```sh
cd cloudflare/ingress-worker
npx wrangler@latest login                 # opens browser; authorize your CF account

# All five config values are Worker secrets — never committed, never in [vars]:
npx wrangler@latest secret put WEBHOOK_SECRET       # long random string; same value n8n signs with
npx wrangler@latest secret put AGENT_API_TOKEN      # must equal AGENT_API_TOKEN in the VM's .env
npx wrangler@latest secret put ORIGIN_URL           # e.g. https://<oracle-vm-host>:8080  (no trailing slash)
npx wrangler@latest secret put NOTIFY_WEBHOOK_URL   # n8n health_alert webhook URL
npx wrangler@latest secret put DISCORD_WEBHOOK_URL  # direct Discord heartbeat destination

npx wrangler@latest deploy                # free plan; cron trigger is created from wrangler.toml
```

Verify: `curl https://cloudos-ingress.<your-subdomain>.workers.dev/ping` →
`{"status":"ok"}`. Then make an authenticated `POST /heartbeat` and confirm its
response contains `sent: true` plus a `discord_message_id`. The cron fires every
30 minutes; check `wrangler tail cloudos-ingress` or the dashboard for scheduled
invocations.

Free-plan budget notes: 100k requests/day and 10 ms CPU (free tier) per
invocation are far above this worker's needs (~48 cron runs/day + webhook/API
traffic; each request does one HMAC and one subrequest). No storage products
are bound, so there is nothing that can accrue cost.

### Local validation (no deploy, no credentials)

```sh
cd cloudflare/ingress-worker
node --check src/index.js        # syntax
node --test test/index.test.mjs  # 30 unit tests, mocked fetch, no network
```

## Workers AI — called from the VM, NEVER from this worker

The free Workers AI allocation (10,000 neurons/day) is consumed by the AI
router (**Agent 8**, `cloudos.router` L1) running on the Oracle VM, via the
REST API with `httpx` — not via a Worker binding, and not from this ingress
worker (§13: no AI orchestration in the Worker; keeps CPU-ms trivial and keeps
quota accounting in one place, the router's `quota_usage` table).

```sh
# Credentials come from the VM's .env: CF_ACCOUNT_ID, CF_WORKERS_AI_TOKEN (§4).
# The API token needs the "Workers AI — Read" permission only.
curl "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct" \
  -H "Authorization: Bearer ${CF_WORKERS_AI_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"ping"}],"max_tokens":32}'
```

Rules enforced by the router (do not bypass from anywhere):

- The model id **must** be listed in `config/free_model_allowlist.json`
  (file owned by the lead, Agent 1; the router, Agent 8, consumes and enforces
  it — anything else fails with `MODEL_NOT_ALLOWED`).
- **Never assume a Workers AI model is free-plan accessible.** Cloudflare's
  catalog mixes free-allocation models with ones that bill immediately.
  Verify first (call it once with your free account and confirm the response
  and the dashboard show no charge/paid flag), then add it to the allowlist.
- Daily budget: `WORKERS_AI_DAILY_BUDGET` (default 9,000 neurons — conservative
  vs. the 10,000 free allocation). At the cap the router **fails closed**:
  `QUOTA_EXHAUSTED`, task blocked, owner notified. No fallback to paid usage,
  ever (`ALLOW_PAID_AI=false` is permanent).

## Failure modes (Agent 7 rows for docs/FAILURE_MODES.md)

| Failure | Detection | Behavior | Recovery |
|---|---|---|---|
| Cloudflare Workers AI quota exhausted | Router-side (Agent 8): `quota_usage('workers_ai')` ≥ `WORKERS_AI_DAILY_BUDGET`, or CF returns a quota error | **Not a worker concern** — this worker never calls Workers AI. The router fails closed with `QUOTA_EXHAUSTED`; the worker (Agent 3) marks the job `blocked` and notifies the owner. Ingress keeps serving normally. | Automatic: allocation resets daily; router resumes under budget. Never raise the budget past the free allocation. |
| Origin (Oracle VM) down | Health cron: `/healthz` non-200, timeout (10 s), or connection failure; also live `/hook/*` / `/api/*` proxying failing | Cron POSTs a signed critical `DEPENDENCY_UNAVAILABLE` notification to `NOTIFY_WEBHOOK_URL` every 30 min while down. Live proxy requests return `502 {"error":{"code":"DEPENDENCY_UNAVAILABLE",...}}` — the worker holds no state and queues nothing (stateless by contract). | Owner follows `infra/oracle/RUNBOOK.md` (reclamation recovery: new VM → bootstrap.sh). State survives in Supabase, code in GitHub, knowledge in Git. Alerts stop at the next healthy cron tick. |
| Invalid HMAC on `/hook/*` webhook | `X-Signature` missing, malformed, or failing constant-time verification against the raw body | `401 {"error":{"code":"VALIDATION_ERROR","message":"invalid or missing webhook signature"}}`; the origin is **never** contacted; nothing is logged beyond CF's standard request log (no payload retention). | Legitimate sender: re-sign with the shared `WEBHOOK_SECRET` over the exact raw bytes sent. If secrets drifted, owner rotates: `wrangler secret put WEBHOOK_SECRET` + update n8n credential. Persistent unsigned traffic is just dropped — no cost, no state. |

## Files

```
cloudflare/
├── README.md                     ← this file
└── ingress-worker/
    ├── wrangler.toml             name=cloudos-ingress, cron */30, free plan, secrets documented
    ├── package.json              {"type":"module"} marker so node:test can import src (no deps)
    ├── src/index.js              worker: exported pure functions + default {fetch, scheduled}
    └── test/index.test.mjs       node --test suite (HMAC verify, allowlist, routes, cron) — mocked fetch
```
