param([switch]$SelfTest)
$ErrorActionPreference = 'Stop'

# Read-only customer triage: no account, browser, database or provider access.
# Never echo a path, command error, identifier, token or file content.
function Format-Diagnostic {
  param(
    [bool]$PythonPresent,
    [bool]$DatabasePresent,
    [string]$PackageStatus,
    [string]$PortStatus
  )
  $pythonState = if ($PythonPresent) { 'vorhanden' } else { 'fehlt' }
  $databaseState = if ($DatabasePresent) { 'vorhanden' } else { 'fehlt' }
  return @(
    'Kleinanzeiger-Diagnose (nur lesend)',
    "Installations-Python: $pythonState",
    "Lokale Datenbankdatei: $databaseState",
    "Paketpruefsumme: $PackageStatus",
    "Dashboard-Port 8765: $PortStatus (keine Funktionsgarantie)",
    'Keine Konto- oder Plattformpruefung durchgefuehrt.'
  )
}

function Get-PackageStatus {
  param([string]$PackageDirectory)
  try {
    $sumsFile = Join-Path $PackageDirectory 'SHA256SUMS.txt'
    if (-not (Test-Path -LiteralPath $sumsFile -PathType Leaf)) {
      return 'nicht vorhanden'
    }
    $lines = @(Get-Content -LiteralPath $sumsFile -ErrorAction Stop)
    if ($lines.Count -ne 1) { return 'ungueltig' }
    if ($lines[0] -match '^([0-9a-fA-F]{64})  (kleinanzeiger-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl)
    $wheel = Join-Path $PackageDirectory $wheelName
    if (-not (Test-Path -LiteralPath $wheel -PathType Leaf)) {
      return 'Paket fehlt'
    }
    $actual = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256 -ErrorAction Stop).Hash
    if ($actual -ieq $expected) { return 'ok' }
    return 'ungueltig'
  } catch {
    return 'unbekannt'
  }
}

if ($SelfTest) {
  $actual = @(Format-Diagnostic -PythonPresent $true -DatabasePresent $false -PackageStatus 'ok' -PortStatus 'frei')
  $expected = @(
    'Kleinanzeiger-Diagnose (nur lesend)',
    'Installations-Python: vorhanden',
    'Lokale Datenbankdatei: fehlt',
    'Paketpruefsumme: ok',
    'Dashboard-Port 8765: frei (keine Funktionsgarantie)',
    'Keine Konto- oder Plattformpruefung durchgefuehrt.'
  )
  if ($actual.Count -ne $expected.Count) { throw 'Diagnose-Selbsttest fehlgeschlagen' }
  for ($i = 0; $i -lt $expected.Count; $i++) {
    if ($actual[$i] -cne $expected[$i]) { throw 'Diagnose-Selbsttest fehlgeschlagen' }
  }
  Write-Output 'WINDOWS_DIAGNOSTICS_SELFTEST_PASS'
  exit 0
}

$pythonPresent = $false
$dbPresent = $false
if ($env:LOCALAPPDATA) {
  try {
    $state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
    $pythonPresent = Test-Path -LiteralPath (Join-Path $state '.venv\Scripts\python.exe') -PathType Leaf
    $dbPresent = Test-Path -LiteralPath (Join-Path $state 'anzeigen.sqlite') -PathType Leaf
  } catch {
    # Unknown local paths must not produce a data-bearing exception on screen.
  }
}
$packageStatus = Get-PackageStatus -PackageDirectory $PSScriptRoot
$portStatus = 'unbekannt'
try {
  $listener = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop)
  $portStatus = if ($listener.Count -gt 0) { 'belegt' } else { 'frei' }
} catch {
  # No listener permission, unavailable cmdlet or no matching listener.
  $portStatus = 'unbekannt'
}
Format-Diagnostic -PythonPresent $pythonPresent -DatabasePresent $dbPresent -PackageStatus $packageStatus -PortStatus $portStatus) {
      $expected = $Matches[1]
      $wheelName = $Matches[2]
    } else {
      return 'ungueltig'
    }
    $wheel = Join-Path $PackageDirectory $Matches[2]
    if (-not (Test-Path -LiteralPath $wheel -PathType Leaf)) {
      return 'Paket fehlt'
    }
    $actual = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256 -ErrorAction Stop).Hash
    if ($actual -ieq $expected) { return 'ok' }
    return 'ungueltig'
  } catch {
    return 'unbekannt'
  }
}

if ($SelfTest) {
  $actual = @(Format-Diagnostic -PythonPresent $true -DatabasePresent $false -PackageStatus 'ok' -PortStatus 'frei')
  $expected = @(
    'Kleinanzeiger-Diagnose (nur lesend)',
    'Installations-Python: vorhanden',
    'Lokale Datenbankdatei: fehlt',
    'Paketpruefsumme: ok',
    'Dashboard-Port 8765: frei (keine Funktionsgarantie)',
    'Keine Konto- oder Plattformpruefung durchgefuehrt.'
  )
  if ($actual.Count -ne $expected.Count) { throw 'Diagnose-Selbsttest fehlgeschlagen' }
  for ($i = 0; $i -lt $expected.Count; $i++) {
    if ($actual[$i] -cne $expected[$i]) { throw 'Diagnose-Selbsttest fehlgeschlagen' }
  }
  Write-Output 'WINDOWS_DIAGNOSTICS_SELFTEST_PASS'
  exit 0
}

$pythonPresent = $false
$dbPresent = $false
if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
  try {
    $state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
    $pythonPresent = Test-Path -LiteralPath (Join-Path $state '.venv\Scripts\python.exe') -PathType Leaf
    $dbPresent = Test-Path -LiteralPath (Join-Path $state 'anzeigen.sqlite') -PathType Leaf
  } catch {
    # Unknown local paths must not produce a data-bearing exception on screen.
  }
}
$packageStatus = Get-PackageStatus -PackageDirectory $PSScriptRoot
$portStatus = 'unbekannt'
try {
  $listener = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop)
  $portStatus = if ($listener.Count -gt 0) { 'belegt' } else { 'frei' }
} catch {
  # No listener permission, unavailable cmdlet or no matching listener.
  $portStatus = 'unbekannt'
}
Format-Diagnostic -PythonPresent $pythonPresent -DatabasePresent $dbPresent -PackageStatus $packageStatus -PortStatus $portStatus