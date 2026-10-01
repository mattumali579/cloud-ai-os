# Revenue OS checkpoint — 2026-10-01

## Completed
- Added `docs/revenue_os_master_prompt.md` as the master autonomous revenue contract.
- Rebuilt `n8n/revenue_os_main.json` into one visual manager workflow with:
  - persistent money goal
  - state/proof loading
  - next-money-action decision
  - exact bottleneck count/details
  - Codex research
  - Codex build-spec planning
  - bounded Claude Code build worker
  - independent verification
  - troubleshooter bot
  - two-failure architecture-change rule
  - lead discovery / dedupe / QA / send / reply paths
  - important-event notification path
  - automatic 10-minute continuation loop
- Extended `src/cloudos/revenue_os.py` with safe whitelisted actions:
  `state, decide, research, plan, build, troubleshoot, notify, product_verify, lead_status, lead_cycle, outreach_qa, reply_check, send`.
- Goal is persisted into `revenue_os_state`.
- Research uses subscription-authenticated Codex CLI with web search and writes `docs/revenue_os_competitor_teardown.md` when run.
- Product implementation is handed to the existing bounded Claude worker and independently re-tested.
- Troubleshooter records exact failure evidence and changes approach after repeated failure.
- Important notifications use the existing AgentMail notification ledger; `OWNER_NOTIFY_EMAIL` can route them to the owner's chosen mailbox.

## Verification
GitHub Actions workflow: `Revenue OS tests`
Run ID: `36926994025`
Conclusion: `success`

Verified in CI:
- Python compilation
- n8n workflow JSON parsing
- troubleshooter node present
- Revenue OS controller tests
- existing bounded Claude worker tests

## Current blockers — 2
1. **N8N_RUNTIME_OFFLINE**
   - Layer: SERVICE
   - Evidence: n8n/Docker was reported offline locally; no live workflow import/activation/execution ID exists.
   - Owner: runtime
   - Next action: start the existing Docker/n8n service, import the current `n8n/revenue_os_main.json`, activate it, and run one manual bootstrap execution.
   - Requires owner/local-machine access: yes.

2. **IMPORTANT_EMAIL_NOTIFICATION_CREDENTIAL**
   - Layer: SERVICE
   - Evidence: AgentMail key was reported not saved in the running service. The notification ledger can queue notices, but provider delivery cannot be claimed without the key (or another configured mail transport).
   - Owner: credential/config
   - Next action: provide the existing AgentMail key to the runtime secret/env and set `OWNER_NOTIFY_EMAIL` to the desired Hostinger/owner mailbox.
   - Requires owner credential action: yes.

## Next required action
Start the existing n8n runtime and run the master workflow once. Acceptance criterion: a real n8n execution ID reaches LOAD STATE → DECIDE → RESEARCH (or current highest-priority action) and writes a Revenue OS evidence record. Then let the scheduled loop continue without another human "go".
