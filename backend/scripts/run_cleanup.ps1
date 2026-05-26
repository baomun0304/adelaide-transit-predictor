# Aggregate + prune realtime_updates older than 7 days. Safe to run anytime.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. .\.venv\Scripts\Activate.ps1
python -m app.cleanup --keep-days 7
