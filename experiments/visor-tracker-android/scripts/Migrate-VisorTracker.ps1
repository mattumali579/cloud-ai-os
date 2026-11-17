# One-time, safety-checked migration from v0.3 ephemeral signing to v0.4 stable signing.
# Affects ONLY com.mattumali.visortracker.stream. Does not touch Visor.
# Does NOT uninstall until the APK and original backup have both been validated.
[CmdletBinding()]
param(
    [switch]$Migrate,
    [string]$ApkPath = "$env:USERPROFILE\Downloads\Visor-Fast-Tracking-v0.4-stable.apk"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Package = 'com.mattumali.visortracker.stream'
$ExpectedSize = 77718494L
$ExpectedSha256 = 'D22E9DDA1B1E5EA07DF9EFCF9524D22817BAD17AB313C3DD3A4610FF5F607206'
$Workspace = Join-Path $env:LOCALAPPDATA 'VisorTrackerMigration'
$BackupDir = Join-Path $Workspace ("backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$OldApk = Join-Path $BackupDir 'original-v0.3.apk'
$Archive = Join-Path $BackupDir 'recoverable-data.tar'
$ReportPath = Join-Path $BackupDir 'migration-report.txt'
$Uninstalled = $false
$InstalledNew = $false
$HasArchive = $false

function Report([string]$message) {
    Write-Host $message
    Add-Content -LiteralPath $ReportPath -Value $message -Encoding UTF8
}
function Fail([string]$message) { throw $message }

function Resolve-Adb {
    $command = Get-Command adb.exe -ErrorAction SilentlyContinue
    $paths = @(
        $(if ($command) { $command.Source }),
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
        (Join-Path $Workspace 'platform-tools\adb.exe')
    )
    foreach ($candidate in $paths) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [string]$candidate
        }
    }
    Report 'Installing official Android SDK Platform Tools locally (no app changes).'
    $zipFile = Join-Path $Workspace 'platform-tools-windows.zip'
    Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $zipFile -UseBasicParsing
    Expand-Archive -LiteralPath $zipFile -DestinationPath $Workspace -Force
    $resolved = Join-Path $Workspace 'platform-tools\adb.exe'
    if (!(Test-Path -LiteralPath $resolved -PathType Leaf)) {
        Fail 'Could not prepare Android SDK Platform Tools.'
    }
    return $resolved
}

function Run-Adb([string[]]$AdbArgs) {
    # In Windows PowerShell 5.1, native stderr can become a terminating
    # NativeCommandError even when adb succeeds. Judge the native exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $result = @(& $script:Adb @AdbArgs 2>&1)
        $status = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($status -ne 0) {
        Fail ("adb failed: " + ($AdbArgs -join ' ') + " : " + ($result -join ' '))
    }
    return $result
}

function Check-Package([int]$expectedVersion) {
    $found = @(Run-Adb -AdbArgs @('shell', 'pm', 'list', 'packages', $Package))
    if ($found -notcontains ('package:' + $Package)) {
        Fail "Expected app $Package not found. No other apps will be touched."
    }
    $dump = @(Run-Adb -AdbArgs @('shell', 'dumpsys', 'package', $Package))
    $match = [regex]::Match(($dump -join [Environment]::NewLine), 'versionCode=(\d+)')
    if (!$match.Success -or [int]$match.Groups[1].Value -ne $expectedVersion) {
        Fail "Wrong installed version. Expected $expectedVersion. Aborting."
    }
}

function Export-DataArchive([string[]]$directories) {
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo.FileName = $script:Adb
    $p.StartInfo.Arguments = "exec-out run-as $Package tar cf - " + ($directories -join ' ')
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.CreateNoWindow = $true
    $p.StartInfo.RedirectStandardOutput = $true
    $p.StartInfo.RedirectStandardError = $true
    if (!$p.Start()) { Fail 'Cannot start private-data backup.' }
    try {
        $output = [System.IO.File]::Create($Archive)
        try { $p.StandardOutput.BaseStream.CopyTo($output) }
        finally { $output.Dispose() }
        $errors = $p.StandardError.ReadToEnd()
        $p.WaitForExit()
        if ($p.ExitCode -ne 0 -or (Get-Item -LiteralPath $Archive).Length -lt 512) {
            Fail "Private-data backup failed: $errors"
        }
    } finally { $p.Dispose() }
}

function Import-DataArchive {
    if (!$HasArchive) { return }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo.FileName = $script:Adb
    $p.StartInfo.Arguments = "shell -T run-as $Package tar xf -"
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.CreateNoWindow = $true
    $p.StartInfo.RedirectStandardInput = $true
    $p.StartInfo.RedirectStandardOutput = $true
    $p.StartInfo.RedirectStandardError = $true
    if (!$p.Start()) { Fail 'Cannot start private-data restore.' }
    try {
        $input = [System.IO.File]::OpenRead($Archive)
        try { $input.CopyTo($p.StandardInput.BaseStream) }
        finally {
            $input.Dispose()
            $p.StandardInput.Close()
        }
        $stdout = $p.StandardOutput.ReadToEnd()
        $errors = $p.StandardError.ReadToEnd()
        $p.WaitForExit()
        if ($p.ExitCode -ne 0) { Fail "Private-data restore failed: $stdout $errors" }
    } finally { $p.Dispose() }
}

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
Report "Migration backup directory: $BackupDir"
Report 'Only Visor Fast Tracking is in scope. The Visor glasses app is untouched.'

