$ErrorActionPreference = "Stop"

Write-Host "BrightReach Hermes Active Revenue Session"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$mission = Join-Path $repoRoot "docs\HERMES_REVENUE_MISSION.md"

foreach ($cmd in @("git","hermes","claude")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "$cmd is not installed or is not on PATH."
    }
}

Set-Location $repoRoot

Write-Host "[1/5] Syncing repository..."
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull failed. Resolve local changes without discarding work." }

Write-Host "[2/5] Checking Hermes..."
hermes --version
if ($LASTEXITCODE -ne 0) { throw "Hermes is not healthy. Run: hermes setup" }

Write-Host "[3/5] Showing Hermes credentials..."
hermes auth list
if ($LASTEXITCODE -ne 0) { throw "Hermes has no usable provider. Run: hermes model and choose ChatGPT or Codex Subscription." }

Write-Host "[4/5] Checking Claude Code authentication..."
claude auth status --text
if ($LASTEXITCODE -ne 0) { throw "Claude Code is not authenticated. Run: claude auth login" }

Write-Host "[5/5] Starting ACTIVE Hermes work session..."
Write-Host "This does not create a recurring schedule. Hermes works now, in this process."
hermes --in $repoRoot chat --query-file $mission
if ($LASTEXITCODE -ne 0) { throw "Hermes session exited with an error." }
