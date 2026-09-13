$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11 or newer is required."
}

python -m pip install --upgrade pyinstaller certifi
python -m PyInstaller `
    --noconfirm `
    --clean `
    --name LocalVideoStudio `
    --add-data "app/static;app/static" `
    run.py

Write-Host "Built dist/LocalVideoStudio/LocalVideoStudio.exe"
Write-Host "FFmpeg and FFprobe must be installed or bundled before distribution."
