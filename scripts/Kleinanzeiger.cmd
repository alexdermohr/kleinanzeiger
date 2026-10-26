@echo off
setlocal
if not exist "%~dp0windows-center.ps1" (
  echo Bitte das vollstaendige Kleinanzeiger-Windows-ZIP entpacken.
  pause
  exit /b 1
)
start "" powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0windows-center.ps1"
exit /b 0
