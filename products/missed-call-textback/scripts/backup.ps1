# Dump schema mctb from the Postgres container into a dated file and keep 14 days.
# pg_dump writes the file inside the container. docker cp copies those bytes out.
# PowerShell 5.1 would otherwise decode stdout with the console code page and
# rewrite the dump with Windows line endings.
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
$name = "mctb-$stamp.sql"
$file = Join-Path $OutDir $name
$remote = "/tmp/$name"

& docker exec $Container pg_dump -U $PgUser -d $PgDatabase --schema=mctb --no-owner --no-acl -f $remote
if ($LASTEXITCODE -ne 0) {
  & docker exec $Container rm -f $remote
  Write-Error "pg_dump inside $Container failed with exit code $LASTEXITCODE"
}

& docker cp "${Container}:${remote}" $file
$copyCode = $LASTEXITCODE
& docker exec $Container rm -f $remote
if ($copyCode -ne 0) {
  Write-Error "docker cp from ${Container}:${remote} failed with exit code $copyCode"
}

Get-ChildItem -Path $OutDir -Filter "mctb-*.sql" -File |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
  Remove-Item -Force

Write-Output "Wrote $file"
