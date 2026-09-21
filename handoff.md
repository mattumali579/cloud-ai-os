# Cloud AI OS Remote Discord Notifications — Handoff

## Primary objective

Fix the existing Cloud AI OS so important Discord notifications reach Matt's phone 24/7 when the Windows laptop is shut down and all local editors, terminals, workers, and services are closed.

Do not rebuild the system or add unrelated features. The first milestone is a verified Discord heartbeat sent by a remote runtime, not by the laptop.

## Success condition

A remotely hosted scheduler and execution function must run without the laptop and send this kind of timestamped message through the existing Discord integration:

> Cloud heartbeat OK — 2026-08-31 02:30 America/Chicago

Code or deployment configuration alone is not success. Obtain cloud-side execution/log evidence and verify that Discord received the message.

## Hard constraints

- Expected monthly cost should be `$0` whenever realistically possible.
- Do not buy, upgrade, or enable paid infrastructure.
- Do not enable services that can silently create usage charges.
- Do not use paid Cloudflare features or paid APIs without explicit approval.
- Oracle is not the default. Use it only if absolutely necessary and only after verifying the exact resource is Always Free eligible and explaining billing risk.
- Prefer, in order:
  1. Cloudflare Workers + Cron Triggers
  2. GitHub Actions scheduled workflows
  3. Existing Supabase scheduled/serverless functionality
  4. Existing remotely hosted free n8n
  5. Oracle Always Free compute only if unavoidable
- Never print, log, or commit Discord webhook URLs, API keys, tokens, passwords, cookies, or other secrets.
- Do not obtain email access through browser-session theft, cookies, passwords, or OAuth bypasses. Use only supported integrations already present.
- Do not delete production data, rotate credentials unnecessarily, or break working workflows.
- Make small, reversible changes and logical commits.
- Preserve the user's existing uncommitted changes.
- Do not repeatedly ask for permission. The user explicitly authorized autonomous work and full repository/cloud inspection, subject to the billing, secret, destructive-action, and irreversible-action limits above.

## Repository

Path: `C:\Users\mattu\cloud-ai-os`

Remote: `https://github.com/mattumali579/cloud-ai-os.git`

Branch observed: `master`

Project instructions in `AGENTS.md` require real verification, minimal patches, secrets outside Git, and no claim of 24/7/cloud operation until it works while Windows is off.

## Verified local findings so far

The repository contains these potential execution paths:

- `cloudflare/ingress-worker/`
  - `wrangler.toml`
  - `src/index.js`
  - tests and package metadata
- `supabase/functions/school-system/`
  - `school.ts`
  - `school.test.mjs`
- `n8n/`
  - `daily_ops.json`
  - `health_alert.json`
  - `owner_notify.json`
- `src/cloudos/`
  - local API/worker code
  - `notify.py`
- `infra/docker/`
  - local/default, Oracle, and GCP compose files
- `infra/oracle/`
  - deployment/bootstrap files and capacity request JSON
- `.env` exists locally and was not printed.
- No conclusion has yet been verified about which remote resources are actually deployed or executing.

Do not infer that the Cloudflare code is remotely executing merely because `cloudflare/` exists. Explicitly distinguish Cloudflare Tunnel from Workers/Cron execution.

## Dirty worktree — preserve these changes

At the start of the audit, `git status --short --branch` showed:

```text
## master
 M .env.example
 M .gitignore
 M infra/docker/docker-compose.oracle.yml
 M infra/docker/docker-compose.yml
 M infra/oracle/bootstrap.sh
?? AGENTS.md
?? db/migrations/003_supabase_hardening.sql
?? db/migrations/004_school_system.sql
?? infra/oracle/a1-capacity-request.json
?? infra/oracle/a1-shape-config.json
?? infra/oracle/e2-micro-capacity-request.json
?? supabase/
```

Assume all of these are user-owned changes unless proven otherwise. Inspect diffs before editing overlapping files. Do not reset, overwrite, delete, or casually include them in commits.

## Audit questions that must be answered with evidence

