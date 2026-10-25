$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$base = $PSScriptRoot
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$wheel = Join-Path $base 'kleinanzeiger-0.1.2-py3-none-any.whl'
$sums = Join-Path $base 'SHA256SUMS.txt'
if (!(Test-Path -LiteralPath $wheel -PathType Leaf) -or !(Test-Path -LiteralPath $sums -PathType Leaf)) {
  throw 'Paket oder SHA256SUMS.txt fehlen. Bitte das gesamte Windows-ZIP entpacken.'
}
$entry = @(Get-Content -LiteralPath $sums | Where-Object { $_ -match '^[0-9a-fA-F]{64}\s+\*?kleinanzeiger-0\.1\.2-py3-none-any\.whl$' })
if ($entry.Count -ne 1) { throw 'Keine eindeutige Wheel-Pruefsumme gefunden.' }
$expected = ($entry[0] -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw 'Die Paket-Pruefsumme stimmt nicht. Installation abgebrochen.' }

New-Item -ItemType Directory -Path $state -Force | Out-Null
$venv = Join-Path $state '.venv'
$installedPython = Join-Path $venv 'Scripts\python.exe'

function Test-Python([string]$exe, [string[]]$argumentsBefore = @()) {
  try {
    & $exe @argumentsBefore -c 'import sys; assert sys.version_info[:2] in ((3,12),(3,13))' 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

if (!(Test-Path -LiteralPath $installedPython -PathType Leaf) -or !(Test-Python $installedPython)) {
  $python = $null
  $prefix = @()
  $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
  if ($launcher) {
    foreach ($version in @('-3.13', '-3.12')) {
      if (Test-Python $launcher.Source @($version)) {
        $python = $launcher.Source
        $prefix = @($version)
        break
      }
    }
  }
  if (!$python) {
    $candidates = @()
    foreach ($home in @($env:LOCALAPPDATA, $env:ProgramFiles, [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'))) {
      if ($home) {
        $candidates += Join-Path $home 'Programs\Python\Python313\python.exe'
        $candidates += Join-Path $home 'Programs\Python\Python312\python.exe'
        $candidates += Join-Path $home 'Python313\python.exe'
        $candidates += Join-Path $home 'Python312\python.exe'
      }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
      $found = Get-Command $name -ErrorAction SilentlyContinue
      if ($found) { $candidates += $found.Source }
    }
    foreach ($candidate in $candidates) {
      if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and (Test-Python $candidate)) {
        $python = $candidate
        break
      }
    }
  }
  if (!$python) {
    # Official Python.org 64-bit installer; pinned digest from 3.13.17 release (2026-10-21).
    $runtime = Join-Path $state 'Runtime\Python313'
    $python = Join-Path $runtime 'python.exe'
    if (!(Test-Python $python)) {
      $url = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe'
      $pythonSha256 = '5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190'
      $temporary = Join-Path $env:TEMP ('kleinanzeiger-python-' + [guid]::NewGuid().ToString('N') + '.exe')
      try {
        Write-Host 'Python fehlt: lade den offiziellen Python-3.13-Installer ...'
        Invoke-WebRequest -Uri $url -OutFile $temporary -UseBasicParsing
        if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pythonSha256) {
          throw 'Python-Download wurde nicht authentifiziert (SHA-256).'
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $temporary
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
          throw 'Python-Installer hat keine gueltige Signatur der Python Software Foundation.'
        }
        New-Item -ItemType Directory -Path $runtime -Force | Out-Null
        $arguments = '/quiet InstallAllUsers=0 Include_launcher=0 Include_pip=1 Include_test=0 PrependPath=0 TargetDir="' + $runtime + '"'
        $process = Start-Process -FilePath $temporary -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Python-Installation fehlgeschlagen (Exitcode $($process.ExitCode))." }
        if (!(Test-Python $python)) { throw 'Der neu installierte Python-Interpreter ist nicht startbar.' }
      } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
      }
    }
  }
  Write-Host 'Richte die isolierte Kleinanzeiger-Python-Umgebung ein ...'
  & $python @prefix -m venv $venv
  if ($LASTEXITCODE -ne 0) { throw 'Python-Umgebung konnte nicht eingerichtet werden.' }
}
if (!(Test-Python $installedPython)) { throw 'Die Kleinanzeiger-Python-Umgebung funktioniert nicht.' }

Write-Host 'Installiere oder aktualisiere Kleinanzeiger (inklusive optionaler KI-Bibliotheken) ...'
& $installedPython -m pip install --upgrade ($wheel + '[private-web,generation]')
if ($LASTEXITCODE -ne 0) { throw 'Die Paketinstallation ist fehlgeschlagen. Bestehende Anzeigendaten bleiben erhalten.' }
& $installedPython -m mark_api.launcher --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Startpruefung fehlgeschlagen.' }

# Existing Google Chrome has priority; without Chrome download a signed Google
# Chrome-for-Testing binary into the user profile (no admin rights needed).
. (Join-Path $base 'windows-chrome.ps1')
$chrome = Find-KleinanzeigerChrome -StateDirectory $state
if (!$chrome) {
  Write-Host 'Chrome fehlt: lade eine offizielle signierte Google-Chrome-Laufzeit ...'
  Install-KleinanzeigerChrome -StateDirectory $state
  $chrome = Find-KleinanzeigerChrome -StateDirectory $state
  if (!$chrome) { throw 'Chrome konnte nicht bereitgestellt werden.' }
}
Write-Host 'Installation/Aktualisierung abgeschlossen. Bestehende Daten und Chrome-Profil bleiben erhalten.'
Write-Host 'Jetzt Starten.cmd ausfuehren. Fuer optionale KI: KI-einrichten.cmd.'
