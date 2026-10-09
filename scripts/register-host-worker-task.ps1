# Registers the single Cloud AI OS host-worker scheduled task (At log on + restart).
# Safe to re-run. Does not print secrets.
param(
    [switch]$Unregister
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
$TaskName = "CloudAIOS-HostWorker"
$VerifyName = "CloudAIOS-PostBootVerify"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Supervisor = Join-Path $Repo "scripts\host_worker_supervisor.py"
$VerifyScript = Join-Path $Repo "scripts\post-boot-verify.ps1"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $VerifyName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "unregistered $TaskName $VerifyName"
    exit 0
}

if (-not (Test-Path $Python)) { throw "missing $Python" }
if (-not (Test-Path $Supervisor)) { throw "missing $Supervisor" }

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$settings.DisallowStartIfOnBatteries = $false

# Use venv python.exe (not pythonw): Windows venv pythonw.exe also launches the
# base pythonw, which produced two supervisors. Hidden PowerShell keeps it windowless.
$psArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"Set-Location -LiteralPath '$Repo'; & '$Python' '$Supervisor'`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs -WorkingDirectory $Repo
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Repeat every minute so a killed process is relaunched even if Task Scheduler
# still thinks the previous run is active.
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(15) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration ([TimeSpan]::FromDays(3650))
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $repeat) -Settings $settings -Principal $principal -Force | Out-Null

$verifyAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$VerifyScript`"" `
    -WorkingDirectory $Repo
$verifyTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$verifyTrigger.Delay = "PT2M"
Register-ScheduledTask -TaskName $VerifyName -Action $verifyAction -Trigger $verifyTrigger -Settings $settings -Principal $principal -Force | Out-Null

Write-Output "registered $TaskName and $VerifyName"
Get-ScheduledTask -TaskName $TaskName, $VerifyName | Format-Table TaskName, State -AutoSize
