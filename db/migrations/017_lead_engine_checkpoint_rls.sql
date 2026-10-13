-- 017_lead_engine_checkpoint_rls.sql
-- Supabase's security advisor flags public.lead_engine_checkpoint because row
-- level security is off, so the anon key can read and write it through the
-- Data API. Enable RLS and add no policies, matching the other Cloud AI OS
-- tables. ENABLE ROW LEVEL SECURITY is a no-op when row security is already
-- on, so applying this file again does not fail or change the table.

ALTER TABLE public.lead_engine_checkpoint ENABLE ROW LEVEL SECURITY;
