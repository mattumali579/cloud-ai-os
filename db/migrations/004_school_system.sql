-- School notification state for the $0 Supabase Edge/Cron deployment.
-- All tables are server-only: RLS is enabled, anon/authenticated receive no
-- privileges, and the Edge Function uses Supabase's injected service role.

CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
CREATE EXTENSION IF NOT EXISTS pg_net;

CREATE TABLE IF NOT EXISTS school_sync_state (
    id                    smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_sync             timestamptz,
    last_success          timestamptz,
    next_sync             timestamptz,
    assignments_found     integer NOT NULL DEFAULT 0 CHECK (assignments_found >= 0),
    assignments_due_24h   integer NOT NULL DEFAULT 0 CHECK (assignments_due_24h >= 0),
    consecutive_failures  integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    last_error            text,
    outlook_status        text NOT NULL DEFAULT 'not_configured',
    discord_status        text NOT NULL DEFAULT 'not_configured',
    cloud_status          text NOT NULL DEFAULT 'supabase_free_fallback',
    updated_at            timestamptz NOT NULL DEFAULT now()
);

INSERT INTO school_sync_state (id, next_sync)
VALUES (1, date_trunc('hour', now()) + interval '1 hour')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS school_messages (
    message_id             text PRIMARY KEY,
    internet_message_id    text,
    subject                text NOT NULL,
    sender                 text,
    received_at            timestamptz NOT NULL,
    body_preview           text,
    source_web_url         text,
    processed_at           timestamptz NOT NULL DEFAULT now(),
    actionable             boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_school_messages_received_at
    ON school_messages (received_at DESC);

CREATE TABLE IF NOT EXISTS school_assignments (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint            text NOT NULL UNIQUE,
    source_message_id      text NOT NULL REFERENCES school_messages(message_id) ON DELETE CASCADE,
    course                 text NOT NULL,
    task                   text NOT NULL,
    due_at                 timestamptz,
    urgency                text NOT NULL CHECK (urgency IN ('high', 'medium', 'normal')),
    status                 text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'dismissed')),
    source_subject         text NOT NULL,
    source_sender          text,
    source_received_at     timestamptz NOT NULL,
    source_web_url         text,
    notified_at            timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_school_assignments_open_due
    ON school_assignments (due_at) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_school_assignments_course
    ON school_assignments (course);

CREATE TABLE IF NOT EXISTS school_sync_runs (
    id                     bigserial PRIMARY KEY,
    started_at             timestamptz NOT NULL DEFAULT now(),
    finished_at            timestamptz,
    status                 text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    messages_checked       integer NOT NULL DEFAULT 0 CHECK (messages_checked >= 0),
    actionable_found       integer NOT NULL DEFAULT 0 CHECK (actionable_found >= 0),
    assignments_added      integer NOT NULL DEFAULT 0 CHECK (assignments_added >= 0),
    notifications_sent     integer NOT NULL DEFAULT 0 CHECK (notifications_sent >= 0),
    error                  text
);

CREATE INDEX IF NOT EXISTS idx_school_sync_runs_started_at
    ON school_sync_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS school_notifications (
    id                     bigserial PRIMARY KEY,
    assignment_id          uuid REFERENCES school_assignments(id) ON DELETE CASCADE,
    event_type             text NOT NULL,
    dedupe_key             text NOT NULL UNIQUE,
    status                 text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempted_at           timestamptz,
    delivered_at           timestamptz,
    last_error             text,
    created_at             timestamptz NOT NULL DEFAULT now()
);

-- Rotated Microsoft refresh tokens are stored only as AES-GCM ciphertext.
-- The encryption key remains an Edge Function secret and never enters Git/DB.
CREATE TABLE IF NOT EXISTS school_secret_state (
    name                   text PRIMARY KEY,
    ciphertext             text NOT NULL,
    iv                     text NOT NULL,
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS school_runtime_config (
    id                         smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    cron_secret_sha256         text,
    max_monthly_cost_usd       numeric NOT NULL DEFAULT 0 CHECK (max_monthly_cost_usd = 0),
    allow_paid_infrastructure  boolean NOT NULL DEFAULT false CHECK (allow_paid_infrastructure = false),
    allow_paid_ai              boolean NOT NULL DEFAULT false CHECK (allow_paid_ai = false),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

INSERT INTO school_runtime_config (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE school_sync_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_secret_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_runtime_config ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE school_sync_state FROM anon, authenticated;
REVOKE ALL ON TABLE school_messages FROM anon, authenticated;
REVOKE ALL ON TABLE school_assignments FROM anon, authenticated;
REVOKE ALL ON TABLE school_sync_runs FROM anon, authenticated;
REVOKE ALL ON TABLE school_notifications FROM anon, authenticated;
REVOKE ALL ON TABLE school_secret_state FROM anon, authenticated;
REVOKE ALL ON TABLE school_runtime_config FROM anon, authenticated;
REVOKE ALL ON SEQUENCE school_sync_runs_id_seq FROM anon, authenticated;
REVOKE ALL ON SEQUENCE school_notifications_id_seq FROM anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_sync_state TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_messages TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_assignments TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_sync_runs TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_notifications TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_secret_state TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE school_runtime_config TO service_role;
GRANT USAGE, SELECT ON SEQUENCE school_sync_runs_id_seq TO service_role;
GRANT USAGE, SELECT ON SEQUENCE school_notifications_id_seq TO service_role;