try {
    $Adb = Resolve-Adb
    $devices = @(& $Adb devices | Where-Object { $_ -match '^\S+\s+device$' })
    if ($devices.Count -ne 1) {
        Fail 'Connect and authorize exactly one phone with USB debugging. Check the phone authorization prompt.'
    }
    Report ("Phone: " + ((Run-Adb -AdbArgs @('shell', 'getprop', 'ro.product.model')) -join '').Trim())
    Check-Package 3

    if (!(Test-Path -LiteralPath $ApkPath -PathType Leaf)) {
        Report 'Checking phone Downloads for the v0.4 APK...'
        $phonePath = '/sdcard/Download/Visor-Fast-Tracking-v0.4-stable.apk'
        $null = & $Adb pull $phonePath $ApkPath 2>$null
        if ($LASTEXITCODE -ne 0) {
            Fail 'Download v0.4 APK to PC or phone Downloads using the verified Drive link; retry afterwards. No uninstall performed.'
        }
    }
    $apk = Get-Item -LiteralPath $ApkPath
    if ($apk.Length -ne $ExpectedSize) {
        Fail "Wrong APK size: $($apk.Length), expected $ExpectedSize. No uninstall performed."
    }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($apk.FullName)
    try {
        $entries = @($zip.Entries | ForEach-Object { $_.FullName })
        if ($entries -notcontains 'AndroidManifest.xml' -or $entries -notcontains 'classes.dex') {
            Fail 'Invalid APK layout. No uninstall performed.'
        }
    } finally { $zip.Dispose() }
    $hash = (Get-FileHash -LiteralPath $apk.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($hash -ne $ExpectedSha256) {
        Fail 'APK SHA-256 does not match the verified v0.4 stable-signed artifact. No uninstall performed.'
    }
    Report ("Verified APK file: " + $apk.FullName)
    Report ("Verified SHA256: " + $hash)

    $installed = @(Run-Adb -AdbArgs @('shell', 'pm', 'path', $Package))
    $base = @($installed | Where-Object { $_ -match '^package:.*base\.apk$' })
    if ($base.Count -ne 1) { Fail 'Cannot identify the original installed base APK. Aborting.' }
    $remoteApk = ($base[0] -replace '^package:', '').Trim()
    Run-Adb -AdbArgs @('pull', $remoteApk, $OldApk) | Out-Null
    if (!(Test-Path -LiteralPath $OldApk) -or (Get-Item -LiteralPath $OldApk).Length -lt 1000000) {
        Fail 'Original APK backup was unsuccessful. No uninstall performed.'
    }
    Report ("Original v0.3 APK saved: " + $OldApk)

    $probe = @(& $Adb shell run-as $Package pwd 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Fail ("Private app data is not accessible. No uninstall performed. " + ($probe -join ' '))
    }
    $saveDirs = @()
    foreach ($dir in @('shared_prefs', 'files', 'databases')) {
        $null = & $Adb shell run-as $Package ls -d $dir 2>$null
        if ($LASTEXITCODE -eq 0) { $saveDirs += $dir }
    }
    Run-Adb -AdbArgs @('shell', 'am', 'force-stop', $Package) | Out-Null
    if ($saveDirs.Count -gt 0) {
        Export-DataArchive $saveDirs
        $HasArchive = $true
        Report ("Private app data backed up: " + $Archive)
    } else {
        Report 'No existing app preference/files/databases directories to migrate.'
    }

    if (!$Migrate) {
        Report 'PREFLIGHT PASSED. No uninstall performed; rerun with -Migrate to apply the authorized change.'
        return
    }

    Report 'Removing ONLY the old com.mattumali.visortracker.stream installation.'
    Run-Adb -AdbArgs @('uninstall', $Package) | Out-Null
    $Uninstalled = $true

    Report 'Installing permanently signed v0.4 with the SAME package ID.'
    Run-Adb -AdbArgs @('install', $apk.FullName) | Out-Null
    $InstalledNew = $true
    Check-Package 4
    Import-DataArchive
    Run-Adb -AdbArgs @('shell', 'am', 'start', '-n', "$Package/com.mattumali.visortracker.MainActivity") | Out-Null
    Report 'SUCCESS: v0.4 installed, versionCode verified, saved private data restored.'
    Report 'On your phone: check overlay permission, Visor Camera consent, and Higher Accuracy.'
    Report 'Then test boxes over an active Visor Live View. Hardware FPS is not proven by ADB installation.'
} catch {
    $reason = $_.Exception.Message
    Report ("MIGRATION ERROR: " + $reason)
    if ($Uninstalled) {
        Report 'Restoring the backed-up v0.3 installation now.'
        try {
            if ($InstalledNew) {
                Run-Adb -AdbArgs @('uninstall', $Package) | Out-Null
            }
            Run-Adb -AdbArgs @('install', $OldApk) | Out-Null
            Check-Package 3
            Import-DataArchive
            Report 'ROLLBACK SUCCEEDED. Original app restored; recheck its permissions.'
        } catch {
            Report ("ROLLBACK ERROR: " + $_.Exception.Message)
            Report ("Both backups remain in " + $BackupDir)
        }
    } else {
        Report 'The installed v0.3 was not uninstalled.'
    }
    throw
}
