# Cloud AI OS — Shared Architecture Contracts (v1)

Authority: FINAL IMPLEMENTATION ADDENDUM > Gemini audit > original Master Prompt.
All subagents implement against THIS document. Do not invent incompatible interfaces.
If a needed interface is missing here, note it in your report — do not improvise a conflicting one.

## 0. Prime directives

- **$0/month recurring cost. Hard requirement.** The system FAILS CLOSED when a free
  allocation is exhausted: stop the affected task → persist state → log reason →
  notify owner. Never upgrade an account, attach paid infra, invoke a paid model,
  enable usage-based billing, or exceed a free-tier allocation.
- **Compute is disposable, data is durable, code is reproducible, AI is optional,
  paid services are disabled.**
- Do NOT manufacture artificial utilization (dummy hashes, fake traffic) to defeat
  provider idle-reclamation. Design for recovery from reclamation instead.

## 1. Topology

```
Cloudflare Worker (free)  ── ingress, webhook validation, auth, health cron
        │
Oracle A1 VM (Always Free, ARM64, assume 2 OCPU / 12 GB shared budget, ~8 GB used)
        │  Docker Compose
        ├── agent-api   (FastAPI, same image as worker)
        ├── worker      (job queue consumer + retention pruner, same image)
        ├── n8n         (official image, workflow automation)
        └── db          (LOCAL DEV ONLY — postgres:16-alpine; on Oracle, Supabase is the DB)
Supabase (free, 500 MB hard budget)  ── structured state: jobs, agent_runs, events, indexes
GitHub                               ── canonical CODE
Second Brain (Git/Markdown repo)     ── canonical KNOWLEDGE (NOT Supabase)
GCP e2-micro                         ── disaster-recovery fallback ONLY (degraded mode: api+worker)
```

Source-of-truth hierarchy: CODE→GitHub; KNOWLEDGE→Git/Markdown; STRUCTURED STATE→Supabase;
TEMPORARY COMPUTE STATE→VM. Nothing irreplaceable lives only inside a running container.

## 2. Repository layout (ownership map)

```
cloud-ai-os/
├── contracts/            Agent 1 (lead) — this spec. READ-ONLY for agents 2–10.
├── config/
│   ├── privacy.yaml              Agent 1 (lead). Agent 10 may EXTEND rules, not rename keys.
│   └── free_model_allowlist.json Agent 1 (lead). Agent 8 consumes.
├── src/cloudos/
│   ├── contracts.py      Agent 1 — shared enums/dataclasses. READ-ONLY.
│   ├── config.py         Agent 1 — env loading. READ-ONLY.
│   ├── notify.py         Agent 1 — owner notification. READ-ONLY.
│   ├── db.py             Agent 4 — connection pool + migration runner.
│   ├── api/              Agent 2 — FastAPI app (`cloudos.api.app:app`).
│   ├── worker/           Agent 3 — poll loop, queue ops, retention.
│   ├── router/           Agent 8 — AI router L0–L3.
│   ├── privacy/          Agent 10 — classifier, gate, secret scanner.
│   └── second_brain/     Agent 9 — context API over Git/Markdown.
├── db/migrations/        Agent 4 — ordered NNN_name.sql files.
├── n8n/                  Agent 5 — exported workflow JSON + docs.
├── infra/
│   ├── docker/           Agent 6 — Dockerfile, docker-compose.yml,
│   │                                docker-compose.oracle.yml, docker-compose.gcp-minimal.yml
│   ├── oracle/           Agent 6 — setup.sh bootstrap.sh deploy.sh backup.sh restore.sh RUNBOOK.md
│   │                                ARM64_COMPAT.md
│   └── gcp/              Agent 6 — degraded-mode docs.
├── cloudflare/           Agent 7 — ingress worker (JS), wrangler.toml, workers-ai client notes.
├── tests/                each agent owns tests/test_<area>*.py — never edit another agent's test file.
└── docs/                 PHASES.md (lead), FAILURE_MODES.md (Agent 3+8+10 sections), ZERO_COST_AUDIT.md (lead).
```

