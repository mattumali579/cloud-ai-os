# Oracle A1 Runbook — Cloud AI OS

Scope: operating the single Always Free A1 VM, and above all **recovering from
reclamation** (ARCHITECTURE.md §0, §14). Guiding rule (§0):

> Compute is disposable, data is durable, code is reproducible, AI is optional,
> paid services are disabled.

We do **not** fight Oracle's idle reclamation with synthetic load — that is
explicitly forbidden (§0). We make losing the VM boring instead.

## What survives the VM (source-of-truth map, §1)

| Thing | Canonical home | Survives VM loss? |
|---|---|---|
| Code | GitHub (`cloud-ai-os` repo) | Yes |
| Knowledge | Second Brain Git/Markdown repo | Yes |
| Structured state (jobs, agent_runs, events, quota, sb_index) | Supabase | Yes |
| n8n workflows + credentials | n8n volume → `backup.sh` archive, **offsite copy** | Yes, if backups ran |
| Secrets (`.env`) | `backup.sh` archive offsite + owner password manager | Yes, if backups ran |
| Running containers, local image cache | the VM only | No — and that is fine |

The only things that need the backup discipline are the **n8n volume** and
**`.env`**. Everything else regenerates from GitHub/Supabase.

## Routine operations

| Task | Command (on the VM, repo root) |
|---|---|
| Deploy latest code | `./infra/oracle/deploy.sh` |
| Status | `docker compose -f infra/docker/docker-compose.oracle.yml ps` |
| Logs | `docker compose -f infra/docker/docker-compose.oracle.yml logs --tail 100 agent-api worker n8n` |
| Health | `curl -s localhost:8080/healthz` |
| Backup now | `./infra/oracle/backup.sh` (then copy the archive offsite) |
| Restart one service | `docker compose -f infra/docker/docker-compose.oracle.yml restart worker` |
| Reach n8n UI | from laptop: `ssh -L 5678:localhost:5678 ubuntu@<vm-ip>` → http://localhost:5678 |

Schedule `backup.sh` daily via cron (example in the script header) and copy
archives to OCI Object Storage (Always Free ≤ 20 GB) or your laptop.

## Failure mode: a service died / restarted

`restart: always` (docker-compose.oracle.yml) restarts crashed containers and
brings the whole stack back after a VM reboot. If a service crash-loops:

1. `docker compose -f infra/docker/docker-compose.oracle.yml ps` — find the looper.
2. `docker compose -f infra/docker/docker-compose.oracle.yml logs --tail 200 <svc>`
3. Typical causes: bad `.env` (Supabase URL/credentials), Supabase paused
   (free projects pause after ~1 week idle — unpause in the Supabase dashboard),
   OOM (check `docker inspect <ctr> --format '{{.State.OOMKilled}}'`; limits are
   api 512m / worker 1g / n8n 768m).
4. Worker/API restarts are safe by design: jobs are claimed with
   `FOR UPDATE SKIP LOCKED` and retried with backoff (§8); `migrate()` at
   startup is idempotent (§5). No manual state repair is expected.

## Failure mode: the VM is GONE (reclaimed / terminated / region issue)

This is the headline scenario (§0, §14). Nothing irreplaceable was on the VM.

```
VM disappears
   │  knowledge  → still in Second Brain Git repo
   │  state      → still in Supabase
   │  code       → still in GitHub
   │  n8n + .env → latest offsite backup archive
   ▼
provision new VM  (infra/oracle/setup.sh — capacity-retry loop is normal)
   ▼
bootstrap        (infra/oracle/bootstrap.sh with BACKUP_ARCHIVE=<offsite archive>)
   ▼
operational      (healthz green; worker resumes queued jobs from Supabase)
```

Step by step:

1. **Provision** a replacement A1 from your laptop:
   `COMPARTMENT_ID=... SUBNET_ID=... AVAILABILITY_DOMAIN=... IMAGE_ID=... SSH_PUBKEY_FILE=... ./infra/oracle/setup.sh`
   "Out of host capacity" retries are expected — let the loop run, try other ADs.
2. **Fetch the newest backup archive** from offsite storage onto the new VM
   (e.g. `oci os object get --bucket-name cloudos-backups --name <archive> --file ~/backup.tar.gz`,
   or `scp` it from your laptop).
3. **Bootstrap**:
   `REPO_URL=https://github.com/<owner>/cloud-ai-os.git BACKUP_ARCHIVE=~/backup.tar.gz ./bootstrap.sh`
   (no archive? bootstrap still works — you re-enter `.env` by hand and
   re-import n8n workflows from `n8n/*.json`, which are in git; only n8n
   *credentials* need re-entering.)
4. **Repoint ingress**: update the Cloudflare Worker's origin IP/hostname
   (§13 — `wrangler` var or DNS record) to the new public IP.
5. **Verify** with the checklist below.
6. Jobs that were `running` when the VM died will exceed their lock and be
   retried or land in `failed` per §8 — inspect `GET /v1/jobs?status=failed`.

Expected time to recover: minutes of work + however long A1 capacity takes.
While waiting on capacity, the GCP degraded mode can carry the core loop —
see `infra/docker/docker-compose.gcp-minimal.yml` and `infra/gcp/README.md`.

## Failure mode: no A1 capacity for days → GCP degraded mode

Disaster recovery ONLY (not active-active, no cross-cloud replication, §14):
bring up `docker-compose.gcp-minimal.yml` on a free e2-micro (api + worker
against the same Supabase; **no n8n**), keep retrying `setup.sh`, and tear the
GCP instance down as soon as Oracle is back. Never run both workers against
Supabase at once longer than the cutover takes — INSTANCE_NAME differs, locking
keeps it safe (§8), but degraded mode is a stopgap, not a second production.

## Phase-7 recovery test checklist (§15 P7)

Run this as a DRILL before declaring P7 passed — terminate the VM on purpose
(or use a scratch VM) and time the recovery:

- [ ] A fresh backup archive exists offsite and is < 24 h old (`backup.sh` + copy).
- [ ] Old VM terminated (or pretend-reclaimed: stop using it entirely).
- [ ] `setup.sh` provisions a new A1 (capacity-retry loop allowed to run).
- [ ] `bootstrap.sh` with `BACKUP_ARCHIVE=` completes without manual fixes
      beyond the documented `.env` prompt (skipped here — restore supplied it).
- [ ] `curl localhost:8080/healthz` → `{"status":"ok","db":true}` (Supabase reachable).
- [ ] `GET /v1/jobs?status=queued` shows the pre-drill queue intact (state survived).
- [ ] A `noop` job posted via `POST /v1/jobs` is picked up and succeeds (worker alive).
- [ ] n8n UI (via SSH tunnel) shows the workflows + credentials from the backup.
- [ ] Second Brain reindex job (`sb.reindex`) succeeds (knowledge repo cloned/reachable).
- [ ] Cloudflare Worker `/ping` OK and `/api/*` proxies to the NEW origin.
- [ ] No paid resources were created anywhere during recovery (§0) — verify the
      OCI cost analysis page still reads $0.00.
- [ ] Total recovery time recorded in docs/PHASES.md.
