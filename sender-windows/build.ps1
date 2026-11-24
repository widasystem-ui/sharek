# Builds dist\Sharek\Sharek.exe and release\Sharek-Windows-v<version>.zip
param([string]$Version = "1.0")
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path $here
$work = Join-Path $env:TEMP "sharek-pyinstaller"

python -m PyInstaller --noconfirm --clean --windowed --name Sharek --icon "$here\sharek.ico" `
    --add-data "$here\ui;ui" --collect-data soundcard --hidden-import pystray._win32 `
    --exclude-module tkinter --exclude-module matplotlib --exclude-module pytest `
    --workpath $work --specpath $work --distpath "$root\dist" "$here\app.py"

New-Item -ItemType Directory -Force "$root\release" | Out-Null
$zip = "$root\release\Sharek-Windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$root\dist\Sharek" -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Built $zip"
