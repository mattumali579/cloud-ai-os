-- 001_init.sql — Cloud AI OS base schema (contracts/ARCHITECTURE.md §5).
-- Applied by cloudos.db.migrate() inside pg_advisory_lock and recorded in
-- schema_migrations, so each version runs at most once; IF NOT EXISTS is kept
-- as belt-and-braces for manually pre-provisioned databases.
--
-- gen_random_uuid() is built into PostgreSQL 13+ (postgres:16-alpine locally,
-- Supabase in Phase 2 — both qualify). The DO block installs pgcrypto only if
-- the function is somehow missing (older/unusual servers).

DO $$
BEGIN
    PERFORM gen_random_uuid();
EXCEPTION WHEN undefined_function THEN
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
END
$$;

-- ---------------------------------------------------------------------------
-- jobs — the Postgres-backed queue (FOR UPDATE SKIP LOCKED, §8)

CREATE TABLE IF NOT EXISTS jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type          text NOT NULL,
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','succeeded','failed','cancelled','blocked')),
    priority      int  NOT NULL DEFAULT 100,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    result        jsonb,
    error_code    text,
    error         text,
    attempts      int  NOT NULL DEFAULT 0,
    max_attempts  int  NOT NULL DEFAULT 3,
    run_at        timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    locked_by     text,
    locked_at     timestamptz
);

-- Required by §5: supports the worker claim query
-- (status = 'queued' AND run_at <= now() ORDER BY priority, run_at).
CREATE INDEX IF NOT EXISTS idx_jobs_status_run_at_priority
    ON jobs (status, run_at, priority);

-- ---------------------------------------------------------------------------
-- agent_runs — COMPACT ONLY: hashes + token counts + small meta.
-- Never full prompts/outputs/context. cost_usd is pinned to 0 by CHECK:
-- paid AI is permanently disabled (§0, §9 L3).

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id            uuid REFERENCES jobs (id) ON DELETE SET NULL,
    agent             text,
    workflow          text,
    model             text,
    level             int,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text,
    prompt_tokens     int NOT NULL DEFAULT 0,
    completion_tokens int NOT NULL DEFAULT 0,
    cost_usd          numeric NOT NULL DEFAULT 0 CHECK (cost_usd = 0),
    error_code        text,
    error             text,
    input_hash        text,
    output_hash       text,
    meta              jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Supports daily retention pruning (AGENT_RUN_RETENTION_DAYS).
CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs (started_at);

-- ---------------------------------------------------------------------------
-- events — short-TTL operational log (EVENT_RETENTION_DAYS)

CREATE TABLE IF NOT EXISTS events (
    id      bigserial PRIMARY KEY,
    ts      timestamptz NOT NULL DEFAULT now(),
    level   text,
    source  text,
    code    text,
    message text,
    meta    jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Supports daily retention pruning (EVENT_RETENTION_DAYS).
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);

-- ---------------------------------------------------------------------------
-- notifications — owner notification outbox (notify.flush drains undelivered)

CREATE TABLE IF NOT EXISTS notifications (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    severity  text,
    code      text,
    message   text,
    meta      jsonb NOT NULL DEFAULT '{}'::jsonb,
    delivered boolean NOT NULL DEFAULT false
);

-- Supports the notify.flush handler; partial index stays tiny.
CREATE INDEX IF NOT EXISTS idx_notifications_undelivered
    ON notifications (ts) WHERE NOT delivered;

-- ---------------------------------------------------------------------------
-- quota_usage — per-provider daily counters; router fails closed at budget

CREATE TABLE IF NOT EXISTS quota_usage (
    provider text NOT NULL,
    day      date NOT NULL,
    requests int  NOT NULL DEFAULT 0,
    units    int  NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, day)
);

-- ---------------------------------------------------------------------------
-- sb_index — Second Brain METADATA ONLY. Content stays in Git/Markdown; it is
-- NEVER uploaded to Supabase (§11).

CREATE TABLE IF NOT EXISTS sb_index (
    path          text PRIMARY KEY,
    title         text,
    privacy_label text,
    content_hash  text,
    modified_at   timestamptz,
    indexed_at    timestamptz
);
