# Serve the static frontend on http://localhost:5173
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m http.server 5173
