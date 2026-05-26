@echo off
REM Auto-starts the realtime collector in its own window.
REM Place a copy in shell:startup to run on every login.
cd /d "C:\Adelaide Metro\backend"
start "Transit Collector" powershell -NoExit -ExecutionPolicy Bypass -File ".\scripts\run_collector.ps1"