## 3. Runtime & dependencies (FIXED — do not add without reporting)

Python 3.12. Single package `cloudos` (installed `pip install -e .`), single Docker image,
two commands: `uvicorn cloudos.api.app:app` and `python -m cloudos.worker`.
Dependencies: `fastapi`, `uvicorn[standard]`, `psycopg[binary,pool]`, `pydantic>=2`,
`httpx`, `pyyaml`, `python-dotenv`, `pytest` (dev). ARM64 (linux/arm64) + linux/amd64 both required.
No Redis, no Celery, no ORM. Queue = Postgres `FOR UPDATE SKIP LOCKED`.

## 4. Environment variables (canonical names — see /.env.example)

```
ENVIRONMENT=local|oracle|gcp          INSTANCE_NAME=cloudos-local
DATABASE_URL=postgresql://...         AGENT_API_TOKEN=...   AGENT_API_PORT=8080
WORKER_POLL_INTERVAL_SECONDS=5        WORKER_CONCURRENCY=2
AGENT_RUN_RETENTION_DAYS=14           EVENT_RETENTION_DAYS=7
COMPLETED_JOB_RETENTION_DAYS=7        FAILED_JOB_RETENTION_DAYS=30
RETENTION_DRY_RUN=false
ALLOW_PAID_AI=false                   ALLOW_PAID_INFRASTRUCTURE=false
MAX_DAILY_PAID_AI_USD=0               MAX_MONTHLY_PAID_AI_USD=0
FREE_MODEL_ALLOWLIST_PATH=config/free_model_allowlist.json
CF_ACCOUNT_ID=                        CF_WORKERS_AI_TOKEN=
WORKERS_AI_DAILY_BUDGET=9000          # neurons/day conservative cap; fail closed at cap
GEMINI_API_KEY=                       GEMINI_DAILY_REQUEST_BUDGET=200
PRIVACY_CONFIG_PATH=config/privacy.yaml
SECOND_BRAIN_PATH=../AI-Second-Brain  SECOND_BRAIN_GIT_URL=
SUPABASE_DB_BUDGET_MB=500
NOTIFY_WEBHOOK_URL=                   # n8n webhook; empty ⇒ notifications table only
```

`cloudos.config.Settings` (pydantic-settings-style dataclass in config.py) is the ONLY way
code reads env. Never `os.environ` directly outside config.py.

## 5. Database schema (Agent 4 writes DDL in db/migrations/; this is the contract)

- `schema_migrations(version text pk, applied_at timestamptz)`
- `jobs(id uuid pk default gen_random_uuid(), type text, status text
   check in ('queued','running','succeeded','failed','cancelled','blocked'),
   priority int default 100, payload jsonb, result jsonb, error_code text, error text,
   attempts int default 0, max_attempts int default 3, run_at timestamptz default now(),
   created_at, started_at, finished_at, locked_by text, locked_at timestamptz)`
   Index: (status, run_at, priority).
- `agent_runs(run_id uuid pk, job_id uuid null refs jobs, agent text, workflow text,
   model text, level int, started_at, finished_at, status text, prompt_tokens int,
   completion_tokens int, cost_usd numeric default 0 CHECK (cost_usd = 0),
   error_code text, error text, input_hash text, output_hash text, meta jsonb)`
   COMPACT ONLY: never store full prompts/outputs/context — hashes + counts + small meta.
- `events(id bigserial pk, ts timestamptz default now(), level text, source text,
   code text, message text, meta jsonb)`  — short TTL (EVENT_RETENTION_DAYS).
- `notifications(id bigserial pk, ts, severity text, code text, message text,
   meta jsonb, delivered bool default false)`
- `quota_usage(provider text, day date, requests int default 0, units int default 0,
   pk(provider, day))` — router increments; fail closed at budget.
- `sb_index(path text pk, title text, privacy_label text, content_hash text,
   modified_at timestamptz, indexed_at timestamptz)` — Second Brain metadata ONLY, never content.

