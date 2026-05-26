# Re-download Adelaide Metro GTFS static and reload into SQLite.
# Schedule weekly to catch route/timetable changes.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. .\.venv\Scripts\Activate.ps1
python -m app.gtfs_static
