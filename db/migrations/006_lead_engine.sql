-- 006_lead_engine.sql — canonical company-level lead store for the fresh lead
-- generator (src/cloudos/leadgen). A "lead" is a COMPANY: every dedupe key
-- below resolves to exactly one companies row.
--
-- Dedupe keys, strongest first:
--   1. normalized_domain           (unique)
--   2. normalized_name + city + state
--   3. historical_ids               (Airtable record ids etc. from old ledgers)
--   4. contacts.email / normalized_phone   (secondary evidence only)

CREATE TABLE IF NOT EXISTS companies (
    company_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name          text NOT NULL,
    normalized_name       text NOT NULL,
    domain                text,
    normalized_domain     text,
    website               text,
    industry              text,
    city                  text,
    state                 text,
    country               text NOT NULL DEFAULT 'US',
    phone                 text,
    normalized_phone      text,
    discovered_at         timestamptz NOT NULL DEFAULT now(),
    discovery_source      text NOT NULL,
    qualification_status  text NOT NULL DEFAULT 'PENDING'
                          CHECK (qualification_status IN ('HIGH','MEDIUM','LOW','REJECT','PENDING')),
    qualification_reason  text,
    personalization       jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_contacted_at    timestamptz,
    last_contacted_at     timestamptz,
    outreach_status       text NOT NULL DEFAULT 'new'
                          CHECK (outreach_status IN ('new','outreach_ready','handed_off','contacted',
                                                     'replied','bounced','unsubscribed','do_not_contact',
                                                     'not_ready','rejected')),
    handed_off_at         timestamptz,
    handoff_ref           text,
    is_historical         boolean NOT NULL DEFAULT false,
    historical_ids        text[] NOT NULL DEFAULT '{}',
    enriched_at           timestamptz,
    active                boolean NOT NULL DEFAULT true,
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_normalized_domain
    ON companies (normalized_domain) WHERE normalized_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_name_loc
    ON companies (normalized_name, lower(coalesce(city,'')), lower(coalesce(state,'')));
CREATE INDEX IF NOT EXISTS idx_companies_phone
    ON companies (normalized_phone) WHERE normalized_phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_companies_historical_ids
    ON companies USING gin (historical_ids);
CREATE INDEX IF NOT EXISTS idx_companies_outreach
    ON companies (outreach_status, qualification_status, discovered_at);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    uuid NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    email         text NOT NULL,
    email_status  text NOT NULL DEFAULT 'unknown'
                  CHECK (email_status IN ('published','validated','probable','invalid','unknown')),
    role          text,
    source        text,
    source_url    text,
    discovered_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_email ON contacts (lower(email));
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts (company_id);

CREATE TABLE IF NOT EXISTS discovery_history (
    id            bigserial PRIMARY KEY,
    source        text NOT NULL,
    query         text NOT NULL,
    company_id    uuid REFERENCES companies (company_id) ON DELETE SET NULL,
    outcome       text NOT NULL,           -- new | duplicate | rejected | error
    detail        text,
    discovered_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_discovery_history_source ON discovery_history (source, discovered_at);

-- Only provider-confirmed events belong here. Drafted/queued/skipped are NOT sent.
CREATE TABLE IF NOT EXISTS outreach_history (
    id          bigserial PRIMARY KEY,
    company_id  uuid REFERENCES companies (company_id) ON DELETE SET NULL,
    email       text,
    campaign    text,
    sent_at     timestamptz,
    status      text NOT NULL,             -- sent | replied | bounced | unsubscribed | failed
    provider    text,
    dedupe_key  text NOT NULL UNIQUE,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outreach_history_company ON outreach_history (company_id);

-- Every (source, query) combination ever run, so rotation never repeats blindly.
CREATE TABLE IF NOT EXISTS lead_queries (
    source         text NOT NULL,
    query          text NOT NULL,
    industry       text,
    location       text,
    runs           int  NOT NULL DEFAULT 0,
    last_run_at    timestamptz,
    last_results   int,
    last_new       int,
    total_results  int  NOT NULL DEFAULT 0,
    total_new      int  NOT NULL DEFAULT 0,
    last_error     text,
    PRIMARY KEY (source, query)
);

-- Self-healing state per discovery source.
CREATE TABLE IF NOT EXISTS lead_source_health (
    source                text PRIMARY KEY,
    consecutive_failures  int NOT NULL DEFAULT 0,
    total_failures        int NOT NULL DEFAULT 0,
    total_successes       int NOT NULL DEFAULT 0,
    last_success_at       timestamptz,
    last_failure_at       timestamptz,
    last_error            text,
    disabled_until        timestamptz
);

CREATE TABLE IF NOT EXISTS lead_runs (
    run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mode        text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stats       jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Cloud AI OS exposes Supabase through PostgREST; keep these server-side only.
ALTER TABLE companies           ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_history   ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_history    ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_queries        ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_source_health  ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_runs           ENABLE ROW LEVEL SECURITY;
