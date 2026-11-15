@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" configure_notifications.py contact
pause
