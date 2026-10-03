# Add one shop to schema mctb by running psql inside the Postgres container.
# Windows does not need bash or a host psql.
#
# powershell -ExecutionPolicy Bypass -File scripts\setup_tenant.ps1 `
#   -Slug northline -BusinessName "Northline Heating & Air" `
#   -TwilioNumber 4145550100 -OwnerName Dana -OwnerPhone 4145550199 `
#   -Container cloudos-db-1 -PgUser cloudos -PgDatabase cloudos `
#   -PublicBaseUrl https://HOST

param(
  [Parameter(Mandatory = $true)][string]$Slug,
  [Parameter(Mandatory = $true)][string]$BusinessName,
  [Parameter(Mandatory = $true)][string]$TwilioNumber,
  [Parameter(Mandatory = $true)][string]$OwnerName,
  [Parameter(Mandatory = $true)][string]$OwnerPhone,
  [string]$OwnerEmail = "",
  [string]$Hours = "Mon-Fri 8am-5pm",
  [string]$BookingLink = "",
  [string]$Timezone = "America/Chicago",
  [string]$CallMode = "forward",
  [string]$PublicBaseUrl = "",
  [string]$Container = "cloudos-db-1",
  [string]$PgUser = "cloudos",
  [string]$PgDatabase = "cloudos"
)

$ErrorActionPreference = "Stop"

if ($Slug -notmatch '^[a-z0-9-]{2,40}$') {
  Write-Error "Slug must be 2-40 characters of a-z, 0-9, or hyphen."
}
if ($CallMode -ne "forward" -and $CallMode -ne "dial") {
  Write-Error "Call mode must be forward or dial."
}

function Convert-UsPhone([string]$raw) {
  $digits = ($raw -replace '\D', '')
  if ($digits.Length -eq 10) { return "+1$digits" }
  if ($digits.Length -eq 11 -and $digits.StartsWith("1")) { return "+$digits" }
  Write-Error "Not a US phone number: $raw"
}

function Convert-SqlLiteral([string]$value) {
  return "'" + ($value -replace "'", "''") + "'"
}

$TwilioNumber = Convert-UsPhone $TwilioNumber
$OwnerPhone = Convert-UsPhone $OwnerPhone
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = -join ($bytes | ForEach-Object { $_.ToString("x2") })

$sql = @"
INSERT INTO mctb.tenants (
  slug, business_name, twilio_number, owner_name, owner_phone, owner_email,
  hours_text, booking_link, timezone, call_mode, dashboard_token
) VALUES (
  $(Convert-SqlLiteral $Slug),
  $(Convert-SqlLiteral $BusinessName),
  $(Convert-SqlLiteral $TwilioNumber),
  $(Convert-SqlLiteral $OwnerName),
  $(Convert-SqlLiteral $OwnerPhone),
  $(Convert-SqlLiteral $OwnerEmail),
  $(Convert-SqlLiteral $Hours),
  $(Convert-SqlLiteral $BookingLink),
  $(Convert-SqlLiteral $Timezone),
  $(Convert-SqlLiteral $CallMode),
  $(Convert-SqlLiteral $token)
);
"@

$sql | & docker exec -i $Container psql -U $PgUser -d $PgDatabase -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) {
  Write-Error "psql inside $Container failed with exit code $LASTEXITCODE"
}

$base = $PublicBaseUrl.TrimEnd("/")
Write-Output ""
Write-Output "Shop saved."
Write-Output "Twilio number: $TwilioNumber"
Write-Output "Owner phone:   $OwnerPhone"
Write-Output "Call mode:     $CallMode"
if ($base) {
  Write-Output "Voice webhook: $base/webhook/mctb-voice"
  Write-Output "SMS webhook:   $base/webhook/mctb-sms"
  Write-Output "Dashboard:     $base/webhook/mctb-dashboard?token=$token"
} else {
  Write-Output "Dashboard token: $token"
  Write-Output "Dashboard path:  /webhook/mctb-dashboard?token=$token"
  Write-Output "Pass -PublicBaseUrl to print the full links."
}
Write-Output "Give the owner the dashboard link. It is the password."
