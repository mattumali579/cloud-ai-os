-- 016_planner_candidate_index.sql
-- The sender prepares first emails by walking Ready companies that already
-- have Google review facts. Ordering that walk by ready_at and stopping after
-- one small batch needs an index; otherwise the planner seq-scans the whole
-- table and the database cancels the statement (or the job hangs until GitHub
-- kills it). Read-only for existing rows: no data is updated or deleted.

CREATE INDEX IF NOT EXISTS idx_companies_review_ready_at
    ON companies (ready_at DESC NULLS LAST)
    WHERE active
      AND first_contacted_at IS NULL
      AND (personalization ? 'google_reviews')
      AND (personalization ? 'google_rating');
