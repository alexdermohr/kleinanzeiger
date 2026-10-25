$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$python = Join-Path $state '.venv\Scripts\python.exe'
$db = Join-Path $state 'anzeigen.sqlite'
$backups = Join-Path $state 'Backups'
if (!(Test-Path $python -PathType Leaf)) {
  throw 'Zuerst Kleinanzeiger installieren.'
}
foreach ($suffix in @('', '-wal', '-shm', '-journal')) {
  if (Test-Path -LiteralPath ($db + $suffix)) {
    throw "Wiederherstellung nur in eine neue, nicht vorhandene Datenbank. Bestehende Dateien vorher bei beendetem Produkt sicher archivieren: $suffix"
  }
}
$available = @(Get-ChildItem -LiteralPath $backups -Filter '*.sqlite' -File -ErrorAction SilentlyContinue)
if ($available.Count -eq 0) { throw 'Keine Windows-Kleinanzeiger-Backups gefunden.' }
Write-Host 'Verfuegbare Sicherungen:'
$available | ForEach-Object { Write-Host $_.Name }
$name = Read-Host 'Dateinamen der wiederherzustellenden Sicherung genau eingeben'
if (!$name -or $name -ne [IO.Path]::GetFileName($name)) { throw 'Ungueltiger Dateiname.' }
$source = Join-Path $backups $name
if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Backup fehlt.' }
$confirmation = Read-Host 'Verifizierte Sicherung in neue Anzeigen-Datenbank einspielen? Mit JA bestaetigen'
if ($confirmation -cne 'JA') { throw 'Abgebrochen ohne Aenderung.' }
& $python -m mark_api.windows_backup --backup $source --db $db
if ($LASTEXITCODE -ne 0) { throw 'Wiederherstellung abgewiesen. Bestehende Dateien wurden nicht ueberschrieben.' }
Write-Host 'Wiederherstellung in neue Datenbank erfolgreich.'
