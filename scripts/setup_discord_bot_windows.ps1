$ErrorActionPreference = "Stop"

Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required. Install Python, then run this script again."
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw "Codex CLI is required. Install it and sign in with ChatGPT before continuing."
}
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[discord]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Add DISCORD_BOT_TOKEN and replace AGENT_API_TOKEN before starting."
}

Write-Host "Checking Codex subscription login..."
codex login status

if (-not (Get-Command higgsfield -ErrorAction SilentlyContinue)) {
    Write-Host "Higgsfield CLI is not installed. Install with: npm install -g @higgsfield/cli"
} else {
    higgsfield version
}

Write-Host "Setup complete. Edit .env, then run:"
Write-Host ".\.venv\Scripts\python.exe -m cloudos.discord_bot.launcher"
