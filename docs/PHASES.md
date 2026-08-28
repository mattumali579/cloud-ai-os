# Phase gates

Each phase requires PASS before the next begins. Phases needing owner credentials
are delivered as code + docs and marked BLOCKED-ON-OWNER.

| Phase | Scope | Gate | Status |
|---|---|---|---|
| 1 | Local Docker: agent-api, worker, Postgres, Second Brain interface | compose up, /healthz ok, job round-trip, pytest green | IN PROGRESS |
| 2 | Supabase integration (migrations applied, DATABASE_URL swap) | migrations on Supabase, budget check | BLOCKED-ON-OWNER (Supabase project + connection string) |
| 3 | n8n integration (daily_ops, owner_notify, health_alert workflows) | workflows imported, daily ops fires | PENDING (code ready after Phase 1) |
| 4 | AI router + privacy gate live | routing levels enforced, failure sims pass | PENDING |
| 5 | Oracle deployment (bootstrap.sh on fresh A1 VM) | system operational on VM | BLOCKED-ON-OWNER (OCI account/instance) |
| 6 | Cloudflare ingress (wrangler deploy) | /ping + proxy + health cron live | BLOCKED-ON-OWNER (CF account) |
| 7 | Recovery/redeployment test (VM recreated → operational) | RUNBOOK executed end-to-end | BLOCKED-ON-OWNER |