`cloudos/db.py` interface (Agent 4; consumers 2/3/8/9 code against EXACTLY this):
```python
def get_pool() -> psycopg_pool.ConnectionPool   # lazy singleton from settings.database_url
@contextmanager
def get_conn():                                  # yields a pooled psycopg connection
def migrate(conn=None) -> list[str]              # apply db/migrations/*.sql in filename order,
                                                 # advisory-locked, idempotent via schema_migrations
def healthcheck() -> bool                        # SELECT 1, never raises
def reset_pool() -> None                         # test helper
```
Both api and worker call `migrate()` at startup (idempotent). Rows are returned as dicts
(`psycopg.rows.dict_row`).

## 6. Shared code contract — src/cloudos/contracts.py (written by lead; import, don't redefine)

```python
class JobStatus(str, Enum): QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, BLOCKED
class PrivacyLabel(str, Enum): PUBLIC, INTERNAL, PERSONAL, SENSITIVE, SECRET
class ErrorCode(str, Enum):
    LIMIT_REACHED, QUOTA_EXHAUSTED, PAID_DISABLED, MODEL_NOT_ALLOWED,
    PRIVACY_BLOCKED, SECRET_DETECTED, DEPENDENCY_UNAVAILABLE, VALIDATION_ERROR, INTERNAL_ERROR
@dataclass RouteRequest: prompt:str, task:str="general", privacy_label:PrivacyLabel=INTERNAL,
    max_tokens:int=1024, model_hint:str|None=None, meta:dict
@dataclass RouteResult: ok:bool, text:str|None, model:str|None, level:int|None,
    error_code:ErrorCode|None, error:str|None, prompt_tokens:int=0, completion_tokens:int=0, cost_usd:float=0.0
class CloudOSError(Exception): .code: ErrorCode, .message, .details: dict
```

Error convention everywhere (API JSON, job.error_code, events.code):
`{"error": {"code": "<ErrorCode>", "message": str, "details": {}}}`. Secrets NEVER appear
in any error, log, or event — metadata only (pattern name, source path, hash).

## 7. Agent API contract (Agent 2) — bearer auth `Authorization: Bearer $AGENT_API_TOKEN`

```
GET  /healthz          → 200 {"status":"ok","db":true|false}   (no auth)
POST /v1/jobs          {type, payload, priority?, run_at?, max_attempts?} → 201 {job}
GET  /v1/jobs/{id}     → {job}
GET  /v1/jobs?status=&type=&limit=  → {jobs:[...]}
POST /v1/jobs/{id}/cancel → {job}
POST /v1/agent/invoke  {prompt, task?, privacy_label?, model_hint?} → RouteResult JSON
                       (calls cloudos.router.route(); privacy gate enforced inside router)
GET  /v1/runs?limit=   → compact agent_runs rows
GET  /v1/quota         → today's quota_usage + budgets + fail-closed status
POST /v1/webhooks/n8n  {event, data} — HMAC or bearer validated; enqueues a job
```

## 8. Worker contract (Agent 3)

- Poll loop: claim ONE job (`FOR UPDATE SKIP LOCKED`, status=queued, run_at<=now, order by
  priority, run_at), set running/locked_by=INSTANCE_NAME, dispatch by `type` to a handler
  registry `cloudos.worker.handlers.HANDLERS: dict[str, Callable[[Job], dict]]`.
- Built-in handlers: `noop` (returns payload), `ai.generate` (calls router.route),
  `sb.reindex` (calls second_brain.reindex), `retention.prune` (below), `notify.flush`.
- Failure: attempts+=1; retry with backoff (run_at = now + 2^attempts min) until max_attempts
  → status failed. CloudOSError(LIMIT_REACHED/QUOTA_EXHAUSTED/PRIVACY_BLOCKED/SECRET_DETECTED/
  PAID_DISABLED) ⇒ status **blocked** (no retry), persist error_code, write event, notify owner.
