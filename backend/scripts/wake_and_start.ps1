# Runs when the PC wakes at 5 AM. Re-launches all 3 services if not already running.
$ErrorActionPreference = "SilentlyContinue"

function Start-IfMissing($title, $bat) {
    $running = Get-Process powershell -ErrorAction SilentlyContinue |
               Where-Object { $_.MainWindowTitle -eq $title }
    if (-not $running) {
        Start-Process cmd -ArgumentList "/c", $bat -WindowStyle Normal
    }
}

Start-IfMissing "Transit Collector" "C:\Adelaide Metro\backend\scripts\start_collector.bat"
Start-IfMissing "Transit API"       "C:\Adelaide Metro\backend\scripts\start_api.bat"
Start-IfMissing "Transit Frontend"  "C:\Adelaide Metro\frontend\start_frontend.bat"
