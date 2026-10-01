$ErrorActionPreference = "Stop"

Write-Host "BrightReach Revenue Autopilot"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is not installed or not on PATH."
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    throw "Claude Code is not installed or not on PATH."
}

$inside = git rev-parse --is-inside-work-tree 2>$null
if ($inside -ne "true") {
    throw "Run this from the cloud-ai-os repository."
}

Write-Host "[1/4] Syncing cloud-ai-os..."
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    throw "git pull failed. Resolve the local git state without discarding work, then run this again."
}

Write-Host "[2/4] Checking Claude authentication..."
claude auth status --text
if ($LASTEXITCODE -ne 0) {
    throw "Claude Code is not authenticated. Run: claude auth login"
}

Write-Host "[3/4] Running local verification before handoff..."
python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Tests are failing locally. Fix the failing test state before starting an unattended cloud session."
}

$prompt = @"
Read docs/REVENUE_AUTOPILOT.md first and treat it as the operating contract.

Execute the BrightReach revenue mission autonomously in this repository. Start by inspecting the latest GitHub Actions runs and current code/status, then choose the single highest-value engineering bottleneck between fresh qualified leads, deliverable sends, reply capture, follow-up, and conversion. Fix it with the smallest safe change, run the strongest verification available, record evidence, then choose the next highest-value non-blocked task and continue.

Do not rebuild systems that already work. Do not weaken dedupe, the 100/day first-touch cap, QA, suppression, opt-out handling, or verification gates. Do not expose secrets or personal postal data. Never claim an email was sent unless provider evidence exists. If a required secret or external authorization is unavailable in the cloud environment, document the exact blocker and immediately work the next non-blocked revenue bottleneck instead of waiting for me.

Keep working through useful milestones until the cloud session naturally reaches a hard external blocker or there is no higher-value safe work left.
"@

Write-Host "[4/4] Launching Claude Code cloud session..."
claude --cloud $prompt
if ($LASTEXITCODE -ne 0) {
    throw "Claude cloud launch failed. Run 'claude update' and retry if your installed version does not support --cloud."
}

Write-Host "Cloud handoff submitted. The cloud session continues independently of this laptop."
