# Read-only recovery probe for the certificate that signed v0.3.
# Does not upload, copy, delete, or disclose any signing key or password.
# Run from Windows PowerShell: powershell -NoProfile -ExecutionPolicy Bypass -File .\Find-OriginalSigningKey.ps1
param([string[]]$Roots = @())

$ErrorActionPreference = 'Stop'
$target = '2035D392ECF99508618B1D7E0A835E07D6FF79E0EA64634645BB9AEE31FF451C'
$tools = @()
$which = Get-Command keytool.exe -ErrorAction SilentlyContinue
if ($which) { $tools += $which.Source }
if ($env:JAVA_HOME) { $tools += (Join-Path $env:JAVA_HOME 'bin\keytool.exe') }
if ($env:ProgramFiles) { $tools += (Join-Path $env:ProgramFiles 'Android\Android Studio\jbr\bin\keytool.exe') }
if ($env:ProgramFiles) {
    $javaRoot = Join-Path $env:ProgramFiles 'Java'
    if (Test-Path $javaRoot) {
        $tools += @(Get-ChildItem $javaRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            Join-Path $_.FullName 'bin\keytool.exe'
        })
    }
}
$keytool = @($tools | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1)
if ($keytool.Count -eq 0) {
    Write-Host 'BLOCKED: Java keytool.exe not found; cannot verify stored signing keys.'
    exit 2
}
if ($Roots.Count -eq 0) {
    $Roots = @(
        (Join-Path $env:USERPROFILE '.android'),
        (Join-Path $env:USERPROFILE 'cloud-ai-os'),
        (Join-Path $env:USERPROFILE 'Documents'),
        (Join-Path $env:LOCALAPPDATA 'Android')
    )
}
Write-Host 'Searching local key files for the v0.3 signing certificate (read-only).'
Write-Host "Original v0.3 SHA-256: $target"
$candidates = New-Object 'System.Collections.Generic.List[string]'
foreach ($root in $Roots | Select-Object -Unique) {
    if (!(Test-Path $root)) { continue }
    Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in '.jks', '.keystore', '.p12', '.pfx' } |
        ForEach-Object { $candidates.Add($_.FullName) }
}
$found = $false
$unverified = New-Object 'System.Collections.Generic.List[string]'
foreach ($file in $candidates | Select-Object -Unique) {
    # Android debug.keystore uses the well-known 'android' password.
    # Other keystores may use a custom password; those are NOT modified.
    $result = & $keytool[0] -list -v -keystore $file -storepass android 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $unverified.Add($file)
        continue
    }
    $match = [regex]::Match($result, '(?i)SHA256:\s*([0-9A-F:]+)')
    if (!$match.Success) { $unverified.Add($file); continue }
    $digest = $match.Groups[1].Value.Replace(':','').ToUpperInvariant()
    if ($digest -eq $target) {
        Write-Host "MATCH FOUND: $file"
        Write-Host 'Preserve this file securely. It is the original signer; do not post it publicly.'
        $found = $true
    }
}
if ($found) { exit 0 }
Write-Host "NO VERIFIED MATCH among $($candidates.Count) candidate key files."
if ($unverified.Count -gt 0) {
    Write-Host "Could not inspect $($unverified.Count) custom-password key files."
    $unverified | ForEach-Object { Write-Host "PASSWORD UNKNOWN: $_" }
}
Write-Host 'The v0.3 APK contains a public certificate, not the original private key.'
Write-Host 'Without a matching private key, Android cannot install new code over v0.3 in place.'
exit 1
