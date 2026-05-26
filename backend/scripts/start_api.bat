@echo off
cd /d "C:\Adelaide Metro\backend"
start "Transit API" powershell -NoExit -ExecutionPolicy Bypass -File ".\scripts\run_api.ps1"
