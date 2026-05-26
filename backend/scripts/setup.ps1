# One-time setup: create venv, install deps, download GTFS, load into SQLite.
# Run from project root: .\backend\scripts\setup.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtualenv..."
    python -m venv .venv
}

Write-Host "Activating venv..."
. .\.venv\Scripts\Activate.ps1

Write-Host "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

Write-Host "Downloading + loading GTFS static..."
python -m app.gtfs_static

Write-Host "Done. Next: run .\scripts\run_api.ps1 (API) and .\scripts\run_collector.ps1 (realtime)."
