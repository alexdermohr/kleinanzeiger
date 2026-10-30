$ErrorActionPreference = 'Stop'
$base = $PSScriptRoot
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$python = Join-Path $state '.venv\Scripts\python.exe'
$db = Join-Path $state 'anzeigen.sqlite'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Zuerst Installieren.cmd ausfuehren.' }
. (Join-Path $base 'windows-chrome.ps1')
$chrome = Find-KleinanzeigerChrome -StateDirectory $state
if ($chrome) {
  try {
    $profile = Join-Path $state 'ChromeProfile'
    New-Item -ItemType Directory -Path $profile -Force | Out-Null
    $chromeArgs = '--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="' + $profile + '" https://www.kleinanzeigen.de/m-meine-anzeigen.html'
    Start-Process -FilePath $chrome -ArgumentList $chromeArgs
    Write-Host 'Im isolierten Chrome-Fenster anmelden; bei Verbindungsproblemen ist lokales Arbeiten moeglich.'
    Read-Host 'Nach dem Login oder fuer lokales Arbeiten ENTER druecken'
    Wait-KleinanzeigerChromeDevTools -Port 9222 -TimeoutSeconds 12
  } catch {
    Write-Warning 'Browserverbindung nicht bereit. Der lokale Arbeitsbereich wird geprueft.'
  }
} else {
  Write-Warning 'Chrome fehlt. Oeffne den lokalen Arbeitsbereich ohne Plattformaenderungen.'
}
$extra = @()
if (!(Test-Path -LiteralPath $db -PathType Leaf)) { $extra += '--init-db' }
$keyFile = Join-Path $state 'openai-key.dpapi'
if (Test-Path -LiteralPath $keyFile -PathType Leaf) {
  try {
    $encrypted = (Get-Content -LiteralPath $keyFile -Raw).Trim()
    $secret = ConvertTo-SecureString -String $encrypted
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
      $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
      if ([string]::IsNullOrWhiteSpace($key)) { throw 'API-Key ist leer.' }
      $env:OPENAI_API_KEY = $key
      $extra += '--enable-generation'
    } finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
      Remove-Variable key -ErrorAction SilentlyContinue
    }
  } catch {
    Write-Warning 'KI-Zugang nicht lesbar. Bitte KI-einrichten.cmd erneut ausfuehren.'
    Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
  }
}
try {
  & $python -m mark_api.launcher --db $db --cdp-port 9222 @extra
  if ($LASTEXITCODE -ne 0) { throw 'Produktstart fehlgeschlagen. Chrome/CDP/Login und KI-Konfiguration pruefen.' }
} finally {
  Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
}
