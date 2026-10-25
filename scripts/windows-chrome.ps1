# Shared Chrome detection and the official, user-local Chrome-for-Testing fallback.
function Find-KleinanzeigerChrome([string]$StateDirectory) {
  $candidates = @()
  foreach ($home in @($env:ProgramFiles, [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'), $env:LOCALAPPDATA)) {
    if ($home) { $candidates += Join-Path $home 'Google\Chrome\Application\chrome.exe' }
  }
  foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
                     'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe')) {
    try {
      $registry = Get-Item -LiteralPath $key -ErrorAction Stop
      $path = $registry.GetValue('')
      if ($path) { $candidates += [string]$path }
    } catch { }
  }
  $command = Get-Command 'chrome.exe' -ErrorAction SilentlyContinue
  if ($command) { $candidates += $command.Source }
  $candidates += Join-Path $StateDirectory 'Browser\chrome-win64\chrome.exe'
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      return $candidate
    }
  }
  return $null
}

function Install-KleinanzeigerChrome([string]$StateDirectory) {
  $directory = Join-Path $StateDirectory 'Browser'
  $exe = Join-Path $directory 'chrome-win64\chrome.exe'
  if (Test-Path -LiteralPath $exe -PathType Leaf) { return }
  $work = Join-Path $env:TEMP ('kleinanzeiger-chrome-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $work -Force | Out-Null
  try {
    $manifest = Invoke-RestMethod -Uri 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json'
    $matches = @($manifest.channels.Stable.downloads.chrome | Where-Object { $_.platform -eq 'win64' })
    if ($matches.Count -ne 1) { throw 'Google-Chrome-Download ist nicht eindeutig.' }
    $url = [string]$matches[0].url
    if ($url -notmatch '^https://storage\.googleapis\.com/chrome-for-testing-public/[0-9.]+/win64/chrome-win64\.zip$') {
      throw 'Unerwarteter Google-Chrome-Downloadlink.'
    }
    $zip = Join-Path $work 'chrome-win64.zip'
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $extracted = Join-Path $work 'extracted'
    Expand-Archive -LiteralPath $zip -DestinationPath $extracted
    $sourceExe = Join-Path $extracted 'chrome-win64\chrome.exe'
    if (!(Test-Path -LiteralPath $sourceExe -PathType Leaf)) { throw 'Chrome-Archiv ist unvollstaendig.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $sourceExe
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Google') {
      throw 'Google-Chrome-Binaerdatei besitzt keine gueltige Google-Signatur.'
    }
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if (Test-Path -LiteralPath (Join-Path $directory 'chrome-win64')) {
      throw 'Vorhandene Chrome-Laufzeit ist unvollstaendig. Bitte Zustand pruefen.'
    }
    Move-Item -LiteralPath (Join-Path $extracted 'chrome-win64') -Destination $directory
  } finally {
    Remove-Item -LiteralPath $work -Force -Recurse -ErrorAction SilentlyContinue
  }
}
