-- 009_outreach_sender.sql - the Hostinger SENDER on top of the lead engine (006)
-- and the reply layer (007/008).
--
-- Rules the schema itself enforces:
--   * one queue row per (company, step): a first touch or a given follow-up can
--     exist exactly once, ever - retries reuse the row, they never add one.
--   * the RFC Message-ID is chosen and saved BEFORE the SMTP call, so a crash
--     after the provider accepted the mail is detectable (the id is in Sent)
--     and never becomes a silent second send.
--   * a row in 'claimed' belongs to one worker (FOR UPDATE SKIP LOCKED claim).
--   * companies.ready_at is stamped the moment a company first becomes
--     outreach_ready - the daily 300 target counts THIS, not discoveries.

-- ------------------------------------------------------------ ready stamp
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ready_at timestamptz;

CREATE OR REPLACE FUNCTION companies_stamp_ready_at() RETURNS trigger AS $$
BEGIN
    IF NEW.outreach_status = 'outreach_ready' AND NEW.ready_at IS NULL THEN
        NEW.ready_at := now();
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_companies_ready_at ON companies;
CREATE TRIGGER trg_companies_ready_at BEFORE INSERT OR UPDATE OF outreach_status ON companies
    FOR EACH ROW EXECUTE FUNCTION companies_stamp_ready_at();

-- companies that were already ready before this migration keep their best-known time
UPDATE companies SET ready_at = coalesce(handed_off_at, updated_at, discovered_at)
 WHERE ready_at IS NULL AND outreach_status IN ('outreach_ready', 'handed_off');
CREATE INDEX IF NOT EXISTS idx_companies_ready_at ON companies (ready_at) WHERE ready_at IS NOT NULL;

-- ------------------------------------------------------------------ queue
CREATE TABLE IF NOT EXISTS outreach_queue (
    queue_id          bigserial PRIMARY KEY,
    company_id        uuid NOT NULL REFERENCES companies (company_id) ON DELETE RESTRICT,
    step              smallint NOT NULL CHECK (step BETWEEN 0 AND 5),   -- 0 = first touch, 1.. = follow-ups
    recipient         text NOT NULL,
    subject           text NOT NULL DEFAULT '',
    body              text NOT NULL DEFAULT '',
    copy_variant      text,
    thread_root       text,              -- Message-ID of the first touch (follow-ups thread under it)
    state             text NOT NULL DEFAULT 'queued' CHECK (state IN (
                        'queued',        -- waiting for due_at
                        'claimed',       -- one worker is sending it right now
                        'sent',          -- provider accepted it; recorded in outreach_messages
                        'failed',        -- permanent failure (bad address, retries used up)
                        'cancelled',     -- guard refused / reply / unsubscribe / bounce stopped it
                        'ambiguous')),   -- a worker died mid-send and Sent has no copy: NEVER auto-resent
    due_at            timestamptz NOT NULL DEFAULT now(),
    message_id_header text UNIQUE,       -- chosen before the SMTP call
    claimed_by        text,
    claimed_at        timestamptz,
    attempts          int NOT NULL DEFAULT 0,
    sent_at           timestamptz,
    provider_response text,
    outreach_message_id uuid REFERENCES outreach_messages (message_id),
    stop_reason       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, step),
    CHECK (state <> 'sent' OR (sent_at IS NOT NULL AND message_id_header IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_outreach_queue_due ON outreach_queue (due_at) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS idx_outreach_queue_sent ON outreach_queue (sent_at) WHERE state = 'sent';
CREATE INDEX IF NOT EXISTS idx_outreach_queue_company ON outreach_queue (company_id);

-- ------------------------------------------------- airtable mirror + budget
-- What was last written to Airtable per company, so only real changes cost an API call.
CREATE TABLE IF NOT EXISTS airtable_mirror (
    company_id   uuid PRIMARY KEY REFERENCES companies (company_id) ON DELETE CASCADE,
    record_id    text,
    pushed_hash  text,
    pushed_at    timestamptz
);
CREATE TABLE IF NOT EXISTS airtable_api_usage (
    month   text PRIMARY KEY,       -- 'YYYY-MM' (UTC)
    calls   int NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS airtable_state (
    key        text PRIMARY KEY,    -- e.g. 'record_count'
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------ sender run journal
CREATE TABLE IF NOT EXISTS outreach_sender_runs (
    run_id      bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    mode        text NOT NULL,
    stats       jsonb NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE outreach_queue        ENABLE ROW LEVEL SECURITY;
ALTER TABLE airtable_mirror       ENABLE ROW LEVEL SECURITY;
ALTER TABLE airtable_api_usage    ENABLE ROW LEVEL SECURITY;
ALTER TABLE airtable_state        ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_sender_runs  ENABLE ROW LEVEL SECURITY;
