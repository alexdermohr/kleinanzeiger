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
    if ($lines[0] -match '^([0-9a-fA-F]{64})  (kleinanzeiger-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl)$') {
      $expected = $Matches[1]
      $wheelName = $Matches[2]
    } else {
      return 'ungueltig'
    }
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

function Get-PortStatus {
  param([int[]]$ListenerPorts)
  if ($ListenerPorts -contains 8765) { return 'belegt' }
  return 'frei'
}

if ($SelfTest) {
  # No matching port is a healthy 'frei', not an API failure.
  if ((Get-PortStatus -ListenerPorts @()) -cne 'frei') { throw 'Portstatus-Selbsttest fehlgeschlagen' }
  if ((Get-PortStatus -ListenerPorts @(443, 8766)) -cne 'frei') { throw 'Portstatus-Selbsttest fehlgeschlagen' }
  if ((Get-PortStatus -ListenerPorts @(443, 8765)) -cne 'belegt') { throw 'Portstatus-Selbsttest fehlgeschlagen' }
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
  # The filtered Get-NetTCPConnection cmdlet throws on an empty match.
  # Enumerate read-only TCP listeners instead: an empty set means 'frei'.
  $listenerPorts = @(
    [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
      ForEach-Object { $_.Port }
  )
  $portStatus = Get-PortStatus -ListenerPorts $listenerPorts
} catch {
  # Genuine network enumeration errors remain unknown, never 'frei'.
  $portStatus = 'unbekannt'
}
Format-Diagnostic -PythonPresent $pythonPresent -DatabasePresent $dbPresent -PackageStatus $packageStatus -PortStatus $portStatus
