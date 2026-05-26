@echo off
cd /d "C:\Adelaide Metro\frontend"
start "Transit Frontend" powershell -NoExit -ExecutionPolicy Bypass -File ".\serve.ps1"
