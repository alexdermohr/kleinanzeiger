$ErrorActionPreference='Stop'
$state=Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$python=Join-Path $state '.venv\Scripts\python.exe'
$db=Join-Path $state 'anzeigen.sqlite'
if (!(Test-Path $python -PathType Leaf)) { throw 'Zuerst Installieren.cmd ausfuehren.' }
$chromeLocations=@()
foreach($folder in @([Environment]::GetFolderPath('ProgramFiles'),[Environment]::GetFolderPath('ProgramFilesX86'),$env:LOCALAPPDATA)) {
  if($folder) { $chromeLocations += Join-Path $folder 'Google\Chrome\Application\chrome.exe' }
}
$chrome=$chromeLocations | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1
if (!$chrome) { throw 'Google Chrome muss installiert sein.' }
$profile=Join-Path $state 'ChromeProfile'
New-Item -ItemType Directory -Path $profile -Force | Out-Null
$args='--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="{0}" https://www.kleinanzeigen.de/m-meine-anzeigen.html' -f $profile
Start-Process -FilePath $chrome -ArgumentList $args
Write-Host 'Im neuen Chrome-Fenster anmelden und nur die Anzeigenverwaltung offen lassen.'
Read-Host 'Nach erfolgreichem Login ENTER druecken'
$extra=@()
if (!(Test-Path $db -PathType Leaf)) { $extra+='--init-db' }
& $python -m mark_api.launcher --db $db --cdp-port 9222 @extra
if ($LASTEXITCODE -ne 0) { throw 'Produktstart fehlgeschlagen. Chrome/CDP/Login pruefen.' }
