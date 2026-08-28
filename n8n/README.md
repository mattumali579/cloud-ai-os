# n8n — Workflow Automation (Agent 5)

Importable n8n workflows for Cloud AI OS, per `contracts/ARCHITECTURE.md` §12
(image, backend, auth), §7 (agent-api endpoints they call), §8 (daily retention
job = the system's legitimate Supabase activity), and §0 (zero-cost /
no-artificial-utilization prime directives).

Runtime: official `docker.n8n.io/n8nio/n8n` image (arm64 OK), **SQLite backend**,
basic auth via env. The container itself (compose service, volumes, memory
limits) is defined by Agent 6 in `infra/docker/`; this directory only ships the
workflow JSON + docs.

## Workflows

### `daily_ops.json` — CloudOS Daily Ops (schedule)

```
Daily Schedule (1x/day, 03:30)
  → HTTP POST {AGENT_API_URL}/v1/jobs   {"type":"retention.prune","payload":{}}   (Bearer AGENT_API_TOKEN)
  → HTTP GET  {AGENT_API_URL}/v1/quota                                            (Bearer AGENT_API_TOKEN)
  → Set "Evaluate Quota"  (alert=yes if any provider fail-closed / over-budget)
  → IF alert == yes
      → HTTP POST {NOTIFY_WEBHOOK_URL}  {severity:"critical", code:"QUOTA_EXHAUSTED", message, meta:{quota}}
```

This single daily run — enqueue the retention prune job, read quota — **is** the
system's legitimate daily Supabase activity (1–3 small DB interactions/day,
§8). The worker also self-schedules `retention.prune` with
enqueue-if-none-pending, so the duplicate enqueue is harmless; this workflow is
the belt-and-suspenders path plus the quota alert.

The quota check in "Evaluate Quota" is deliberately shape-tolerant (top-level
`fail_closed` / `over_budget`, or any entry under `providers`/`quota` as an
array or object map with `fail_closed` / `over_budget` / `exhausted`). Tighten
the expression once Agent 2's `/v1/quota` response shape is final.

### `owner_notify.json` — CloudOS Owner Notify (webhook)

```
Webhook POST /webhook/owner-notify   body: {severity, code, message, meta}
  → Set "Format Notification"   (safe defaults for every missing field; meta stringified + truncated)
  → HTTP POST {OWNER_ALERT_WEBHOOK_URL}   body: {content: "[SEVERITY] CODE -- message\nmeta: {...}"}   ← PLACEHOLDER
  → Respond 200 {ok: true}
```

The notification node is a **documented placeholder** (see its in-editor note):
the shipped body (`{content}`) matches a **Discord webhook** as-is; for Slack
use `{text}`, for Telegram point the URL at
`https://api.telegram.org/bot<token>/sendMessage` with `{chat_id, text}`, or
swap the node for n8n's email node. All transport configuration lives in this
one node — every other flow (daily_ops, health_alert, the Python side's
`notify.py` via `NOTIFY_WEBHOOK_URL`) funnels through this webhook.

Delivery failure does **not** fail the webhook: the notify node runs with
on-error → continue, so callers always get a 200 and never retry-storm; failed
deliveries stay visible in n8n's Executions list, and the Python side
additionally persists to the `notifications` table (§5).

### `health_alert.json` — CloudOS Health Alert (webhook)

```
Webhook POST /webhook/health-alert    (Cloudflare health cron's origin-down report, §13)
  → Set "Format Health Alert"   (normalize to {severity:"critical", code:"ORIGIN_DOWN", message, meta})
  → HTTP POST {NOTIFY_WEBHOOK_URL}   (forwards to owner_notify's notification step)
  → Respond 200 {ok: true}
```

It forwards to the owner-notify webhook rather than sending directly, so the
notification transport is configured in exactly one place. `owner_notify` must
be **active** for the forward to succeed.

## Required environment variables (on the n8n container)

| Variable | Used by | Example / meaning |
|---|---|---|
| `AGENT_API_URL` | daily_ops | Base URL of agent-api, no trailing slash. Compose network: `http://agent-api:8080` |
| `AGENT_API_TOKEN` | daily_ops | Bearer token for `/v1/*` (§7). Same value as the api container's `AGENT_API_TOKEN` (§4) |
| `NOTIFY_WEBHOOK_URL` | daily_ops, health_alert | The **owner-notify webhook** production URL, e.g. `http://localhost:5678/webhook/owner-notify` (same instance) or `http://n8n:5678/webhook/owner-notify` from other containers. Same canonical name the Python side uses (§4) |
| `OWNER_ALERT_WEBHOOK_URL` | owner_notify | **Your** external notification destination (Discord/Slack/Telegram/etc. webhook URL). Unset ⇒ delivery fails softly (200 still returned, failure visible in Executions) |
| `N8N_BASIC_AUTH_ACTIVE=true` | n8n itself | Enables basic auth on the editor/UI (§12) |
| `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD` | n8n itself | Credentials for the above. Set real values in `.env`; never commit them |

Notes:

- Workflows reference these via `{{ $env.NAME }}` expressions — nothing is
  hardcoded. Do **not** set `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`; it would break
  every `$env` expression here.
- n8n webhook endpoints themselves are unauthenticated. Keep them on the
  internal docker network, and for anything internet-facing use the Cloudflare
  ingress worker's HMAC-validated `/hook/*` route (§13) in front.

## Import

Imported workflows arrive **inactive** — activate all three afterwards
(webhook production URLs only exist while their workflow is active).

