# Diagnose Samsung's generic "App not installed" for Visor Fast Tracking v0.5.
# No USB, no new APK download, and NO uninstall. Uses on-phone Downloads APK.
# Android Wireless Debugging pairing is performed locally with the owner.
[CmdletBinding()]
param(
    [string]$LocalApk = "",
    [switch]$SkipPairing
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$package = 'com.mattumali.visortracker.stream'
$expectedSize = 77735018L
$expectedSha = '0E957C8DCF42FEA00FE60584A603D17CD64E304B6BB2A97E86CF9468707F455E'
$root = Join-Path $env:LOCALAPPDATA 'VisorTrackerMigration'
$adbCandidates = @(
    (Join-Path $root 'platform-tools\adb.exe'),
    (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe')
)
$foundCmd = Get-Command adb.exe -ErrorAction SilentlyContinue
if ($foundCmd) { $adbCandidates += $foundCmd.Source }
$adb = @($adbCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1)
if ($adb.Count -ne 1) {
    throw 'ADB was not found. Previous migration script should have placed it under LocalAppData\VisorTrackerMigration\platform-tools.'
}
$adb = $adb[0]
Write-Host 'This script will NOT uninstall Visor, uninstall Tracker, or download an APK.'
Write-Host 'On the Samsung, turn OFF Visor Live View and connect to the SAME Wi-Fi network as Jarvis.'
Write-Host 'Settings > Developer options > Wireless debugging > enable it.'
if (!$SkipPairing) {
    Write-Host 'Tap Pair device with pairing code on the Samsung.'
    $pairTarget = (Read-Host 'Enter PAIRING IP:PORT from the pairing popup').Trim()
    $pairCode = (Read-Host 'Enter six-digit pairing code (local only)').Trim()
    if ($pairTarget -notmatch '^[0-9a-fA-F.:]+:[0-9]{2,5}$' -or $pairCode -notmatch '^\d{6}$') {
        throw 'Invalid pairing endpoint or code. Nothing was changed.'
    }
    & $adb pair $pairTarget $pairCode
    if ($LASTEXITCODE -ne 0) { throw 'Wireless pairing failed. Confirm same Wi-Fi network and use the pairing port, not debugging port.' }
}
Write-Host 'Back out to the main Wireless debugging page, which displays a DIFFERENT IP address & Port.'
$connectTarget = (Read-Host 'Enter DEBUGGING IP:PORT from main Wireless debugging page').Trim()
if ($connectTarget -notmatch '^[0-9a-fA-F.:]+:[0-9]{2,5}$') { throw 'Invalid debugging IP:PORT.' }
& $adb connect $connectTarget
if ($LASTEXITCODE -ne 0) { throw 'ADB could not connect. Check same Wi-Fi, Wireless debugging enabled, and port.' }

$devices = @(& $adb devices | Where-Object { $_ -match '^\S+\s+device$' })
if ($devices.Count -ne 1) { throw "Expected one authorized phone; found $($devices.Count). Check adb devices." }
Write-Host '=== PHONE ==='
& $adb shell getprop ro.product.model
& $adb shell getprop ro.build.version.release
& $adb shell getprop ro.build.version.sdk
Write-Host '=== EXISTING PACKAGE (including uninstalled-for-user records) ==='
& $adb shell pm list users
& $adb shell pm list packages -u $package
Write-Host '=== CHECKING EXISTING APK ON PHONE ==='
if (-not $LocalApk) {
    $LocalApk = Join-Path $root 'Visor-Fast-Tracking-verified-v5.apk'
    $remotePaths = @(
        '/sdcard/Download/Visor-Fast-Tracking.apk',
        '/sdcard/Downloads/Visor-Fast-Tracking.apk',
        '/sdcard/Documents/Visor-Fast-Tracking.apk'
    )
    $pulled = $false
    foreach ($remote in $remotePaths) {
        $null = & $adb pull $remote $LocalApk 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $LocalApk -PathType Leaf)) {
            $pulled = $true
            break
        }
    }
    if (!$pulled) {
        throw 'APK not found in phone Download/Documents folders. In My Files, long-press the existing APK > Details to identify its storage location. No apps were changed.'
    }
}
if (!(Test-Path -LiteralPath $LocalApk -PathType Leaf)) { throw "File missing: $LocalApk" }
$size = (Get-Item -LiteralPath $LocalApk).Length
$sha = (Get-FileHash -LiteralPath $LocalApk -Algorithm SHA256).Hash.ToUpperInvariant()
Write-Host "APK bytes: $size"
Write-Host "APK SHA256: $sha"
if ($size -ne $expectedSize -or $sha -ne $expectedSha) {
    throw 'APK does not match the validated v0.5 release. No install attempted.'
}
Write-Host '=== ANDROID INSTALLATION RESULT (exact error) ==='
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $result = @(& $adb install -r $LocalApk 2>&1)
    $resultCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $oldPreference }
$result | ForEach-Object { Write-Host $_ }
if ($resultCode -eq 0) {
    Write-Host 'INSTALL SUCCESS. Checking package version...'
    & $adb shell dumpsys package $package |
        Select-String -Pattern 'versionCode=' |
        Select-Object -First 2
    Write-Host 'Open Visor Fast Tracking on the Samsung and grant permissions.'
} else {
    Write-Host 'INSTALL REJECTED. The INSTALL_FAILED_... message above is the actual cause.'
    Write-Host 'No packages were uninstalled. Keep this output for targeted repair.'
    exit $resultCode
}
