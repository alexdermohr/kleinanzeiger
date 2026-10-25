$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$python = Join-Path $state '.venv\Scripts\python.exe'
$db = Join-Path $state 'anzeigen.sqlite'
$backups = Join-Path $state 'Backups'
if (!(Test-Path $python -PathType Leaf) -or !(Test-Path $db -PathType Leaf)) {
  throw 'Keine installierte Kleinanzeiger-Datenbank vorhanden.'
}
New-Item -ItemType Directory -Path $backups -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$dest = Join-Path $backups "Kleinanzeiger-$stamp.sqlite"
& $python -m mark_api.backup_cli --db $db --backup $dest
if ($LASTEXITCODE -ne 0) { throw 'Backup fehlgeschlagen. Keine Sicherung als gueltig behandeln.' }
Write-Host "Sicherung: $dest"
Write-Host 'Die .manifest.json-Datei gehoert zwingend dazu.'
