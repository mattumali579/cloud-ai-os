# Supabase — Phase 2 procedure (structured state, $0/month)

Supabase (free tier) is the durable home of Cloud AI OS **structured state**:
`jobs`, `agent_runs`, `events`, `notifications`, `quota_usage`, `sb_index`.
Hard budget: **500 MB** (`SUPABASE_DB_BUDGET_MB=500`). Compute is disposable;
this database is what survives VM reclamation (contract §0, §1).

Phase gate: P2 requires owner credentials — steps below are BLOCKED-ON-OWNER
until a project exists.

## What lives here — and what NEVER does

| Lives in Supabase | NEVER in Supabase |
|---|---|
| Job queue rows (`jobs`: status, payload, timestamps) | Second Brain content (Markdown bodies stay in Git) |
| Compact run metadata (`agent_runs`: hashes, token counts, `cost_usd = 0`) | Full prompts, outputs, or assembled context (hashes only) |
| Short-TTL operational events, notification outbox | Secrets, API keys, `.env` contents |
| Per-provider quota counters (`quota_usage`) | File bodies of any kind |
| Second Brain **metadata** (`sb_index`: path, title, label, content hash) | Anything you could not afford to have leak from a third-party DB |

Source-of-truth hierarchy: CODE→GitHub, KNOWLEDGE→Git/Markdown,
STRUCTURED STATE→Supabase, TEMPORARY COMPUTE STATE→VM.

## 1. Create the free project

1. Sign in at https://supabase.com → **New project** (free plan — do not enter
   billing details; `ALLOW_PAID_INFRASTRUCTURE=false` is an invariant).
2. Pick the region closest to the Oracle A1 VM.
3. Generate a strong database password and store it in your password manager —
   it appears inside `DATABASE_URL` and must never be committed.

## 2. Get the connection string (5432 vs 6543 — it matters)

Project → **Connect**. Supabase offers three flavors:

| Flavor | Host / port | Mode | Use for |
|---|---|---|---|
| Direct | `db.<ref>.supabase.co:5432` | session | migrations, admin SQL (note: often IPv6-only on free tier) |
| Pooler, session mode | `aws-0-<region>.pooler.supabase.com:5432` | session | **recommended for migrations** (IPv4-friendly) and fine for runtime |
| Pooler, transaction mode | `aws-0-<region>.pooler.supabase.com:6543` | transaction | runtime option for many short-lived clients |

**Migrations MUST use session mode (port 5432).** The migration runner holds a
session-level `pg_advisory_lock`, which transaction pooling does not preserve —
lock and unlock can land on different backend sessions.

**Runtime note (6543, transaction mode):** `cloudos.db` opens few, long-lived
pooled connections (max 4) so session mode on 5432 is the simple default for
api + worker too. If you do point runtime at 6543, it works: the pool creates
connections with `prepare_threshold=None` (no server-side prepared statements,
the classic transaction-pooler failure). Just never run
`scripts/apply_migrations.py` through 6543.

## 3. Run migrations against Supabase

From the repo root, point `DATABASE_URL` at the **session-mode** string:

PowerShell:

```powershell
$env:DATABASE_URL = "postgresql://postgres.<project-ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres"
python scripts/apply_migrations.py --dry-run   # expect: pending: 001_init
python scripts/apply_migrations.py             # expect: applied: 001_init
python scripts/apply_migrations.py             # expect: applied: none (schema is up to date)
```

bash:

```bash
DATABASE_URL="postgresql://postgres.<project-ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres" \
  python scripts/apply_migrations.py
```

The script exits nonzero on any failure (2 = configuration, 1 = connection/SQL).
`migrate()` is also called by api and worker at startup, so a fresh deploy
self-heals — the script exists for explicit, observable cutover.

## 4. Swap .env

In `.env` (local dev keeps `ENVIRONMENT=local` + the docker `db` service;
Oracle uses `docker-compose.oracle.yml`, which has **no** db container):

```
DATABASE_URL=postgresql://postgres.<project-ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Restart api + worker. Verify: `GET /healthz` returns `{"status":"ok","db":true}`.

## 5. 500 MB budget — monitoring and the retention guard

### Monitoring SQL (run in the Supabase SQL editor, or via psql)

Whole database:

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size,
       pg_database_size(current_database())                  AS db_bytes,
       round(100.0 * pg_database_size(current_database()) / (500 * 1024 * 1024), 1)
           AS pct_of_500mb_budget;
```

Per-table (find what is growing):

```sql
SELECT relname                                     AS table,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       pg_total_relation_size(c.oid)              AS total_bytes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC;
```

Thresholds: **investigate at 350 MB (70%)**, **act at 400 MB (80%)** — never
let it reach 500 MB (Supabase enforces its own limit; the system must fail
closed before the provider does).

### Retention IS the guard

Growth is bounded by the worker's daily `retention.prune` job (§8), driven by
env vars — tighten these to shrink the database, do not hand-delete first:

```
EVENT_RETENTION_DAYS=7          COMPLETED_JOB_RETENTION_DAYS=7
AGENT_RUN_RETENTION_DAYS=14     FAILED_JOB_RETENTION_DAYS=30
```

Approaching budget checklist:
1. Confirm `retention.prune` actually ran (recent `jobs` row / `events`).
2. Run the per-table query; the offender is almost always `events` or
   `agent_runs` (if `agent_runs` is large per-row, someone is violating
   COMPACT ONLY — fix the writer, it must store hashes not content).
3. Reduce the retention env vars, redeploy, enqueue `retention.prune`.
4. Note: plain `VACUUM` (autovacuum runs it) makes dead space reusable but
   does not shrink the on-disk size immediately — deleted space is reclaimed
   for new rows, which is what the budget math needs.

## 6. NO-KEEPALIVE policy (explicit)

- Legitimate Supabase traffic is **daily ops only: 1–3 interactions/day** —
  the n8n `daily_ops` cron (enqueue `retention.prune`, read `/v1/quota`) plus
  normal job activity.
- **Never** add per-minute pings, dummy writes, fake hashes, or synthetic
  traffic to look "active" or defeat provider idle-reclamation (§0). If
  Supabase pauses a free project for inactivity, that is a recoverable state:
  restore it from the dashboard — data is intact. Design for recovery, do not
  manufacture utilization.

## 7. Failure modes

| Failure | Detection | Response |
|---|---|---|
| Supabase unavailable (paused, outage, network) | `healthcheck()` → false; `/healthz` shows `db:false`; pool timeouts; worker claim errors | Fail closed: api serves 200 on `/healthz` with `db:false`, job processing halts (nothing claimable), no data loss — queue state is IN Supabase. If paused for inactivity: restore project in dashboard, traffic resumes. Compute keeps retrying; owner notified via Cloudflare health cron path. |
| Supabase read-only / writes rejected (e.g. provider limit exceeded) | Inserts/updates raise; migrate() fails nonzero; healthcheck (read) may still pass while job claims error | Fail closed: stop the affected work, surface `DEPENDENCY_UNAVAILABLE`-coded errors in logs/events (locally if DB won't take writes), notify owner via webhook (n8n, not DB-dependent). Free up space (§5 checklist) — never upgrade the plan. |
| DB approaching 500 MB budget | Monitoring SQL ≥ 350 MB (70%); daily ops quota report | Tighten retention env vars, run `retention.prune`, verify per-table sizes drop. At 80%: treat as incident — audit for COMPACT-ONLY violations (content in `agent_runs.meta`, oversized `payload`). Never pay for more storage. |
