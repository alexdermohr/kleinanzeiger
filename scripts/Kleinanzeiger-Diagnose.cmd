@echo off
setlocal
if not exist "%~dp0windows-diagnostics.ps1" (
  echo Kleinanzeiger-Diagnose fehlt. Bitte das offizielle ZIP erneut entpacken.
  goto :end
)
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0windows-diagnostics.ps1"
:end
echo.
echo Zum Schliessen eine Taste druecken.
pause >nul
