# Poll GTFS-Realtime every 30s and store updates. Keep this running in background.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. .\.venv\Scripts\Activate.ps1
python -m app.gtfs_realtime
