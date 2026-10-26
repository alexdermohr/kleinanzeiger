param(
  [ValidateSet('Install','OnlineUpdate','Backup','Restore')][string]$Action = 'Install',
  [string]$PackageDirectory = $PSScriptRoot,
  [string]$BackupName = '',
  [switch]$SelfTest,
  [string]$ExpectedInstalledVersion = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$appRoot = Join-Path $state 'App'
$db = Join-Path $state 'anzeigen.sqlite'
$venvPython = Join-Path $state '.venv\Scripts\python.exe'
$backupRoot = Join-Path $state 'Backups'

function Get-InstalledVersion {
  if (!(Test-Path -LiteralPath $venvPython -PathType Leaf)) { return $null }
  try {
    $result = & $venvPython -c "import importlib.metadata as m; print(m.version('kleinanzeiger'))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $result) { return ([string]$result).Trim() }
  } catch { }
  return $null
}

function Get-ReleaseAssetNames([string]$version) {
  $prefix = 'Kleinanzeiger-v' + $version + '-'
  return @(
    ($prefix + 'Windows.zip')
    ($prefix + 'Windows.sha256')
    ($prefix + 'Release-Manifest.json')
  )
}

function Assert-KleinanzeigerStopped {
  $active = @(Get-NetTCPConnection -LocalPort 8765,8766 -State Listen -ErrorAction SilentlyContinue)
  if ($active.Count -gt 0) {
    throw 'Kleinanzeiger laeuft noch. Vor Installation oder Wiederherstellung die bisherige Anwendung beenden.'
  }
}

function Get-VerifiedPackage([string]$folder) {
  $checksums = Join-Path $folder 'SHA256SUMS.txt'
  if (!(Test-Path -LiteralPath $checksums -PathType Leaf)) { throw 'SHA256SUMS.txt fehlt.' }
  $pattern = '^(?<digest>[0-9A-Fa-f]{64})\s+\*?(?<name>kleinanzeiger-(?<version>[0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl)$'
  $records = @(Get-Content -LiteralPath $checksums | Where-Object { $_ -match $pattern })
  if ($records.Count -ne 1) { throw 'Wheel-Pruefsumme ist nicht eindeutig.' }
  $matched = [regex]::Match([string]$records[0], $pattern)
  $name = $matched.Groups['name'].Value
  $wheel = Join-Path $folder $name
  if (!(Test-Path -LiteralPath $wheel -PathType Leaf)) { throw 'Wheel fehlt.' }
  $actual = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $matched.Groups['digest'].Value.ToLowerInvariant()) { throw 'Wheel-Pruefsumme ungueltig.' }
  foreach ($name in @('Kleinanzeiger.cmd','windows-center.ps1','windows-worker.ps1',
       'windows-install.ps1','windows-start.ps1','windows-chrome.ps1',
       'windows-ki-einrichten.ps1','windows-backup.ps1','windows-restore.ps1')) {
    if (!(Test-Path -LiteralPath (Join-Path $folder $name) -PathType Leaf)) {
      throw "Unvollstaendiges Windows-Paket: $name"
    }
  }
  return @{ version = $matched.Groups['version'].Value; wheel = $wheel; hash = $actual }
}

function Save-Backup {
  if (!(Test-Path -LiteralPath $db -PathType Leaf)) { throw 'Noch keine Datenbank vorhanden.' }
  if (!(Test-Path -LiteralPath $venvPython -PathType Leaf)) { throw 'Bestehendes Python fehlt; die Datenbank bleibt unveraendert.' }
  New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
  $name = 'Kleinanzeiger-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.sqlite'
  $dest = Join-Path $backupRoot $name
  & $venvPython -m mark_api.backup_cli --db $db --backup $dest
  if ($LASTEXITCODE -ne 0) { throw 'Sicherung fehlgeschlagen. Update abgebrochen.' }
  if (!(Test-Path -LiteralPath $dest -PathType Leaf) -or
      !(Test-Path -LiteralPath ($dest + '.manifest.json') -PathType Leaf)) {
    throw 'Sicherungsmanifest fehlt. Update abgebrochen.'
  }
  Write-Output "BACKUP_OK $name"
}

