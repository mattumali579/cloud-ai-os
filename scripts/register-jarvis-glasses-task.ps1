$ErrorActionPreference = "Stop"

$taskName = "CloudAIOS-JarvisGlasses"
$runner = Join-Path $PSScriptRoot "run-jarvis-glasses.ps1"
$config = Join-Path $env:LOCALAPPDATA "CloudAIOS\jarvis-glasses.json"

if (-not (Test-Path -LiteralPath $config)) {
    throw "Configure the Visor bridge first with: .\.venv\Scripts\python.exe .\scripts\jarvis_glasses.py configure"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "$taskName registered and started."
