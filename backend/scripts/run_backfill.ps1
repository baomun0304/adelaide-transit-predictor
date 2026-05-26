# One-shot: backfill delay_seconds for all old realtime_updates rows.
# Safe to run anytime, can be interrupted and resumed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. .\.venv\Scripts\Activate.ps1
python -m app.backfill
