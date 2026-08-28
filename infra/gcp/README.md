# GCP e2-micro — Disaster-Recovery Degraded Mode ONLY

**This is not a second production environment.** ARCHITECTURE.md §1/§14 defines
GCP as fallback only: *not* active-active, *no* cross-cloud replication, torn
down as soon as Oracle is back.

## What it is

A free-tier `e2-micro` (Always Free: 1 shared vCPU, 1 GB RAM, 30 GB standard
disk, in `us-west1`/`us-central1`/`us-east1`) that can carry the **core loop**
while the Oracle A1 VM is gone and A1 capacity cannot be obtained (see
`infra/oracle/RUNBOOK.md`, "no A1 capacity for days").

It runs `infra/docker/docker-compose.gcp-minimal.yml`:

| Service | Runs here? | Memory limit |
|---|---|---|
| agent-api | yes | 256m |
| worker | yes (WORKER_CONCURRENCY=1) | 512m |
| n8n | **NO** | — |
| db | **NO** (Supabase, same as Oracle) | — |

Total limits ≈ 768 MB — sized for the ~1 GB the e2-micro actually has.

## What degraded means (explicitly NOT equivalent to Oracle A1)

- **No n8n**: no workflow automation, no scheduled `daily_ops` cron, no
  owner-notify fan-out — notifications accumulate in the `notifications`
  table only (`NOTIFY_WEBHOOK_URL` must be empty here). The daily
  `retention.prune` enqueue still happens (the worker schedules it, §8).
- **~1/12 the memory, 1 shared vCPU vs 2 OCPU**: throughput is a trickle;
  WORKER_CONCURRENCY=1; heavy jobs may need to wait for Oracle.
- **Same Supabase**: state is continuous — jobs queued before the outage are
  picked up here. Never run the Oracle and GCP workers side by side beyond the
  cutover window (locking keeps it *safe*, §8 — but this mode is a stopgap).
- **Egress caution**: free tier includes 1 GB/month North America egress —
  another reason this is a temporary fallback, not a home.

## Bring-up (manual, owner credentials required — §15 gates)

```bash
# 1. Create the free instance (console or gcloud) — MUST stay inside free tier:
gcloud compute instances create cloudos-dr \
  --machine-type=e2-micro --zone=us-central1-a \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard
# Firewall: allow tcp:22 and tcp:8080 (restrict 8080 to Cloudflare/your IP).

# 2. On the VM: install docker, clone, configure
curl -fsSL https://get.docker.com | sudo sh
git clone https://github.com/<owner>/cloud-ai-os.git && cd cloud-ai-os
cp .env.example .env   # fill: ENVIRONMENT=gcp, INSTANCE_NAME=cloudos-gcp,
                       # DATABASE_URL=<same Supabase>, AGENT_API_TOKEN=<same>,
                       # NOTIFY_WEBHOOK_URL=   (empty — no n8n here)

# 3. Degraded stack up + verify
sudo docker compose -f infra/docker/docker-compose.gcp-minimal.yml up -d --build
curl -s localhost:8080/healthz

# 4. Repoint the Cloudflare Worker origin at this VM (§13).
```

Note: e2-micro is x86_64 (amd64) while Oracle A1 is arm64 — the same
Dockerfile builds both natively (multi-arch, see the Dockerfile header), so no
image changes are needed for cutover.

## Exit criteria (get OFF this box)

1. Oracle A1 re-provisioned + bootstrapped (RUNBOOK.md recovery flow).
2. Cloudflare origin repointed back to Oracle; n8n restored from backup there.
3. `docker compose -f infra/docker/docker-compose.gcp-minimal.yml down` here,
   then **delete the GCP instance** — an idle forgotten VM is a config-drift
   and free-tier-accounting risk. $0/month stays the hard requirement (§0).
