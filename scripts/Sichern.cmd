@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-backup.ps1"
if errorlevel 1 (echo Sicherung nicht erfolgreich. & pause & exit /b 1)
pause
