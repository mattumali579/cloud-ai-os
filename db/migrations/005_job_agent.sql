CREATE TABLE IF NOT EXISTS job_applications (
    job_id text PRIMARY KEY,
    company text NOT NULL,
    title text NOT NULL,
    location text NOT NULL DEFAULT '',
    category text NOT NULL DEFAULT '',
    description_hash text NOT NULL,
    job_url text NOT NULL,
    application_url text NOT NULL DEFAULT '',
    ats text NOT NULL DEFAULT 'generic',
    ats_job_id text NOT NULL DEFAULT '',
    requisition_id text NOT NULL DEFAULT '',
    salary text NOT NULL DEFAULT '',
    discovered_at timestamptz NOT NULL DEFAULT now(),
    job_score integer NOT NULL DEFAULT 0 CHECK (job_score BETWEEN 0 AND 100),
    score_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    application_complexity integer NOT NULL DEFAULT 0,
    resume_variant text NOT NULL DEFAULT '',
    tailored_resume_path text NOT NULL DEFAULT '',
    status text NOT NULL CHECK (status IN ('discovered','rejected','qualified','applying','submitted','skipped','failed','duplicate')),
    skip_reason text NOT NULL DEFAULT '',
    applied_at timestamptz,
    confirmation_id text NOT NULL DEFAULT '',
    confirmation_text text NOT NULL DEFAULT '',
    last_attempt timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_job_applications_ats_job
    ON job_applications (company, ats, ats_job_id) WHERE ats_job_id <> '';
CREATE INDEX IF NOT EXISTS idx_job_applications_status_score
    ON job_applications (status, job_score DESC, discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_applications_description_hash
    ON job_applications (description_hash);

CREATE TABLE IF NOT EXISTS job_agent_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode text NOT NULL,
    dry_run boolean NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb
);

ALTER TABLE job_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_agent_runs ENABLE ROW LEVEL SECURITY;
