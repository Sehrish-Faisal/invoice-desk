@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1
start "" "http://127.0.0.1:8030"
