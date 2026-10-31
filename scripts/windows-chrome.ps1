# Shared Chrome detection and the official, user-local Chrome-for-Testing fallback.
function Find-KleinanzeigerChrome([string]$StateDirectory) {
  $candidates = @()
  foreach ($candidateRoot in @($env:ProgramFiles, [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'), $env:LOCALAPPDATA)) {
    if ($candidateRoot) { $candidates += Join-Path $candidateRoot 'Google\Chrome\Application\chrome.exe' }
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

# A visible Chrome window is not evidence that its DevTools endpoint is ready.
# Probe only our fixed loopback endpoint, never a public web service or user profile.
function Test-KleinanzeigerChromeDevTools([int]$Port = 9222) {
  if ($Port -lt 1 -or $Port -gt 65535) { return $false }
  $response = $null
  $reader = $null
  try {
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/json/version")
    $request.Proxy = $null
    $request.Timeout = 800
    $request.ReadWriteTimeout = 800
    $response = $request.GetResponse()
    if ([int]$response.StatusCode -ne 200) { return $false }
    $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
    $metadata = ConvertFrom-Json -InputObject $reader.ReadToEnd()
    $browser = [string]$metadata.Browser
    $socket = [string]$metadata.webSocketDebuggerUrl
    return ($browser -match '^(Chrome|HeadlessChrome)/' -and
            $socket -match "^ws://(127\.0\.0\.1|localhost):$Port/")
  } catch {
    return $false
  } finally {
    if ($reader) { $reader.Dispose() }
    if ($response) { $response.Close() }
  }
}

# A Chrome /json/version response alone cannot establish which account owns
# port 9222: another browser may already be listening there. Bind the actual
# loopback TCP listener PID to our executable, profile and debugging arguments.
# A failed ownership probe means local-only mode, never another account's CDP.
function Test-KleinanzeigerChromeOwnedDevTools(
    [int]$Port = 9222, [string]$ProfileDirectory,
    [string]$ChromeExecutable) {
  if ($Port -lt 1 -or $Port -gt 65535 -or
      [string]::IsNullOrWhiteSpace($ProfileDirectory) -or
      [string]::IsNullOrWhiteSpace($ChromeExecutable)) { return $false }
  if (!(Test-KleinanzeigerChromeDevTools -Port $Port)) { return $false }
  try {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalAddress -eq '127.0.0.1' })
    if ($listeners.Count -ne 1) { return $false }
    $listenerPid = [int]$listeners[0].OwningProcess
    if ($listenerPid -le 0) { return $false }
    $processes = @(Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction Stop)
    if ($processes.Count -ne 1) { return $false }
    $browser = $processes[0]
    if ([int]$browser.ProcessId -ne $listenerPid -or
        [string]$browser.Name -ine 'chrome.exe') { return $false }
    $expectedExe = [IO.Path]::GetFullPath($ChromeExecutable)
    $observedExe = [IO.Path]::GetFullPath([string]$browser.ExecutablePath)
    if (![string]::Equals($expectedExe, $observedExe, [StringComparison]::OrdinalIgnoreCase)) {
      return $false
    }
    $expectedProfile = [IO.Path]::GetFullPath($ProfileDirectory).TrimEnd([char]92)
    $profileLiteral = [regex]::Escape($expectedProfile)
    $profileArg = '(?i)(?:^|\s)--user-data-dir=(?:"' + $profileLiteral + '"|' + $profileLiteral + ')(?=\s|$)'
    $portArg = '(?i)(?:^|\s)--remote-debugging-port=' + $Port + '(?=\s|$)'
    $commandLine = [string]$browser.CommandLine
    if ([regex]::Matches($commandLine, '(?i)(?:^|\s)--user-data-dir=').Count -ne 1 -or
        [regex]::Matches($commandLine, '(?i)(?:^|\s)--remote-debugging-port=').Count -ne 1) {
      return $false
    }
    return ([regex]::IsMatch($commandLine, $profileArg) -and
            [regex]::IsMatch($commandLine, $portArg))
  } catch {
    return $false
  }
}

function Wait-KleinanzeigerChromeDevTools(
    [int]$Port = 9222, [int]$TimeoutSeconds = 12,
    [scriptblock]$Probe = $null) {
  if ($Port -lt 1 -or $Port -gt 65535 -or $TimeoutSeconds -lt 0 -or $TimeoutSeconds -gt 30) {
    throw 'Ungueltige Chrome-Startpruefung.'
  }
  if ($null -eq $Probe) {
    $Probe = { param([int]$checkedPort) Test-KleinanzeigerChromeDevTools -Port $checkedPort }
  }
  $watch = [System.Diagnostics.Stopwatch]::StartNew()
  do {
    if (& $Probe $Port) { return }
    if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) { break }
    Start-Sleep -Milliseconds 250
  } while ($true)
  throw 'Chrome ist offen, aber die Browsersteuerung (Port 9222) ist nicht erreichbar. Bitte nur die von Kleinanzeiger gestarteten Chrome-Fenster vollstaendig schliessen und erneut starten; das normale Chrome-Profil nicht loeschen.'
}