1. What event is supposed to trigger each Discord notification?
2. What process detects that event?
3. Where does that process currently execute?
4. Which exact component stops when the laptop shuts down?
5. Is Cloudflare only exposing localhost through a Tunnel?
6. Is any Cloudflare Worker actually deployed and executing notification code?
7. Is a Cloudflare Cron Trigger configured remotely?
8. Are Cloudflare Pages or Queues used?
9. Are any GitHub Actions schedules configured and running?
10. Is n8n hosted remotely or only through local Docker/tunnel?
11. Is the Supabase function deployed, and is there a scheduler/cron invoking it?
12. Does Discord webhook delivery work independently of the laptop?
13. Are required secrets present in the chosen cloud runtime?
14. Are there cloud logs proving execution while the laptop was offline?

## Immediate continuation plan

1. Read the relevant source/config files without printing secret values:
   - `cloudflare/ingress-worker/wrangler.toml`
   - `cloudflare/ingress-worker/src/index.js`
   - `cloudflare/README.md`
   - `src/cloudos/notify.py`
   - local worker entrypoints/loops/handlers
   - all three n8n workflow JSON files
   - Docker compose files
   - `supabase/functions/school-system/school.ts`
   - school database migration
   - hidden `.github/workflows/` files, if any
2. Inventory `.env` by key name and state only (`SET`, `EMPTY`, `PLACEHOLDER`); never output values.
3. Inspect authenticated remote state with installed CLIs/APIs:
   - `wrangler whoami`, Worker deployments, routes, secret names, Cron Triggers, and logs
   - GitHub Actions workflow/schedule/run history and repository secret names
   - Supabase project/function/cron/log state
   - n8n hosting endpoint and execution state, if configured
   - Cloudflare Tunnel configuration/process only to determine laptop dependency
4. Trace the current Discord path end-to-end and identify the actual failure before editing.
5. Choose the smallest genuinely free remote platform in the required priority order.
6. Add an easy-to-disable 15- or 30-minute heartbeat schedule (or closest supported free interval).
7. Put the Discord webhook in remote secret storage; never commit it.
8. Deploy and trigger the remote function manually once.
9. Verify cloud logs and Discord receipt. Record exact evidence and timestamp.
10. Only after heartbeat proof, connect existing important notification logic in this order:
    - school email
    - work email
    - important AI/workflow alerts
    - job-related alerts
11. Keep filtering conservative to prevent spam.
12. Run tests and review the final diff before committing only the new scoped changes.

## Tooling note

The normal Windows sandbox repeatedly failed before process launch with:

```text
SetTokenInformation(TokenDefaultDacl) failed: 1344
```

Elevated/read-only execution worked after approval, but some long inspection commands were interrupted. If the error persists, run necessary commands under the already granted full permissions rather than repeatedly asking Matt.

## Supabase-specific instruction already loaded

Because Supabase is in scope, verify current behavior against the current Supabase changelog/docs before implementing a Supabase solution. Prefer the Supabase MCP/CLI for actual remote state. Do not expose `service_role`/secret keys, and verify any deployed change with a real test.

## Required final report format

Use exactly this structure:

```text
REMOTE NOTIFICATIONS
Status: WORKING / PARTIALLY WORKING / NOT WORKING

CURRENT FAILURE:
[1-3 sentences]

WHAT WAS ACTUALLY RUNNING LOCALLY:
[short answer]

WHAT IS NOW RUNNING IN THE CLOUD:
[short answer]

CLOUD PLATFORM:
[Cloudflare Workers / GitHub Actions / etc.]

MONTHLY COST:
$0 expected / explain risk

LAPTOP REQUIRED:
YES / NO

TEST PERFORMED:
[exact test]

DISCORD MESSAGE SENT FROM CLOUD:
YES / NO

FILES CHANGED:
[list]

SECRETS I STILL NEED TO ADD:
[list only if required]

NEXT STEP:
[one action]
```

Then add only this short phone checklist:

- Discord phone notifications enabled
- server/channel not muted
- correct channel notification level
- Android/iPhone OS notifications enabled
- battery/background restrictions allow Discord

