@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-ki-einrichten.ps1"
if errorlevel 1 (echo KI-Einrichtung fehlgeschlagen. & pause & exit /b 1)
pause
