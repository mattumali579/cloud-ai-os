# Starts the ACTIVE BrightReach loop: Hermes on the ChatGPT subscription is the PLANNER,
# Claude Code is the WORKER it calls through scripts/claude_task.py. Claude never picks its own task.
#   start-hermes-revenue.ps1           check everything, then open the live Hermes session
#   start-hermes-revenue.ps1 -Check    check everything and exit (starts nothing)
param([switch]$Check)

$ErrorActionPreference = "Stop"

Write-Host "BrightReach Hermes Active Revenue Session"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$mission = Join-Path $repoRoot "docs\HERMES_REVENUE_MISSION.md"

foreach ($cmd in @("git","hermes","claude","python","gh")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "$cmd is not installed or is not on PATH."
    }
}
if (-not (Test-Path $mission)) { throw "Mission file is missing: $mission" }

# Subscription logins only. A metered key in the environment would be used silently and billed.
foreach ($v in @("ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","OPENAI_API_KEY","GEMINI_API_KEY","GOOGLE_API_KEY")) {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
}

Set-Location $repoRoot

# Inside Hermes, plain `python` is Hermes's own. Hand it the project's Python (the one with the packages).
$env:BRIGHTREACH_PYTHON = (Get-Command python).Source -replace '\\','/'
& $env:BRIGHTREACH_PYTHON -c "import pytest, psycopg" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Python at $env:BRIGHTREACH_PYTHON is missing the project's packages. Run: python -m pip install -e .[dev]" }

Write-Host "[1/5] Syncing repository..."
$pulled = $false
foreach ($try in 1..3) {
    git pull --ff-only
    if ($LASTEXITCODE -eq 0) { $pulled = $true; break }
    Start-Sleep -Seconds 5
}
# A network blip or diverged history must not stop the work session; Hermes inspects git state itself.
if (-not $pulled) { Write-Warning "Could not sync with GitHub after 3 tries. Continuing with the local copy." }

Write-Host "[2/5] Checking Hermes..."
hermes --version
if ($LASTEXITCODE -ne 0) { throw "Hermes is not healthy. Run: hermes setup" }

Write-Host "[3/5] Checking Hermes uses the ChatGPT subscription..."
$auth = (hermes auth list | Out-String)
if ($LASTEXITCODE -ne 0 -or $auth -notmatch "openai-codex") {
    throw "Hermes is not signed in with ChatGPT. Run: hermes auth add openai-codex"
}
# The planner must be the OpenAI model. If Hermes were pointed at anything else, stop rather than let it plan.
$planner = (hermes status | Select-String -Pattern "^\s*(Model|Provider):" | ForEach-Object { $_.Line.Trim() }) -join "; "
Write-Host "      Planner -> $planner"
if ($planner -notmatch "Provider:\s+ChatGPT or Codex Subscription" -or $planner -notmatch "Model:\s+gpt-") {
    throw "Hermes planner is not the ChatGPT/OpenAI model ($planner). Run: hermes model"
}

Write-Host "[4/5] Checking Claude Code authentication..."
claude auth status --text
if ($LASTEXITCODE -ne 0) { throw "Claude Code is not authenticated. Run: claude auth login" }

if ($Check) {
    Write-Host "All checks passed. Nothing was started."
    exit 0
}

Write-Host "[5/5] Starting ACTIVE Hermes work session..."
Write-Host "This does not create a recurring schedule. Hermes works now, in this window, until you close it."
Write-Host "Planner = ChatGPT model (decides every task). Worker = Claude Code (executes one task at a time)."
hermes --in $repoRoot chat --provider openai-codex --query-file $mission
if ($LASTEXITCODE -ne 0) { throw "Hermes session exited with an error." }
