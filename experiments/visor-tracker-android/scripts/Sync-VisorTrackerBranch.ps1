# Fast-forward a local Visor Tracker checkout to origin/feat/visor-overlay-phase1.
# Refuses when tracked files are dirty. Does not change git remotes, delete
# untracked files, or run reset --hard. The dirty Cloud AI OS checkout at
# C:\Users\Admin\cloud-ai-os is not the default and is refused while it is dirty.
[CmdletBinding()]
param(
    [string]$Checkout = "C:\Users\Admin\cloud-ai-os-visor-tracker",
    [string]$Branch = "feat/visor-overlay-phase1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Checkout -PathType Container)) {
    Write-Host "Checkout not found: $Checkout"
    Write-Host "This script does not clone and does not create a remote."
    Write-Host "Open https://github.com/mattumali579/cloud-ai-os/pull/37 in Cursor and choose the existing feat/visor-overlay-phase1 branch."
    exit 2
}

$git = Join-Path $Checkout ".git"
if (-not (Test-Path -LiteralPath $git)) {
    Write-Host "Not a git checkout: $Checkout"
    exit 2
}

Push-Location -LiteralPath $Checkout
try {
    $head = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($head -ne $Branch) {
        Write-Host "Refusing to switch branches. HEAD is $head; this sync only fast-forwards $Branch."
        exit 3
    }

    $porcelain = @(git status --porcelain)
    $tracked = @($porcelain | Where-Object { $_ -and -not $_.StartsWith("??") })
    if ($tracked.Count -gt 0) {
        Write-Host "Refusing to sync because tracked files are dirty:"
        $tracked | ForEach-Object { Write-Host $_ }
        exit 4
    }

    git fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    git merge --ff-only "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "Fast-forwarded $Checkout to origin/$Branch at $(git rev-parse --short HEAD)."
    Write-Host "Remotes were not changed. Untracked files were left in place."
}
finally {
    Pop-Location
}