- Retention (`cloudos.worker.retention.prune(dry_run)`): uses the four retention env vars
  (CONFIGURABLE, not hardcoded); reports per-table {selected, deleted, failures}; dry-run mode
  logs what WOULD be deleted; emits metrics event before pruning. Never prunes active/blocked
  jobs; failed jobs kept FAILED_JOB_RETENTION_DAYS; agent_runs kept AGENT_RUN_RETENTION_DAYS.
- Worker schedules `retention.prune` daily (enqueue if none pending) — 1–3 small DB
  interactions/day is also our legitimate Supabase activity. NO once-per-minute keepalives.

## 9. AI Router contract (Agent 8) — `cloudos.router.route(req: RouteRequest) -> RouteResult`

```
L0 deterministic code (task registry: e.g. summarize→trivial heuristics? NO — L0 means:
   if a task type has a registered deterministic implementation, use it; else escalate)
L1 Cloudflare Workers AI — ONLY models in free_model_allowlist.json; requires CF creds,
   else skip level. Budget: quota_usage('workers_ai') < WORKERS_AI_DAILY_BUDGET, else fail closed.
L2 Gemini free — REQUIRES privacy gate pass: privacy.gate(content, PrivacyLabel) must return
   ALLOW. PUBLIC→allow; INTERNAL→allow only if explicitly marked external_ok; PERSONAL/
   SENSITIVE/SECRET→block. Secret scan runs on the FULL outbound payload before send;
   detection ⇒ SECRET_DETECTED (block, safe metadata only, notify). Budget:
   quota_usage('gemini') < GEMINI_DAILY_REQUEST_BUDGET, else fail closed.
L3 paid — PERMANENTLY DISABLED: if reached (ALLOW_PAID_AI=false always) raise
   CloudOSError(PAID_DISABLED). model_hint naming a non-allowlisted model ⇒ MODEL_NOT_ALLOWED.
```
Every route() call writes ONE compact agent_runs row (hashes, tokens, cost_usd=0).
Exhausted budget ⇒ CloudOSError(QUOTA_EXHAUSTED) → caller (worker) blocks task, notifies.
Provider clients live in `cloudos/router/providers/{workers_ai,gemini}.py`, httpx, injectable
transport for tests. No SDKs.

## 10. Privacy contract (Agent 10) — `cloudos.privacy`

- `classify(path:str|None, frontmatter:dict|None, default=INTERNAL) -> PrivacyLabel`:
  deterministic: explicit `privacy:` frontmatter wins → path rules from privacy.yaml
  (blocked globs ⇒ SENSITIVE, allowed globs ⇒ PUBLIC) → default. NEVER AI-decided.
- `gate(content:str, label:PrivacyLabel) -> GateDecision(allowed:bool, reason:str)`:
  label routing per §9-L2 AND secret scan. Both must pass.
