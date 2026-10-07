-- 011_google_reviews_campaign.sql
-- Campaign-aware, Claude-authored copy storage for the Google Reviews / Maps
-- offer. This extends the existing sender and canonical company database; it
-- does not create a second lead store or sending path.

ALTER TABLE outreach_queue ADD COLUMN IF NOT EXISTS campaign text NOT NULL DEFAULT 'legacy';
ALTER TABLE outreach_queue ADD COLUMN IF NOT EXISTS copy_source text NOT NULL DEFAULT 'python-generated';
ALTER TABLE outreach_queue ADD COLUMN IF NOT EXISTS evidence jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS outreach_campaign_copy (
    company_id    uuid NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    campaign      text NOT NULL,
    step          smallint NOT NULL CHECK (step BETWEEN 0 AND 5),
    subject       text NOT NULL DEFAULT '',
    body          text NOT NULL,
    copy_version  text NOT NULL,
    evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,
    qa_problems   jsonb NOT NULL DEFAULT '[]'::jsonb,
    qa_passed_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, campaign, step)
);
CREATE INDEX IF NOT EXISTS idx_campaign_copy_ready
    ON outreach_campaign_copy (campaign, step, qa_passed_at)
    WHERE qa_passed_at IS NOT NULL;

ALTER TABLE outreach_campaign_copy ENABLE ROW LEVEL SECURITY;

