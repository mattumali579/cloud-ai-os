-- Durable, provider-neutral proof for the visual Revenue OS controller.
-- Payloads contain counts, IDs and command results only: never email copy or credentials.
CREATE TABLE IF NOT EXISTS revenue_os_state (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS revenue_os_evidence (
    evidence_id bigserial PRIMARY KEY,
    execution_key text NOT NULL,
    action      text NOT NULL,
    outcome     text NOT NULL CHECK (outcome IN ('verified', 'blocked', 'failed', 'skipped')),
    proof       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revenue_os_evidence_created ON revenue_os_evidence (created_at DESC);

ALTER TABLE revenue_os_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE revenue_os_evidence ENABLE ROW LEVEL SECURITY;
