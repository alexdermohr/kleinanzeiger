param(
  [ValidateSet('Install','OnlineUpdate','Backup','Restore','Rollback','ImportMail','Classify','ImportProducts','ExportProducts')][string]$Action = 'Install',
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
  foreach ($name in @('Kleinanzeiger.cmd','windows-center.ps1','windows-worker.ps1','windows-offline-compat.py',
       'Kleinanzeiger-Handbuch.html','Kleinanzeiger-Funktionen.html',
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
  # Save-Backup runs against the ALREADY installed package before updating.
  # Use the stable internal compatibility module present in previous releases.
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
    $_.Extension -in @('.ps1','.cmd','.whl','.txt','.md','.html','.py')
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

function Invoke-ProgramRollback([string]$rollbackName) {
  Assert-KleinanzeigerStopped
  if ($rollbackName -cnotmatch '^\.venv-rollback-\d{8}-\d{6}-\d{3}-[0-9a-f]{32}$') {
    throw 'Bitte eine gespeicherte vorherige Programmversion auswaehlen.'
  }
  $current = Join-Path $state '.venv'
  $candidate = Join-Path $state $rollbackName
  if (!(Test-Path -LiteralPath $current -PathType Container) -or
      !(Test-Path -LiteralPath $candidate -PathType Container)) {
    throw 'Aktuelle oder gesicherte Programmversion nicht gefunden.'
  }
  foreach ($folder in @($current,$candidate)) {
    $entry = Get-Item -LiteralPath $folder -Force
    if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw 'Verknuepfte Programmverzeichnisse werden nicht aktiviert.'
    }
  }
  $oldVersion = Get-InstalledVersion
  $candidatePython = Join-Path $candidate 'Scripts\python.exe'
  if (!$oldVersion -or !(Test-Path -LiteralPath $candidatePython -PathType Leaf)) {
    throw 'Aktuelle oder vorherige Programmversion nicht startbar.'
  }
  try {
    $previous = @(& $candidatePython -c "import importlib.metadata as m; print(m.version('kleinanzeiger'))" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $previous.Count -ne 1 -or
        [string]$previous[0] -notmatch '^\d+\.\d+\.\d+$') {
      throw 'Version nicht validiert.'
    }
    # Rollback targets can predate the new Kleinanzeiger namespace.
    & $candidatePython -m mark_api.launcher --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Starttest fehlgeschlagen.' }
  } catch {
    throw 'Gesicherte Programmversion ist nicht einsetzbar. Aktuelle Version bleibt erhalten.'
  }
  # An older application must prove it can read the current SQLite schema
  # without upgrading, rewriting, or erasing any customer data.
  if (Test-Path -LiteralPath $db -PathType Leaf) {
    $env:KLEINANZEIGER_ROLLBACK_DB_CHECK = $db
    try {
      $checkCode = "import os,sqlite3; from urllib.parse import quote; from mark_api.storage import SnapshotStore; p=os.environ['KLEINANZEIGER_ROLLBACK_DB_CHECK']; c=sqlite3.connect('file:'+quote(p,safe='/')+'?mode=ro',uri=True); c.row_factory=sqlite3.Row; SnapshotStore._validate_store_schema(c); c.close()"
      & $candidatePython -c $checkCode 2>$null | Out-Null
      if ($LASTEXITCODE -ne 0) {
        throw 'Alte Programmversion kann die bestehende Datenbank nicht lesen.'
      }
    } finally {
      Remove-Item Env:KLEINANZEIGER_ROLLBACK_DB_CHECK -ErrorAction SilentlyContinue
    }
  }
  Assert-KleinanzeigerStopped
  $id = [guid]::NewGuid().ToString('N')
  $savedName = '.venv-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $id
  $savedPath = Join-Path $state $savedName
  $currentMoved = $false
  $candidateMoved = $false
  try {
    Rename-Item -LiteralPath $current -NewName $savedName -ErrorAction Stop
    $currentMoved = $true
    Rename-Item -LiteralPath $candidate -NewName '.venv' -ErrorAction Stop
    $candidateMoved = $true
    if ((Get-InstalledVersion) -ne [string]$previous[0]) {
      throw 'Aktivierte Programmversion weicht von der Auswahl ab.'
    }
    & $venvPython -m mark_api.launcher --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Aktivierte Programmversion ist nicht startbar.' }
  } catch {
    $cause = $_.Exception.Message
    try {
      if ($candidateMoved) {
        Rename-Item -LiteralPath $current -NewName ('.venv-rejected-' + $id) -ErrorAction Stop
      }
      if ($currentMoved) {
        Rename-Item -LiteralPath $savedPath -NewName '.venv' -ErrorAction Stop
      }
    } catch {
      throw "Rueckwechsel unklar ($cause). Programmordner erhalten, Diagnose pruefen."
    }
    throw "Rueckwechsel nicht aktiviert ($cause). Bisherige Programmversion wiederhergestellt."
  }
  Write-Output ("ROLLBACK_OK " + [string]$previous[0] + " (bisher: " + $oldVersion + ")")
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

function Read-UiPayload {
  # The controller sends UTF-8 JSON over a private ASCII/base64 stdin pipe,
  # never through command-line arguments, a temporary file, or a log.
  $line = [Console]::In.ReadLine()
  if (!$line -or $line.Length -gt 131072 -or $line -cnotmatch '^[A-Za-z0-9+/]+={0,2}$') {
    throw 'Bedienanfrage fehlt oder ist ungueltig.'
  }
  try {
    $bytes = [Convert]::FromBase64String($line)
    if ($bytes.Length -gt 65536) { throw 'too large' }
    $payload = ConvertFrom-Json -InputObject ([Text.Encoding]::UTF8.GetString($bytes))
    if ($null -eq $payload -or $payload -is [array]) { throw 'invalid object' }
    return $payload
  } catch {
    throw 'Die Bedienanfrage konnte nicht gelesen werden.'
  }
}

function Import-CustomerMail {
  Assert-KleinanzeigerStopped
  if (!(Test-Path -LiteralPath $db -PathType Leaf) -or
      !(Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw 'Kleinanzeiger muss vor dem E-Mail-Import eingerichtet werden.'
  }
  $payload = Read-UiPayload
  $paths = @($payload.paths)
  if ($paths.Count -lt 1 -or $paths.Count -gt 100) {
    throw 'Bitte 1 bis 100 EML-Dateien waehlen.'
  }
  $verified = @()
  [int64]$totalBytes = 0
  foreach ($raw in $paths) {
    if ($raw -isnot [string] -or [string]::IsNullOrWhiteSpace($raw) -or
        !$raw.EndsWith('.eml',[StringComparison]::OrdinalIgnoreCase) -or
        ![IO.Path]::IsPathRooted($raw) -or
        !(Test-Path -LiteralPath $raw -PathType Leaf)) {
      throw 'Nur vorhandene lokale EML-Dateien importieren.'
    }
    $file = Get-Item -LiteralPath $raw
    if ($file.Length -gt 5MB) { throw 'Eine E-Mail ist groesser als 5 MB.' }
    $totalBytes += $file.Length
    if ($totalBytes -gt 25MB) { throw 'E-Mail-Auswahl ist groesser als 25 MB.' }
    $verified += $file.FullName
  }
  Save-Backup
  & $venvPython -m mark_api.email_import_cli --db $db @verified
  if ($LASTEXITCODE -ne 0) { throw 'E-Mail-Import fehlgeschlagen; vorhandene Daten bleiben erhalten.' }
  Write-Output 'MAIL_IMPORT_OK'
}

function Update-CustomerLabels {
  Assert-KleinanzeigerStopped
  if (!(Test-Path -LiteralPath $db -PathType Leaf) -or
      !(Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw 'Kleinanzeiger muss vor der Vergleichszuordnung eingerichtet werden.'
  }
  $payload = Read-UiPayload
  # Legacy Windows PowerShell 5.1 strips embedded native argv double quotes.
  # Re-encode validated local JSON as ASCII and pipe it to the existing-data
  # classifier, preserving Unicode, quotes and backslashes exactly.
  $json = ConvertTo-Json -InputObject $payload -Depth 5 -Compress
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
  if ($encoded.Length -gt 8192) { throw 'Vergleichslabels sind zu lang.' }
  # Windows PowerShell 5.1 can recode native stdin and strip argv quotes.
  # This child-only environment value is ASCII/base64, and is erased
  # immediately after the command. No user labels enter command arguments.
  $env:KLEINANZEIGER_LABEL_PAYLOAD = $encoded
  try {
    & $venvPython -m mark_api.windows_customer_choices --db $db --apply-labels
    if ($LASTEXITCODE -ne 0) { throw 'Vergleichslabels konnten nicht gespeichert werden.' }
    Write-Output 'LABELS_OK'
  } finally {
    Remove-Item Env:KLEINANZEIGER_LABEL_PAYLOAD -ErrorAction SilentlyContinue
  }
}

function Invoke-PortfolioWorker([string]$mode) {
  Assert-KleinanzeigerStopped
  if (!(Test-Path -LiteralPath $db -PathType Leaf) -or
      !(Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw 'Kleinanzeiger muss vor dem Artikelimport eingerichtet sein.'
  }
  $payload = Read-UiPayload
  $file = [string]$payload.path
  if (!$file -or $file.Length -gt 1024 -or ![IO.Path]::IsPathRooted($file)) {
    throw 'Bitte eine lokale Datei auswaehlen.'
  }
  if ($mode -eq 'import') {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Importdatei nicht gefunden.' }
    if ([string]$payload.sha256 -notmatch '^[0-9a-f]{64}$') {
      throw 'Vorschaupruefwert fehlt.'
    }
    Save-Backup
  } else {
    if (Test-Path -LiteralPath $file) { throw 'Exportdatei darf noch nicht existieren.' }
  }
  $json = ConvertTo-Json -InputObject $payload -Compress -Depth 4
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
  if ($encoded.Length -gt 16384) { throw 'Importpfad ist zu lang.' }
  $env:KLEINANZEIGER_IMPORT_REQUEST = $encoded
  try {
    & $venvPython -m mark_api.portfolio_import_cli --db $db --mode $mode
    if ($LASTEXITCODE -ne 0) { throw 'Artikelaktion fehlgeschlagen; Details pruefen.' }
    Write-Output $(if($mode -eq 'import'){'PRODUCT_IMPORT_OK'}else{'PRODUCT_EXPORT_OK'})
  } finally {
    Remove-Item Env:KLEINANZEIGER_IMPORT_REQUEST -ErrorAction SilentlyContinue
  }
}

if ($SelfTest) {
  if (!(Get-Command 'Get-FileHash' -ErrorAction SilentlyContinue)) { throw 'SHA-256 nicht verfuegbar.' }
  $names = @(Get-ReleaseAssetNames '0.1.26')
  if ($names.Count -ne 3 -or $names[0] -ne 'Kleinanzeiger-v0.1.26-Windows.zip' -or
      $names[1] -ne 'Kleinanzeiger-v0.1.26-Windows.sha256' -or
      $names[2] -ne 'Kleinanzeiger-v0.1.26-Release-Manifest.json') {
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
  'ImportMail' { Import-CustomerMail }
  'Classify' { Update-CustomerLabels }
  'ImportProducts' { Invoke-PortfolioWorker 'import' }
  'ExportProducts' { Invoke-PortfolioWorker 'export' }
  'Rollback' { Invoke-ProgramRollback $BackupName }
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
      # Restore can run against an older installed venv; keep its stable module path.
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