**UI:** open n8n (basic-auth login) → Workflows → **Import from File…** → pick
each JSON → Save → toggle **Active**.

**CLI** (inside the n8n container; with Agent 6's compose, copy files in first):

```sh
docker compose cp n8n/ n8n:/tmp/workflows/
docker compose exec n8n n8n import:workflow --separate --input=/tmp/workflows/
# or one at a time:
docker compose exec n8n n8n import:workflow --input=/tmp/workflows/daily_ops.json
```

Then activate in the UI (or `n8n update:workflow --all --active=true` to
activate everything, if you have no other workflows).

Smoke test after activation:

```sh
curl -X POST http://localhost:5678/webhook/owner-notify \
  -H 'Content-Type: application/json' \
  -d '{"severity":"info","code":"TEST","message":"hello from smoke test","meta":{"who":"you"}}'
# → {"ok":true} and (if OWNER_ALERT_WEBHOOK_URL is set) a real notification
```

## Why SQLite, not our Postgres (Supabase)

n8n defaults to SQLite (`DB_TYPE=sqlite`) and we keep it that way — **never
point n8n at the Supabase `DATABASE_URL`**:

- Supabase free tier is a **500 MB hard budget** (§0, §4
  `SUPABASE_DB_BUDGET_MB=500`) reserved for structured state (jobs, agent_runs,
  events, quota_usage, sb_index). n8n writes execution logs, workflow
  revisions, and binary-data bookkeeping on every run — steady churn that would
  eat the budget and trip the fail-closed path for no benefit.
- n8n state is **temporary compute state** (§1 source-of-truth hierarchy): the
  canonical copies of these workflows live in this Git directory. If the VM is
  reclaimed, re-import from Git — nothing irreplaceable is in n8n's DB.
- Keep the SQLite file small with execution pruning:
  `EXECUTIONS_DATA_PRUNE=true`, `EXECUTIONS_DATA_MAX_AGE=168` (hours ⇒ 7 days).

## Memory constraints (Oracle A1, 12 GB shared budget)

The A1 VM budget assumes ~8 GB used across api/worker/n8n/(db) (§1, §14). For
the n8n service (values live in Agent 6's compose files):

- `NODE_OPTIONS=--max-old-space-size=512` — caps the Node.js heap at 512 MB so
  n8n degrades (GC pressure / failed execution) instead of OOM-killing
  neighbors.
- compose `mem_limit: 768m` — hard container ceiling (512 MB heap + native/V8
  overhead headroom), per §14's api 512m / worker 1g / n8n 768m / db 512m split.

Keep workflows small (these three are tiny); avoid loading large binaries
through n8n.

## Degraded mode (gcp-minimal): n8n is EXCLUDED

`docker-compose.gcp-minimal.yml` (Agent 6, §14) runs **agent-api + worker
only** in ~1 GB RAM on the GCP e2-micro disaster-recovery fallback — no n8n.
Consequences, by design:

- Retention pruning still happens: the worker self-schedules `retention.prune`
  daily (§8). Nothing depends on `daily_ops.json` for correctness.
- With no owner-notify webhook reachable, set `NOTIFY_WEBHOOK_URL=` (empty) on
  the api/worker side ⇒ notifications persist to the `notifications` table
  only (§4) and are delivered when normal service resumes.
- The Cloudflare health cron's alert target is gone; origin-down visibility
  degrades to Cloudflare logs until the Oracle VM is restored.

## No-keepalive policy

Per §0 and §8: **do not** add per-minute (or any high-frequency) schedules,
pings, dummy jobs, or "keepalive" workflows to defeat provider
idle-reclamation — manufactured utilization is forbidden. The one daily
`daily_ops` run (1–3 small DB interactions/day) is the system's entire
scheduled footprint. Design for recovery from reclamation
(`infra/oracle/RUNBOOK.md`) instead of trying to prevent it.

## Failure modes

| Failure | Behavior | Recovery |
|---|---|---|
| n8n container down/unavailable | `daily_ops` doesn't run (worker's own daily prune still covers retention, §8). Python `notify.py` POSTs to `NOTIFY_WEBHOOK_URL` fail; notifications persist in the `notifications` table. Cloudflare health-cron POSTs fail (visible in CF logs). No data loss — workflow JSON is canonical in Git. | Restart the container (compose `restart: always` on Oracle); re-import from `n8n/` if the volume was lost |
| Malformed webhook payload (missing/wrong fields, empty body) | `owner_notify` / `health_alert` still succeed: every field has a safe default (`severity: info`/`critical`, `code: UNSPECIFIED`/`ORIGIN_DOWN`, placeholder message), meta is stringified defensively, caller still gets 200. A notification saying the payload was malformed is delivered rather than dropped | Fix the caller; inspect the raw payload in n8n Executions |
| agent-api down during `daily_ops` | The POST `/v1/jobs` node errors → execution marked failed in Executions list; no quota check that day. Worker-side daily prune is unaffected | Investigate api container; next day's run recovers automatically |
| `OWNER_ALERT_WEBHOOK_URL` unset/wrong | Notification node fails softly (on-error → continue), webhook still returns 200; failure visible in Executions; Python-side copy still lands in `notifications` table | Set the env var to a real Discord/Slack/Telegram webhook and restart n8n |
| `owner_notify` workflow inactive | `daily_ops` alert branch and `health_alert` forward get 404 from `NOTIFY_WEBHOOK_URL`; both fail softly (health_alert still returns 200 to Cloudflare) | Activate the workflow (webhook URLs exist only while active) |
