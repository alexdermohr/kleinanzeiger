$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$base = $PSScriptRoot
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$wheel = Join-Path $base 'kleinanzeiger-0.1.14-py3-none-any.whl'
$sums = Join-Path $base 'SHA256SUMS.txt'
if (!(Test-Path -LiteralPath $wheel -PathType Leaf) -or !(Test-Path -LiteralPath $sums -PathType Leaf)) {
  throw 'Paket oder SHA256SUMS.txt fehlen. Bitte das gesamte Windows-ZIP entpacken.'
}
$wheelName = [IO.Path]::GetFileName($wheel)
$entryPattern = '^[0-9a-fA-F]{64}\s+\*?' + [regex]::Escape($wheelName) + '$'
$entry = @(Get-Content -LiteralPath $sums | Where-Object { $_ -match $entryPattern })
if ($entry.Count -ne 1) { throw 'Keine eindeutige Wheel-Pruefsumme gefunden.' }
$expected = ($entry[0] -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw 'Die Paket-Pruefsumme stimmt nicht. Installation abgebrochen.' }

# All package and dependency writes happen in a new disposable environment.
# The last working program remains untouched until verification succeeds.
New-Item -ItemType Directory -Path $state -Force | Out-Null
$venv = Join-Path $state '.venv'
$installedPython = Join-Path $venv 'Scripts\python.exe'
$operationId = [guid]::NewGuid().ToString('N')
$stage = Join-Path $state ('.venv-stage-' + $operationId)
$stagePython = Join-Path $stage 'Scripts\python.exe'
$rollback = Join-Path $state ('.venv-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $operationId)
$invalid = Join-Path $state ('.venv-invalid-' + $operationId)

function Test-Python([string]$exe, [string[]]$argumentsBefore = @()) {
  try {
    & $exe @argumentsBefore -c 'import sys; assert sys.version_info[:2] in ((3,12),(3,13))' 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

$python = $null
$prefix = @()
if (Test-Python $installedPython) {
  # Prefer the base interpreter of the currently installed environment.
  # Never run pip against that environment while building the replacement.
  try {
    $candidate = @(& $installedPython -c 'import sys; print(getattr(sys, "_base_executable", "") or "")' 2>$null)
    if ($LASTEXITCODE -eq 0 -and $candidate.Count -eq 1 -and
        (Test-Python ([string]$candidate[0]))) { $python = [string]$candidate[0] }
  } catch { }
}
if (!$python) {
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
    foreach ($candidateRoot in @($env:LOCALAPPDATA, $env:ProgramFiles, [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'))) {
      if ($candidateRoot) {
        $candidates += Join-Path $candidateRoot 'Programs\Python\Python313\python.exe'
        $candidates += Join-Path $candidateRoot 'Programs\Python\Python312\python.exe'
        $candidates += Join-Path $candidateRoot 'Python313\python.exe'
        $candidates += Join-Path $candidateRoot 'Python312\python.exe'
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

}

$mayDisposeStage = $true
try {
  Write-Host 'Richte eine neue isolierte Kleinanzeiger-Umgebung ein ...'
  & $python @prefix -m venv $stage
  if ($LASTEXITCODE -ne 0 -or !(Test-Python $stagePython)) {
    throw 'Die neue Python-Umgebung konnte nicht eingerichtet werden.'
  }
  Write-Host 'Installiere die neue Version getrennt von der vorhandenen Installation ...'
  & $stagePython -m pip install ($wheel + '[private-web,generation]')
  if ($LASTEXITCODE -ne 0) { throw 'Die neue Paketinstallation ist fehlgeschlagen. Die bisherige Version bleibt erhalten.' }
  & $stagePython -m mark_api.launcher --help | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Die neue Version hat die Startpruefung nicht bestanden. Die bisherige Version bleibt erhalten.' }
  & $stagePython -c "import importlib.metadata as m; assert m.version('kleinanzeiger') == '0.1.14'"
  if ($LASTEXITCODE -ne 0) { throw 'Die neue Version stimmt nicht mit dem geprueften Paket ueberein.' }

  # Chrome readiness is checked BEFORE the old working application is moved.
  . (Join-Path $base 'windows-chrome.ps1')
  $chrome = Find-KleinanzeigerChrome -StateDirectory $state
  if (!$chrome) {
    Write-Host 'Chrome fehlt: lade eine offizielle signierte Google-Chrome-Laufzeit ...'
    Install-KleinanzeigerChrome -StateDirectory $state
    $chrome = Find-KleinanzeigerChrome -StateDirectory $state
    if (!$chrome) { throw 'Chrome konnte nicht bereitgestellt werden. Die bisherige Version bleibt erhalten.' }
  }

  # The control center prohibits updates while its product process is active.
  # Also fail safely when Windows keeps any environment file locked.
  $hadOld = Test-Path -LiteralPath $venv
  $oldMoved = $false
  $newMoved = $false
  try {
    if ($hadOld) {
      Rename-Item -LiteralPath $venv -NewName (Split-Path -Leaf $rollback) -ErrorAction Stop
      $oldMoved = $true
    }
    if (Test-Path -LiteralPath $venv) { throw 'Der Installationspfad ist weiterhin belegt.' }
    Rename-Item -LiteralPath $stage -NewName (Split-Path -Leaf $venv) -ErrorAction Stop
    $newMoved = $true
    # Verify the environment a second time at its final, relocated path.
    if (!(Test-Python $installedPython)) { throw 'Neue Python-Umgebung ist nach dem Wechsel nicht startbar.' }
    & $installedPython -m mark_api.launcher --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Neue Version ist nach dem Wechsel nicht startbar.' }
    & $installedPython -c "import importlib.metadata as m; assert m.version('kleinanzeiger') == '0.1.14'"
    if ($LASTEXITCODE -ne 0) { throw 'Neue Paketversion ist nach dem Wechsel ungueltig.' }
  } catch {
    $activationError = $_.Exception.Message
    try {
      if ($newMoved) {
        Rename-Item -LiteralPath $venv -NewName (Split-Path -Leaf $invalid) -ErrorAction Stop
      }
      if ($oldMoved) {
        Rename-Item -LiteralPath $rollback -NewName (Split-Path -Leaf $venv) -ErrorAction Stop
        $oldMoved = $false
      }
    } catch {
      # Never delete potentially recoverable environments when rollback is unclear.
      $mayDisposeStage = $false
      throw "Versionswechsel fehlgeschlagen ($activationError). Die bisherige Umgebung liegt moeglicherweise unter $rollback; bitte Diagnose pruefen: $($_.Exception.Message)"
    }
    throw "Aktualisierung nicht aktiviert ($activationError). Die bisherige Installation wurde nicht ersetzt."
  }
  Write-Host 'Installation/Aktualisierung abgeschlossen. Bestehende Daten und Chrome-Profil bleiben erhalten.'
  if ($hadOld) { Write-Host "Vorherige Programmversion fuer Rueckfall gesichert: $rollback" }
  Write-Host 'Zum Starten und Einrichten Kleinanzeiger.cmd verwenden.'
} finally {
  if ($mayDisposeStage -and (Test-Path -LiteralPath $stage -PathType Container)) {
    # Only the unique new staging directory can be discarded automatically.
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
  }
}