# Live n8n workflows (audited 2026-11-23, task GB-2)

n8n runs locally in Docker (`infra/docker/docker-compose.yml`, service `n8n`),
pinned to `docker.n8n.io/n8nio/n8n:2.48.8`, published on `127.0.0.1`/`[::1]:5679`
only. The container gets **no** repo `.env`; `$env` access in nodes is blocked.

Only two workflows are active. Both JSON files in this folder are exports of the
live definitions (they contain no credentials).

| Workflow | ID | Trigger | Purpose | Output |
|---|---|---|---|---|
| Intelligence Library Collector | `OANO8Vu5znOMvi1k` | every 2 h | Runs `scripts/library_collector.mjs` (free OpenAlex, Crossref, arXiv, GitHub, Wikipedia, YouTube/RSS feeds) for the next Research Queue topics | `work/research-alerts/incoming/n8n-append-<UTC>.json` + `.csv` (39 Master Index columns), merged into the Master Index sheet by the Grok Bot routine |
| Jarvis Error Reporter → Muse | `z1LSLiNvQ2RSOfnN` | n8n error trigger (error workflow of the collector) | De-duplicates failures (30 min window) and writes an alert file | `work/research-alerts/muse-alerts/error-<fingerprint>-<execution>.json` and `latest_error.json`; failures are also visible through the Muse monitor gateway |

## Collector relevance gate

Before the gate, roughly 95% of collector rows were off-topic (math, medical and
code results). Cause: relevance was scored on the item **plus the queue topic
text**, and some sources had a fixed relevance floor of 6. So almost any search
hit for a topic like "Procore" or "ULI" passed. The collector now:

- builds focused literature queries (filler words removed; queries with no
  construction or real-estate term get "construction" added), restricted to
  2015+ work that has an abstract;
- re-scores each item on its own title and summary only;
- requires a construction, real-estate, precon, AEC-tech or Gulf-region signal
  (repos also need 10 or more stars and a domain term in the description);
- rejects off-topic terms (medical, math, biology and similar);
- reports its decisions in `stats.relevance_gate` in each batch file.

The output file format is unchanged.

## Disabled (kept for reference, not deleted)

| Workflow | Why disabled |
|---|---|
| BrightReach Lead Discovery & Contact Engine (`leadDiscoveryEngine`) | Every 10 min it re-crawled the existing 18k-company backlog. In its last 400 runs it added 0 outreach-ready leads and prepared 0 drafts. The last local outreach-ready lead dates from 2026-11-04 and outreach now runs from GitHub Actions/Supabase. 52 errors/3 crashes since Nov 10 (agent-api restarts, out-of-memory, earlier 401s). To revive: re-add `AGENT_API_URL`/`AGENT_API_TOKEN` env and set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`; clear the stale `lead_engine_checkpoint.is_running` lock first. |
| ONLINE_MONEY_RESEARCH Continuous Knowledge Engine (`onlineMoneyResearch`) | Hourly trigger of the research engine, whose corpus stopped growing (997 sources, 9 extracted, unchanged across 300+ runs). It duplicated the engine's own internal loop. Its "Composio Drive Sync" node only listed 10 Drive files. |
| 04 - Sales Optimizer (`salesOptimizerWorkflow`) | Only ran as a side effect of the two workflows above (~150 times/day). It had 1 experiment and 0 approvals, and its notify target (`research-engine /notify`) is a no-op. |

## Deleted (after a verified backup)

`My workflow` (Discord test relay), `Composio Hub` (unused sub-workflow), and the 9
inactive MCTB product workflows (`mctbActions`, `mctbDashboard`, `mctbDial`,
`mctbFollowups`, `mctbHealth`, `mctbRevenue`, `mctbReview`, `mctbSms`,
`mctbVoice`). The MCTB source of truth remains `products/missed-call-textback/workflows/`.

## Backup, restore, upgrade, rollback (run on the PC, PowerShell)

Backups live outside the repo in `C:\cloudos-backups\n8n-<date>\` (volume tarball incl.
the encryption-key file, per-workflow exports, encrypted credential export).

```powershell
# backup (about 2 min of n8n downtime)
docker stop cloudos-n8n-1
docker run --rm --entrypoint sh -u root -v cloudos_n8n-data:/src:ro -v C:\cloudos-backups\n8n-NEW:/dst docker.n8n.io/n8nio/n8n:2.48.8 -c "cd /src && tar -czf /dst/n8n-data-volume.tgz --exclude=./database.sqlite.bak_20261023 ."
docker start cloudos-n8n-1
# restore test: extract into a scratch volume, run a scratch container on 127.0.0.1:5689,
# `n8n list:workflow`, decrypt-count credentials, then remove the container and volume.
# rollback of the 2.48.8 upgrade: restore n8n-data-volume-pre-upgrade-2.41.6.tgz into
# cloudos_n8n-data and set image to docker.n8n.io/n8nio/n8n:2.41.6-local-backup.
```
