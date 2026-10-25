@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-install.ps1"
if errorlevel 1 (echo Installation fehlgeschlagen. & pause & exit /b 1)
echo Installation abgeschlossen.
pause
