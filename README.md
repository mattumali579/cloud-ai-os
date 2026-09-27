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
