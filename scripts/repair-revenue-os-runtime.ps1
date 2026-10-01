# Repair and verify the local Revenue OS runtime after pulling the latest repository state.
# Safe by default: does not enable live sending, change credentials, reset the DB, or delete n8n state.
param(
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$compose = Join-Path $repoRoot "infra\docker\docker-compose.yml"
Set-Location $repoRoot

function Step([string]$name, [scriptblock]$body) {
    Write-Host ""
    Write-Host "=== $name ==="
    & $body
    if ($LASTEXITCODE -ne 0) { throw "$name failed with exit code $LASTEXITCODE" }
}

if (-not $SkipPull) {
    Step "Sync latest repository" {
        $dirty = @(git status --porcelain)
        if ($dirty.Count -gt 0) {
            throw "Local repo has uncommitted changes. Preserve/commit them first; refusing to overwrite them."
        }
        git pull --ff-only
    }
}

if (-not (Test-Path $compose)) { throw "Compose file missing: $compose" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not on PATH." }

$authWarnings = @()
foreach ($pair in @(
    @{Name="Codex"; Path=(Join-Path $env:USERPROFILE ".codex")},
    @{Name="Claude"; Path=(Join-Path $env:USERPROFILE ".claude")}
)) {
    if (-not (Test-Path $pair.Path)) {
        $authWarnings += "$($pair.Name) auth directory missing: $($pair.Path)"
    }
}
if ($authWarnings.Count) {
    Write-Warning ($authWarnings -join "; ")
    Write-Warning "The deterministic Revenue OS/API will still start, but research/build will report auth as a blocker."
}

Step "Validate compose" {
    docker compose -f $compose config --quiet
}

Step "Rebuild existing db + agent-api + n8n" {
    docker compose -f $compose up -d --build db agent-api n8n
}

Write-Host ""
Write-Host "=== Wait for Agent API health ==="
$healthy = $false
foreach ($i in 1..30) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:8080/healthz" -TimeoutSec 3
        if ($h.status -eq "ok" -and $h.db -eq $true) {
            $healthy = $true
            Write-Host ("API healthy; DB=" + $h.db)
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    docker compose -f $compose ps
    docker compose -f $compose logs --tail 120 agent-api
    throw "Agent API/DB did not become healthy."
}

Step "Apply full portable migration sequence" {
    docker compose -f $compose exec -T agent-api python -c "from cloudos import db; print('applied=', db.migrate())"
}

Step "Verify installed-path bug is gone" {
    docker compose -f $compose exec -T agent-api python -c "from cloudos.outreach import sender; from cloudos.revenue_os import ROOT; print('config=', sender.CONFIG); print('root=', ROOT); assert sender.CONFIG.is_file(), sender.CONFIG; assert str(ROOT) == '/workspace', ROOT"
}

Write-Host ""
Write-Host "=== Subscription worker checks ==="
$codexOk = $false
$claudeOk = $false
docker compose -f $compose exec -T agent-api codex login status
if ($LASTEXITCODE -eq 0) { $codexOk = $true } else { Write-Warning "Codex subscription auth is not available inside agent-api." }
docker compose -f $compose exec -T agent-api claude auth status --text
if ($LASTEXITCODE -eq 0) { $claudeOk = $true } else { Write-Warning "Claude subscription auth is not available inside agent-api." }

Step "Revenue OS state + decision smoke test" {
    docker compose -f $compose exec -T agent-api python -c "import json; from cloudos.revenue_os import run; s=run('state', execution_key='local-repair-state'); d=run('decide', execution_key='local-repair-decide'); print(json.dumps({'state_evidence':s['evidence']['evidence_id'],'bottleneck_count':s['dashboard']['bottleneck_count'],'bottlenecks':s['dashboard']['bottlenecks'],'degraded_items':s['dashboard'].get('degraded_items',[]),'decision':d['proof']}, default=str, indent=2))"
}

Write-Host ""
Write-Host "=== Runtime status ==="
docker compose -f $compose ps

$n8nReachable = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:5679" -UseBasicParsing -TimeoutSec 5
    $n8nReachable = $true
    Write-Host ("n8n reachable: HTTP " + [int]$resp.StatusCode)
} catch {
    if ($_.Exception.Response) {
        $n8nReachable = $true
        Write-Host ("n8n reachable (authenticated response): HTTP " + [int]$_.Exception.Response.StatusCode)
    } else {
        Write-Warning "n8n is not reachable on localhost:5679"
    }
}

Write-Host ""
Write-Host "=== FINAL ==="
Write-Host ("Codex auth inside API: " + ($(if ($codexOk) {"PASS"} else {"BLOCKED"})))
Write-Host ("Claude auth inside API: " + ($(if ($claudeOk) {"PASS"} else {"BLOCKED"})))
Write-Host ("n8n reachable: " + ($(if ($n8nReachable) {"PASS"} else {"BLOCKED"})))
Write-Host "Live sending remains fail-closed unless REVENUE_OS_SEND_ENABLED=true is already set and sender readiness passes."
Write-Host "Missing AgentMail is degraded notification delivery only; notices remain queued and do not block revenue work."
Write-Host "The already-active RevenueOSMain1 schedule can now run the repaired API path."
