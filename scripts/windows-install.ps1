$ErrorActionPreference='Stop'
$base=$PSScriptRoot
$state=Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$wheel=Join-Path $base 'kleinanzeiger-0.1.1-py3-none-any.whl'
$sums=Join-Path $base 'SHA256SUMS.txt'
if (!(Test-Path $wheel -PathType Leaf) -or !(Test-Path $sums -PathType Leaf)) { throw 'Wheel oder Pruefsummen fehlen.' }
$entry=@(Get-Content $sums | Where-Object { $_ -match '^[0-9a-fA-F]{64}\s+\*?kleinanzeiger-0\.1\.1-py3-none-any\.whl$' })
if ($entry.Count -ne 1) { throw 'Wheel-Pruefsumme fehlt.' }
$expected=($entry[0] -split '\s+')[0].ToLowerInvariant()
$actual=(Get-FileHash $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw 'Die Paket-Pruefsumme ist ungueltig.' }
if (!(Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python 3.12/3.13 von python.org installieren.' }
$version='3.12'
& py -3.12 -c 'import sys; assert sys.version_info[:2] == (3,12)' 2>$null
if ($LASTEXITCODE -ne 0) {
  $version='3.13'
  & py -3.13 -c 'import sys; assert sys.version_info[:2] == (3,13)' 2>$null
  if ($LASTEXITCODE -ne 0) { throw 'Python 3.12/3.13 fehlt.' }
}
New-Item -ItemType Directory -Path $state -Force | Out-Null
$venv=Join-Path $state '.venv'
& py "-$version" -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Python-Umgebung konnte nicht erstellt werden.' }
$python=Join-Path $venv 'Scripts\python.exe'
& $python -m pip install "$wheel[private-web]"
if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen.' }
& $python -m mark_api.launcher --help
if ($LASTEXITCODE -ne 0) { throw 'Startpruefung fehlgeschlagen.' }
Write-Host 'Installation abgeschlossen. Jetzt Starten.cmd ausfuehren.'
