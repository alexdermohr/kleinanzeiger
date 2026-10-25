@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-restore.ps1"
if errorlevel 1 (echo Keine erfolgreiche Wiederherstellung. & pause & exit /b 1)
pause
