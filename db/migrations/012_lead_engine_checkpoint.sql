-- 012_lead_engine_checkpoint.sql
-- Checkpoint tracking and concurrency lock for unattended lead discovery and contact collection batches.

CREATE TABLE IF NOT EXISTS lead_engine_checkpoint (
    checkpoint_id       text PRIMARY KEY,
    batch_type          text NOT NULL DEFAULT 'backlog_and_discovery',
    last_processed_id   uuid,
    last_processed_at   timestamptz NOT NULL DEFAULT now(),
    is_running          boolean NOT NULL DEFAULT false,
    locked_at           timestamptz,
    lock_token          text,
    items_processed     integer NOT NULL DEFAULT 0,
    stats               jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lead_engine_checkpoint_updated
    ON lead_engine_checkpoint (updated_at);
