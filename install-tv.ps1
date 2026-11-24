# Installs the Sharek receiver on an Android / Google TV over Wi-Fi (ADB).
# Usage:  .\install-tv.ps1 192.168.1.50
param([Parameter(Mandatory = $true)][string]$TvIp)

$ErrorActionPreference = "Stop"
# Uses the newest Sharek-TV*.apk next to this script (download it from the Releases page).
$apk = Get-ChildItem $PSScriptRoot -Filter "Sharek-TV*.apk" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $apk) { Write-Host "Put Sharek-TV.apk next to this script first." -ForegroundColor Yellow; exit 1 }
$target = if ($TvIp -match ":") { $TvIp } else { "${TvIp}:5555" }

Write-Host "Connecting to $target ... (accept the 'Allow USB debugging' prompt on the TV)"
adb connect $target
Start-Sleep -Seconds 2
$state = (adb -s $target get-state 2>&1) -join ""
if ($state -notmatch "device") {
    Write-Host "Not authorised yet: approve the prompt on the TV, then run this script again." -ForegroundColor Yellow
    exit 1
}
adb -s $target install -r $apk
adb -s $target shell monkey -p com.sharek.tv -c android.intent.category.LEANBACK_LAUNCHER 1 | Out-Null
Write-Host "Done. Sharek is open on the TV." -ForegroundColor Green
