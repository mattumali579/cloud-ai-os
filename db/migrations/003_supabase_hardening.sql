-- Keep Cloud AI OS tables private when public is exposed through Supabase's
-- Data API. The Oracle services connect directly as the database owner, so no
-- anon/authenticated policies are required or intended.

ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE quota_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE sb_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_status ENABLE ROW LEVEL SECURITY;

-- Covers agent_runs.job_id for joins and parent-row deletion checks.
CREATE INDEX IF NOT EXISTS idx_agent_runs_job_id ON agent_runs (job_id);
