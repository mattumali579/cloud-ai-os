# Cloud AI OS

$0/month recurring-cost autonomous AI platform. Fail-closed on every free-tier limit.

Principles: **compute is disposable, data is durable, code is reproducible, AI is
optional, paid services are disabled.**

- Architecture & interface contracts: [`contracts/ARCHITECTURE.md`](contracts/ARCHITECTURE.md)
- Phase gates & status: [`docs/PHASES.md`](docs/PHASES.md)
- Zero-cost audit: [`docs/ZERO_COST_AUDIT.md`](docs/ZERO_COST_AUDIT.md)
- Failure-mode matrix: [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md)
- Oracle recovery runbook: [`infra/oracle/RUNBOOK.md`](infra/oracle/RUNBOOK.md)

## Quick start (local, Phase 1)

```bash
cp .env.example .env          # edit AGENT_API_TOKEN at minimum
docker compose -f infra/docker/docker-compose.yml up -d --build
curl http://localhost:8080/healthz
```

Submit a job:

```bash
curl -X POST http://localhost:8080/v1/jobs \
  -H "Authorization: Bearer $AGENT_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"type":"noop","payload":{"hello":"world"}}'
```

## Tests

```bash
pip install -e .[dev]
pytest                        # unit tests, no DB needed
DATABASE_URL=postgresql://cloudos:cloudos@localhost:5432/cloudos pytest  # + integration
```

## Discord AI employees (Codex, no Anthropic API)

The Discord adapter routes named employee channels through Cloud AI OS to the
Codex CLI authenticated with a ChatGPT account. Research roles enable Codex web
search, employee prompts are enriched from the configured Second Brain, and
Higgsfield generation is available only through an explicit credit-spend
confirmation command.

See [`docs/DISCORD_CODEX_EMPLOYEES.md`](docs/DISCORD_CODEX_EMPLOYEES.md).

## Fresh lead generator

Finds small/medium businesses you have never contacted, checks their public
website for a published email, grades them HIGH/MEDIUM/LOW/REJECT, dedupes
against every past lead, and hands the good ones to the existing sender. Runs
every 6 hours in GitHub Actions and only works when the ready pile is low.

- START: `gh workflow run lead-engine.yml -f mode=force` (or `python lead_engine.py cycle --force`)
- STOP: `gh workflow disable lead-engine.yml`
- STATUS: `python lead_engine.py status`
- TEST: `python lead_engine.py test`
- RECOVERY: `python lead_engine.py reset-source <name>`; `python lead_engine.py import-history` is safe to re-run

Details: [`docs/lead_engine/README.md`](docs/lead_engine/README.md).

## BrightReach reply layer

Reads the outreach Gmail every 10 minutes, remembers every send and reply per
company, classifies replies against the whole thread, keeps one status per
company, pings Discord once per important event, and puts ready replies in Gmail
Drafts. Never emails a prospect by itself. Senders call
`python outreach_replies.py check EMAIL KIND` before every send.

- STATUS: `python outreach_status.py` (`--queue` for only what needs you)
- STOP: `gh workflow disable outreach-replies.yml`

Details: [`docs/outreach_replies/README.md`](docs/outreach_replies/README.md).
