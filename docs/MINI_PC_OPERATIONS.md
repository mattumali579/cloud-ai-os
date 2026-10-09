# Mini PC operations (Cloud AI OS)

One production path on this Windows 11 host:

```
Windows login
  → Docker Desktop (auto-start)
  → Compose: db + agent-api + n8n  (restart: unless-stopped)
  → Scheduled task: CloudAIOS-HostWorker
  → scripts/host_worker_supervisor.py
  → scripts/host_worker_boot.py  (existing host worker)
  → Postgres job queue
```

Do not run a second n8n, a second worker, or `python -m cloudos.worker` in a leftover PowerShell window.

## What starts automatically

| Piece | Mechanism |
|---|---|
| Docker Desktop | Docker `AutoStart` + HKCU Run `Docker Desktop` |
| db, agent-api, n8n | Compose `restart: unless-stopped` |
| Host worker | Task Scheduler `CloudAIOS-HostWorker` (At log on, restart on failure) |

The Compose `worker` service stays on the `container-worker` profile. Job types that shell out to Claude/Codex must stay on the **host** worker.

## Worker startup

Task runs hidden PowerShell → `.venv\Scripts\python.exe scripts\host_worker_supervisor.py` (venv `pythonw.exe` was observed to launch a second base `pythonw` on this machine).

The supervisor:

1. Takes `logs/host_worker.pid` (refuses a second live instance).
2. Waits until `http://127.0.0.1:8080/healthz` reports `db: true` (boot-order retry).
3. Runs the existing `host_worker_boot.py` (does **not** start a second API once Compose API is up).
4. If the worker process returns or crashes, logs and restarts with backoff.

Register (idempotent):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Admin\cloud-ai-os\scripts\register-host-worker-task.ps1
```

## Logs

`C:\Users\Admin\cloud-ai-os\logs\host_worker.log` (rotated, 5 MB × 5). Git-ignored.

Post-boot evidence (if the pending flag is set): `C:\Users\Admin\cloud-ai-os\logs\post-boot-verify.txt`.

## Health / status

```powershell
powershell -NoProfile -File C:\Users\Admin\cloud-ai-os\scripts\system-status.ps1
```

## Manual worker start / stop

```powershell
Start-ScheduledTask -TaskName CloudAIOS-HostWorker
Stop-ScheduledTask -TaskName CloudAIOS-HostWorker
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'host_worker_supervisor' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Stopping the process should cause the 1-minute repeating scheduled task to start it again. The supervisor lock prevents a second live worker.

## Restart the Compose stack

```powershell
cd C:\Users\Admin\cloud-ai-os
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d
```

## After reboot

1. Log into Windows (this account).
2. Docker Desktop starts itself; wait until `system-status.ps1` shows SYSTEM: READY.
3. No PowerShell startup commands are required.
4. Task `CloudAIOS-PostBootVerify` runs ~2 minutes after logon. If `logs/post-boot-pending.flag` exists, it submits one noop job and writes `logs/post-boot-verify.txt`.

## Recovery behavior

- Container crash: Docker restarts db / agent-api / n8n (`unless-stopped`).
- Worker crash: supervisor restarts in-process; Task Scheduler also restarts the pythonw process.
- Stale `running` jobs locked by `host-worker` are requeued by existing `queue.recover_stale` on worker start.
- Worker loop already retries DB blips per iteration; supervisor waits for API/DB at boot.

## Development

Repo tests and Compose commands are unchanged. Do not enable the `container-worker` profile on this Mini PC while the host worker task is running.
