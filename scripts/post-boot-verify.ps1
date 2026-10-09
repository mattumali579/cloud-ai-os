# Writes post-login evidence for the Mini PC autostart chain.
# If logs\post-boot-pending.flag exists, also submits one harmless noop job.
$ErrorActionPreference = "Continue"
$Repo = Split-Path $PSScriptRoot -Parent
$env:Path = "C:\Program Files\Docker\Docker\resources\bin;$env:Path"
$OutDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
$Report = Join-Path $OutDir "post-boot-verify.txt"
$Flag = Join-Path $OutDir "post-boot-pending.flag"

function Get-DotEnvValue([string]$Key) {
    $line = Get-Content (Join-Path $Repo ".env") -ErrorAction SilentlyContinue | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return $line.Substring($Key.Length + 1)
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("POST-BOOT VERIFY $Stamp")

$status = & (Join-Path $Repo "scripts\system-status.ps1") | Out-String
$lines.Add($status.TrimEnd())

$jobLine = "job=skipped (no pending flag)"
if (Test-Path $Flag) {
    try {
        $token = Get-DotEnvValue "AGENT_API_TOKEN"
        $headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }
        $created = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8080/v1/jobs" -Headers $headers -Body '{"type":"noop","payload":{"hello":"post-boot-verify"}}'
        $final = $null
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            $got = Invoke-RestMethod -Uri ("http://127.0.0.1:8080/v1/jobs/" + $created.id) -Headers $headers
            if ($got.status -in @("succeeded", "failed", "blocked", "cancelled")) { $final = $got; break }
        }
        if ($final) {
            $jobLine = "job=$($created.id) queued->$($final.status)"
        } else {
            $jobLine = "job=$($created.id) did-not-finish"
        }
        Remove-Item $Flag -Force
    } catch {
        $jobLine = "job=ERROR $($_.Exception.Message)"
    }
}
$lines.Add($jobLine)
$lines.Add("done")
$lines -join "`r`n" | Set-Content -Path $Report -Encoding utf8
Write-Output $jobLine