function Publish-AppFiles([string]$folder) {
  New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
  $files = @(Get-ChildItem -LiteralPath $folder -File | Where-Object {
    $_.Extension -in @('.ps1','.cmd','.whl','.txt','.md')
  })
  foreach ($file in $files) {
    if ([IO.Path]::GetFullPath($file.DirectoryName).TrimEnd('\') -ieq
        [IO.Path]::GetFullPath($appRoot).TrimEnd('\')) { continue }
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $appRoot $file.Name) -Force
  }
  # A stable, installed shortcut works even after the downloaded ZIP is deleted.
  try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    if ($desktop) {
      $shortcutPath = Join-Path $desktop 'Kleinanzeiger.lnk'
      $shell = New-Object -ComObject WScript.Shell
      $shortcut = $shell.CreateShortcut($shortcutPath)
      $target = Join-Path $appRoot 'Kleinanzeiger.cmd'
      if (!(Test-Path -LiteralPath $shortcutPath) -or
          [string]::Equals([string]$shortcut.TargetPath,$target,[StringComparison]::OrdinalIgnoreCase)) {
        $shortcut.TargetPath = $target
        $shortcut.WorkingDirectory = $appRoot
        $shortcut.Description = 'Kleinanzeiger starten und verwalten'
        $shortcut.Save()
      }
    }
  } catch {
    Write-Output 'Hinweis: Desktop-Verknuepfung konnte nicht erstellt werden.'
  }
}

function Install-Package([string]$folder) {
  $package = Get-VerifiedPackage $folder
  Assert-KleinanzeigerStopped
  if (Test-Path -LiteralPath $db -PathType Leaf) {
    Write-Output 'Sichere vorhandene Anzeigen vor dem Update ...'
    Save-Backup
  }
  Write-Output ('Installiere Kleinanzeiger ' + $package.version + ' ...')
  & (Join-Path $folder 'windows-install.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen. Daten unveraendert.' }
  $installed = Get-InstalledVersion
  if ($installed -ne $package.version) {
    throw "Versionspruefung fehlgeschlagen: erwartet $($package.version), installiert $installed."
  }
  Publish-AppFiles $folder
  Write-Output ('INSTALL_OK ' + $installed)
}

function Download-VerifiedRelease {
  $api = 'https://api.github.com/repos/alexdermohr/kleinanzeiger/releases/latest'
  $release = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent'='Kleinanzeiger-Windows-Setup' }
  if ($release.draft -or $release.prerelease -or
      [string]$release.tag_name -notmatch '^v(?<version>[0-9]+\.[0-9]+\.[0-9]+)$') {
    throw 'Keine gueltige stabile Version gefunden.'
  }
  $version = $Matches['version']
  $current = Get-InstalledVersion
  if ($current -and [version]$version -le [version]$current) {
    return $null
  }
  $names = @(Get-ReleaseAssetNames $version)
  $downloadRoot = 'https://github.com/alexdermohr/kleinanzeiger/releases/download/v' + $version + '/'
  $dir = Join-Path $env:TEMP ('kleinanzeiger-update-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $dir -Force | Out-Null
  try {
  foreach ($name in $names) {
    $assets = @($release.assets | Where-Object {
      $_.name -ceq $name -and $_.browser_download_url -ceq ($downloadRoot + $name)
    })
    if ($assets.Count -ne 1) { throw "Release-Datei $name fehlt." }
    Invoke-WebRequest -Uri ($downloadRoot + $name) -OutFile (Join-Path $dir $name) -UseBasicParsing
  }
  $zip = Join-Path $dir $names[0]
  $digest = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
  $sum = (Get-Content -LiteralPath (Join-Path $dir $names[1]) -Raw).Trim()
  $manifest = Get-Content -LiteralPath (Join-Path $dir $names[2]) -Raw | ConvertFrom-Json
  if ($sum -cne ($digest + '  ' + $names[0]) -or
      [string]$manifest.version -ne $version -or
      [string]$manifest.tag -ne ('v' + $version) -or
      [string]$manifest.windows_zip.sha256 -ne $digest -or
      [int64]$manifest.windows_zip.bytes -ne (Get-Item -LiteralPath $zip).Length -or
      [string]$manifest.source_commit -ne [string]$release.target_commitish) {
    throw 'Release-Manifest oder SHA-256-Pruefung fehlgeschlagen.'
  }
  $folder = Join-Path $dir 'files'
  Expand-Archive -LiteralPath $zip -DestinationPath $folder
  $package = Get-VerifiedPackage $folder
  if ($package.version -ne $version -or
      [string]$manifest.wheel.sha256 -ne $package.hash) {
    throw 'Paket und Release-Manifest stimmen nicht ueberein.'
  }
  return @{folder=$folder;temp=$dir}
  } catch {
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
    throw
  }
}

if ($SelfTest) {
  if (!(Get-Command 'Get-FileHash' -ErrorAction SilentlyContinue)) { throw 'SHA-256 nicht verfuegbar.' }
  $names = @(Get-ReleaseAssetNames '0.1.4')
  if ($names.Count -ne 3 -or $names[0] -ne 'Kleinanzeiger-v0.1.4-Windows.zip' -or
      $names[1] -ne 'Kleinanzeiger-v0.1.4-Windows.sha256' -or
      $names[2] -ne 'Kleinanzeiger-v0.1.4-Release-Manifest.json') {
    throw 'Release-Dateinamen sind nicht eindeutig.'
  }
  if ($ExpectedInstalledVersion -and (Get-InstalledVersion) -ne $ExpectedInstalledVersion) {
    throw 'Die installierte Version wurde vom Update-Worker nicht erkannt.'
  }
  Write-Output 'WINDOWS_WORKER_SELFTEST_OK'
  return
}
switch ($Action) {
  'Backup' { Save-Backup }
  'Restore' {
    Assert-KleinanzeigerStopped
    foreach ($suffix in @('','-wal','-shm','-journal')) {
      if (Test-Path -LiteralPath ($db + $suffix)) {
        throw 'Wiederherstellung nur bei nicht vorhandener Datenbank erlaubt.'
      }
    }
    if (!$BackupName -or $BackupName -ne [IO.Path]::GetFileName($BackupName) -or
        $BackupName -notmatch '^Kleinanzeiger-\d{8}-\d{6}-\d{3}\.sqlite$') {
      throw 'Ungueltiger Backup-Dateiname.'
    }
    $source = Join-Path $backupRoot $BackupName
    if (!(Test-Path -LiteralPath $source -PathType Leaf) -or
        !(Test-Path -LiteralPath ($source + '.manifest.json') -PathType Leaf)) {
      throw 'Sicherung oder Manifest fehlt.'
    }
    & $venvPython -m mark_api.windows_backup --backup $source --db $db
    if ($LASTEXITCODE -ne 0) { throw 'Wiederherstellung abgewiesen.' }
    Write-Output 'RESTORE_OK'
  }
  'Install' { Install-Package $PackageDirectory }
  'OnlineUpdate' {
    Assert-KleinanzeigerStopped
    $download = $null
    try {
      Write-Output 'Pruefe offizielle GitHub-Version ...'
      $download = Download-VerifiedRelease
      if ($null -eq $download) {
        Write-Output 'ALREADY_CURRENT'
      } else {
        Install-Package $download.folder
      }
    } finally {
      if ($null -ne $download -and (Test-Path -LiteralPath $download.temp)) {
        Remove-Item -LiteralPath $download.temp -Recurse -Force -ErrorAction SilentlyContinue
      }
    }
  }
}