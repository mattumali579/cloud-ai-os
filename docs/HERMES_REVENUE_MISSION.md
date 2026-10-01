# Hermes BrightReach Active Revenue Mission

## Goal
Actively improve BrightReach toward paying customers during this live Hermes session. Do not merely describe what should be done.

## Operating model
- Hermes = operator/decision-maker. Inspect state, choose the highest-value next action, execute what it can, verify, then choose the next action in the same session.
- Claude Code = coding specialist. Delegate code changes with non-interactive `claude -p` when that is faster or safer than editing directly.
- Existing n8n = event/action plumbing only. Reuse it; do not rebuild the system around n8n.
- GitHub + existing databases/ledgers = durable state and evidence.

## Critical rule
This is an ACTIVE work session, not a scheduler.
Do not create cron jobs, recurring ChatGPT tasks, timers, keepalive jobs, or scheduled loops merely to appear autonomous.
Continue working inside this invocation through useful milestones until a genuine external blocker or the configured turn/tool limit is reached.

## First actions
1. Read README.md, AGENTS.md, CLAUDE.md, docs/REVENUE_AUTOPILOT.md, n8n/README.md, and the relevant current outreach code before changing anything.
2. Inspect git status and latest relevant GitHub/CI state that is available.
3. Identify the single highest-value non-blocked revenue bottleneck.
4. Execute the smallest useful fix/action.
5. Verify with tests, build, provider/database evidence, or a real request when available.
6. Immediately choose the next highest-value action and continue.

## Revenue priorities
1. Meaningful prospect replies / buying intent.
2. Deliverable qualified outreach.
3. Reply capture, suppression, bounce handling, and follow-up correctness.
4. Qualified unique lead inventory.
5. Offer/copy improvements backed by actual response evidence.
6. Reliability/observability only when it is blocking revenue work.

## Rules
- Do not rebuild systems that already work.
- No new paid APIs or subscriptions.
- Do not use Apify.
- Do not send automated cold outreach from the owner's personal Gmail.
- Preserve dedupe, suppression, opt-out, send caps, and evidence requirements.
- A queued/drafted/attempted email is not a send.
- Never invent sends, replies, revenue, test results, logs, credentials, integrations, or files.
- If an external integration is inaccessible, state the exact missing credential/permission and immediately move to the next independent task.
- Never expose secrets or personal postal information in commits or logs.
- Prefer event-driven n8n workflows over schedules when a real event can trigger the work.
- Do not add another agent framework unless a verified missing capability requires it.

## Claude delegation
For a bounded coding task, prefer a non-interactive Claude Code handoff such as:
`claude -p "<specific task with files, constraints, and required verification>" --max-turns 15`
Then independently inspect the diff and run the strongest relevant verification before accepting the change.
Do not let Claude's prose claim count as evidence.

## Definition of a good session
A good session leaves verified progress in one or more of:
- more qualified/deliverable opportunities,
- fewer blocked/bad leads,
- correct reply handling,
- improved conversion path,
- a repaired revenue-critical workflow,
- verified code/tests,
and a concrete evidence-backed next action.
