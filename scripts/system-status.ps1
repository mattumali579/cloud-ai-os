# Cloud AI OS Mini PC health. Queries live Docker, HTTP, DB, and the host worker.
# Usage (from anywhere):  powershell -NoProfile -File C:\Users\Admin\cloud-ai-os\scripts\system-status.ps1
$ErrorActionPreference = "Continue"
$Repo = Split-Path $PSScriptRoot -Parent
$env:Path = "C:\Program Files\Docker\Docker\resources\bin;$env:Path"

function Get-HttpJson([string]$Url) {
    try {
        return Invoke-RestMethod -Uri $Url -TimeoutSec 3
    } catch {
        return $null
    }
}

function Get-ComposeRestart([string]$Name) {
    if (-not $script:dockerOk) { return "unavailable" }
    try {
        return docker inspect -f "{{.HostConfig.RestartPolicy.Name}}" $Name 2>$null
    } catch {
        return "unknown"
    }
}

function Get-ComposeHealth([string]$Name) {
    if (-not $script:dockerOk) { return "UNAVAILABLE" }
    try {
        $running = docker inspect -f "{{.State.Running}}" $Name 2>$null
        if ($running -ne "true") { return "DOWN" }
        $health = docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" $Name 2>$null
        if ($health -eq "healthy") { return "HEALTHY" }
        if ($health -eq "none" -or [string]::IsNullOrWhiteSpace($health)) { return "UP" }
        return $health.ToUpperInvariant()
    } catch {
        return "DOWN"
    }
}

$script:dockerOk = $false
try {
    $null = docker info --format "{{.ServerVersion}}" 2>$null
    if ($LASTEXITCODE -eq 0) { $script:dockerOk = $true }
} catch {}

$db = Get-ComposeHealth "cloudos-db-1"
$apiC = Get-ComposeHealth "cloudos-agent-api-1"
$n8nC = Get-ComposeHealth "cloudos-n8n-1"
$apiHttp = Get-HttpJson "http://127.0.0.1:8080/healthz"
$n8nHttp = Get-HttpJson "http://127.0.0.1:5679/healthz"

$worker = $null
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop | ForEach-Object {
        if ($_.CommandLine -match 'host_worker_supervisor|host_worker_boot|cloudos\.worker') {
            $script:worker = $true
        }
    }
    if ($null -eq $worker) { $worker = $false }
} catch {
    # A standard Windows account may not read other processes' command lines.
    # Unknown is safer and more useful than a false "STOPPED" alarm.
    $worker = $null
}

$queued = "-"; $running = "-"; $failed = "-"
try {
    $raw = docker exec cloudos-db-1 psql -U cloudos -d cloudos -t -A -c "SELECT COALESCE(status,'?') || '=' || count(*) FROM jobs GROUP BY status;" 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
        $queued = 0; $running = 0; $failed = 0
        foreach ($line in ($raw -split "`n")) {
            $line = $line.Trim()
            if ($line -match '^(queued|running|failed)=(\d+)$') {
                switch ($Matches[1]) {
                    "queued" { $queued = [int]$Matches[2] }
                    "running" { $running = [int]$Matches[2] }
                    "failed" { $failed = [int]$Matches[2] }
                }
            }
        }
    }
} catch {}

$workerTs = "-"
$logFile = Join-Path $Repo "logs\host_worker.log"
if (Test-Path $logFile) {
    $workerTs = (Get-Item $logFile).LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
}

$apiLine = if ($apiHttp -and $apiHttp.status -eq "ok" -and $apiHttp.db -eq $true) { "OK  /healthz db=true" } elseif ($apiHttp) { "DEGRADED  /healthz db=$($apiHttp.db)" } else { "DOWN" }
$n8nLine = if ($n8nHttp -and $n8nHttp.status -eq "ok") { "OK  /healthz ok" } else { "DOWN" }
$dockerLine = if ($dockerOk) { "OK" } else { "UNAVAILABLE (inspection denied)" }
$workerLine = if ($worker -eq $true) { "OK" } elseif ($worker -eq $false) { "STOPPED" } else { "UNKNOWN (process inspection denied)" }

$httpReady = $apiHttp -and $apiHttp.status -eq "ok" -and $apiHttp.db -eq $true -and $n8nHttp -and $n8nHttp.status -eq "ok"
$ready = $dockerOk -and $db -match 'HEALTHY|UP' -and $httpReady -and $worker -eq $true
if ($ready) {
    $system = "READY"
} elseif ($httpReady -and -not $dockerOk) {
    $system = "PARTIAL - HTTP services healthy; Docker inspection unavailable"
} elseif ($httpReady -and $null -eq $worker) {
    $system = "PARTIAL - HTTP services healthy; worker process inspection unavailable"
} else {
    $system = "NOT READY"
}

@"
CLOUD AI OS STATUS

Docker       $dockerLine
Database     $db  restart=$(Get-ComposeRestart cloudos-db-1)
Agent API    $apiC  $apiLine  restart=$(Get-ComposeRestart cloudos-agent-api-1)
n8n          $n8nC  $n8nLine  restart=$(Get-ComposeRestart cloudos-n8n-1)
Worker       $workerLine  log=$workerTs

Queue
Queued       $queued
Running      $running
Failed       $failed

SYSTEM: $system
"@
