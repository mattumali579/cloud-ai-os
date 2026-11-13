$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "scripts\jarvis_glasses.py"
$logDirectory = Join-Path $repo "logs"
$log = Join-Path $logDirectory "jarvis-glasses.log"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
Set-Location -LiteralPath $repo

# Stop-ScheduledTask terminates this PowerShell wrapper but can leave the
# Python child alive. Replace only stale instances of this exact bridge so a
# manual task restart cannot create two subscribers for one glasses utterance.
$escapedScript = [regex]::Escape($script)
$stale = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match $escapedScript -and
        $_.CommandLine -match '\srun(?:\s|$)'
    }
foreach ($process in $stale) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($stale) {
    Start-Sleep -Milliseconds 750
}

# Windows PowerShell 5.1 surfaces redirected native stderr as a non-terminating
# NativeCommandError. With ErrorActionPreference=Stop that killed the healthy
# long-running bridge whenever it logged a recoverable Visor microphone error.
# JARVIS_AUDIT_DATABASE_URL is loaded inside jarvis_glasses.py from Windows
# Credential Manager (CloudAIOS/JarvisGlasses/AuditDatabaseUrl) when unset.
$ErrorActionPreference = "Continue"
& $python $script run *>> $log
exit $LASTEXITCODE
