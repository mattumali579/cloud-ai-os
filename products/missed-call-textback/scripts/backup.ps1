# Dump schema mctb from the Postgres container into a dated file and keep 14 days.
# powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Container cloudos-db-1 -PgUser cloudos -PgDatabase cloudos -OutDir C:\mctb-backups

param(
  [string]$Container = "cloudos-db-1",
  [string]$PgUser = "cloudos",
  [string]$PgDatabase = "cloudos",
  [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutDir) {
  $OutDir = Join-Path (Split-Path -Parent $PSScriptRoot) "backups"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$file = Join-Path $OutDir "mctb-$stamp.sql"
$dump = & docker exec $Container pg_dump -U $PgUser -d $PgDatabase --schema=mctb --no-owner --no-acl
if ($LASTEXITCODE -ne 0) {
  Write-Error "pg_dump inside $Container failed with exit code $LASTEXITCODE"
}
[System.IO.File]::WriteAllLines($file, $dump)

Get-ChildItem -Path $OutDir -Filter "mctb-*.sql" -File |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
  Remove-Item -Force

Write-Output "Wrote $file"