- `scan_secrets(text:str) -> list[SecretHit(pattern_name, span, redacted_sample)]`:
  deterministic regex: AWS/GCP/Azure key shapes, generic `(api[_-]?key|token|secret|passw)`
  assignments, PEM blocks, JWTs, connection strings (postgres://, mongodb://, amqp://),
  Authorization/Bearer headers, .env-style KEY=value with high-entropy value, Slack/GitHub/
  Stripe token prefixes. Never log the secret itself.
- config/privacy.yaml keys (FIXED): `default_label`, `external_ai: {allowed: [globs], blocked: [globs]}`,
  `frontmatter_key: privacy`, `internal_external_ok_key: external_ok`.

## 11. Second Brain contract (Agent 9) — `cloudos.second_brain`

Canonical store = Git/Markdown at SECOND_BRAIN_PATH (this machine: ../AI-Second-Brain).
Supabase holds ONLY sb_index metadata. Functions:
- `reindex(db) -> {scanned, indexed, skipped}` — walk *.md, parse frontmatter, classify()
  each file, upsert sb_index (path, title, label, content_hash, mtime). Content NEVER uploaded.
- `get_context(query:str, max_files=5, max_chars=8000, for_external:bool=False) -> ContextBundle`
  — keyword/path match over sb_index + local read; when for_external=True, include ONLY files
  whose label passes gate() and run scan_secrets on the assembled bundle.
- `read_note(path) -> (frontmatter, body)`; refuse paths outside SECOND_BRAIN_PATH.

## 12. n8n contract (Agent 5)

Official `docker.n8n.io/n8nio/n8n` image (arm64 OK), SQLite backend (NOT our Postgres —
protects the 500 MB budget), basic auth via env. Deliver as importable JSON in n8n/:
- `owner_notify.json` — webhook → email/Discord/Telegram-agnostic stub (documented).
- `daily_ops.json` — cron (1×/day) → POST /v1/jobs {type: retention.prune} + GET /v1/quota
  → notify if fail-closed. This IS the legitimate daily Supabase activity (§8).
- `health_alert.json` — webhook from Cloudflare health cron → notify on origin-down.
Docs: n8n/README.md — import steps, env, memory cap (--max-old-space-size), how n8n is
EXCLUDED in gcp-minimal mode.

## 13. Cloudflare contract (Agent 7)

`cloudflare/ingress-worker/` (plain JS, no build step): routes `/hook/*` (HMAC-validated
webhooks → forward to origin /v1/webhooks/n8n), `/api/*` (bearer check → proxy to origin
allowlisted paths), `/ping` (health). Scheduled handler (cron, e.g. */15): fetch origin
/healthz; on failure POST NOTIFY n8n webhook. wrangler.toml: free plan, NO paid features,
respect CPU-ms limits — no AI orchestration in the Worker. Workers AI usage happens from
the VM via REST (Agent 8), NOT inside this Worker. Document deploy (wrangler login/deploy)
— do not deploy (owner credentials required).

## 14. Infra contract (Agent 6)

- One Dockerfile (python:3.12-slim, multi-arch notes for buildx amd64+arm64), non-root user.
- docker-compose.yml (local): db(postgres:16-alpine, healthcheck) + agent-api + worker + n8n.
  Memory limits: api 512m, worker 1g, n8n 768m, db 512m — leave headroom (§2 OCPU/12 GB
  assumption, prefer ONE A1 VM, plan ~8 GB used).
- docker-compose.oracle.yml: NO db service (DATABASE_URL→Supabase), restart: always.
- docker-compose.gcp-minimal.yml: agent-api + worker ONLY (~1 GB RAM), no n8n.
- infra/oracle/: setup.sh (OCI CLI instance provisioning notes/commands), bootstrap.sh
  (fresh Ubuntu ARM VM → docker install → clone repo → .env from template → restore.sh →
  compose up → health check), deploy.sh (pull+rebuild+up), backup.sh (pg_dump-equivalent of
  Supabase state? no — Supabase survives; backup = local volumes + .env → tarball to object
  storage/git-ignored dir), restore.sh (inverse), RUNBOOK.md (reclamation recovery: VM
  disappears → knowledge survives in Git, state in Supabase, code in GitHub → new VM →
  bootstrap.sh → operational). ARM64_COMPAT.md table: n8n/Python/FastAPI/psycopg/postgres →
  PASS/FAIL with image tags verified.
- NO artificial-utilization anywhere.

## 15. Phases & gates (docs/PHASES.md tracks status)

P1 local docker (api+worker+db+SB) → P2 Supabase → P3 n8n → P4 router+privacy →
P5 Oracle → P6 Cloudflare → P7 recovery test. Each requires PASS before next.
P2/P5/P6 require owner credentials — code+docs delivered, gate marked BLOCKED-ON-OWNER.

## 16. Testing conventions

pytest; unit tests must run WITHOUT a live DB (skip with `@pytest.mark.skipif(not os.getenv
("DATABASE_URL"))` for integration tests). Router/privacy tests use injected httpx
transports — never call real providers in tests. Each agent: tests/test_<area>*.py only.
