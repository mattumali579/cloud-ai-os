# Cloud AI OS Project Instructions

- This repository is Cloud AI OS, the execution and infrastructure layer.
- Do not modify the AI Second Brain unless explicitly instructed.
- Execute rather than merely explain when the requested outcome is clear.
- Make reasonable assumptions for reversible local changes.
- Ask only when credentials, destructive actions, financial cost, or irreversible external actions require owner input.
- Inspect real errors before proposing fixes.
- Test every meaningful change.
- Never claim something is deployed merely because files or configuration exist.
- Never call something cloud or 24/7 until it works with the Windows laptop powered off.
- Keep secrets outside Git.
- Preserve zero-unapproved-paid-inference behavior.
- Prefer minimal patches over new architecture.

## Creative and outreach rules

For any advertising, social creative, product creative, affiliate creative, or static-ad task, you MUST read and follow `docs/CREATIVE_QUALITY_STANDARD.md` before planning or producing assets.

For outreach, lead-gen, email, SMS, or campaign execution, you MUST read and follow `docs/OUTREACH_EXECUTION_RULES.md`. An explicit outreach assignment from the owner counts as approval to send after sender readiness is verified; do not ask for duplicate approval.

The professional 2026-09-22 GOOD reference is the baseline. The first flat/template batch is a negative example and should be rejected unless explicitly requested.

## Cursor Cloud specific instructions

- Python dependencies live in `/opt/cloudos-venv`. `pytest`, `uvicorn`, and `job-agent` are on `PATH`. The venv adds the repo root to `sys.path` so `pytest` can import top-level scripts such as `outreach_sender`.
- Use local PostgreSQL 16. Do not use `infra/docker/docker-compose.yml` on this VM: that file bind-mounts Windows `%USERPROFILE%` paths. Database URL: `postgresql://cloudos:cloudos@127.0.0.1:5432/cloudos`.
- On boot, Postgres starts and the Agent API listens on port 8080. `GET /healthz` is open. Authenticated routes use `Authorization: Bearer change-me-long-random` unless `AGENT_API_TOKEN` is already set.
- Unit tests: `pytest`. Database tests: `DATABASE_URL=postgresql://cloudos:cloudos@127.0.0.1:5432/cloudos pytest tests/test_db_integration.py tests/test_worker_integration.py`.
- Cloudflare ingress worker (no package install): from `cloudflare/ingress-worker`, run `node --test test/index.test.mjs`.
- `tests/test_host_worker_boot.py::test_native_worker_starts_api_only_when_loopback_api_is_absent` asserts a Windows path and fails on Linux. Job-agent ranking and safety tests read gitignored `data/applicant-profile.yaml` and `data/application_answers.yaml`, which are not in this repo.
