# Phase gates

Each phase requires PASS before the next begins. Phases needing owner credentials
are delivered as code + docs and marked BLOCKED-ON-OWNER.

| Phase | Scope | Gate | Status |
|---|---|---|---|
| 1 | Local Docker: agent-api, worker, Postgres, Second Brain interface | compose up, /healthz ok, job round-trip, pytest green | **PASS** (2026-08-28: 257 tests green; healthz db:true; noop job queued→succeeded; L0 invoke cost 0.0; compact runs row verified) |
| 2 | Supabase integration (migrations applied, DATABASE_URL swap) | migrations on Supabase, budget check | BLOCKED-ON-OWNER (Supabase project + connection string; see docs/SUPABASE.md — migrations over session mode port 5432) |
| 3 | n8n integration (daily_ops, owner_notify, health_alert workflows) | workflows imported, daily ops fires | PARTIAL PASS (all three imported via CLI into local n8n; activation + AGENT_API_URL/TOKEN env are owner steps, see n8n/README.md) |
| 4 | AI router + privacy gate live | routing levels enforced, failure sims pass | CODE-COMPLETE (all routing/gate failure sims pass in tests + live: MODEL_NOT_ALLOWED 403, no-provider 503, L0 served; real L1/L2 calls need CF/Gemini keys) |
| 5 | Oracle deployment (bootstrap.sh on fresh A1 VM) | system operational on VM | BLOCKED-ON-OWNER (OCI account/instance) |
| 6 | Cloudflare ingress (wrangler deploy) | /ping + proxy + health cron live | BLOCKED-ON-OWNER (CF account) |
| 7 | Recovery/redeployment test (VM recreated → operational) | RUNBOOK executed end-to-end | BLOCKED-ON-OWNER |
