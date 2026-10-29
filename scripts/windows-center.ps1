param([switch]$SelfTest, [string]$ExpectedInstalledVersion = '')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
# A single user-session window owns installation, upgrades and the product child.
# Multiple hubs would otherwise be able to update the same SQLite/Python files.
$script:singleInstanceMutex = $null
if (!$SelfTest) {
  $newInstance = $false
  $script:singleInstanceMutex = [System.Threading.Mutex]::new(
    $true, 'Local\Kleinanzeiger-Control-Center', [ref]$newInstance)
  if (!$newInstance) {
    $script:singleInstanceMutex.Dispose()
    $script:singleInstanceMutex = $null
    [System.Windows.Forms.MessageBox]::Show(
      'Kleinanzeiger ist bereits geöffnet. Bitte das vorhandene Fenster verwenden.',
      'Kleinanzeiger') | Out-Null
    return
  }
}
$base = $PSScriptRoot
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$db = Join-Path $state 'anzeigen.sqlite'
$python = Join-Path $state '.venv\Scripts\python.exe'
$profile = Join-Path $state 'ChromeProfile'
$keyFile = Join-Path $state 'openai-key.dpapi'
$worker = Join-Path $base 'windows-worker.ps1'
. (Join-Path $base 'windows-chrome.ps1')

function Get-PackageVersion {
  $sums = Join-Path $base 'SHA256SUMS.txt'
  if (!(Test-Path -LiteralPath $sums)) { return $null }
  foreach ($line in (Get-Content -LiteralPath $sums)) {
    if ($line -match 'kleinanzeiger-(?<version>\d+\.\d+\.\d+)-py3-none-any\.whl$') {
      return $Matches['version']
    }
  }
  return $null
}
function Get-InstalledVersion {
  if (!(Test-Path -LiteralPath $python -PathType Leaf)) { return $null }
  try {
    $result = & $python -c "import importlib.metadata as m; print(m.version('kleinanzeiger'))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $result) { return ([string]$result).Trim() }
  } catch { }
  return $null
}
function Test-ActiveProduct {
  return @(
    Get-NetTCPConnection -LocalPort 8765,8766 -State Listen -ErrorAction SilentlyContinue
  ).Count -gt 0
}
$script:mode = ''
$script:process = $null
$script:outTask = $null
$script:errTask = $null
$script:openedDashboard = $false
$script:nextLaunch = $false
$script:stopRequested = $false
$script:closeAfterStop = $false
$script:details = ''
$script:packageVersion = Get-PackageVersion
$ink = [Drawing.Color]::FromArgb(28,46,58)
$muted = [Drawing.Color]::FromArgb(93,113,124)
$accent = [Drawing.Color]::FromArgb(17,103,122)
$paper = [Drawing.Color]::FromArgb(245,248,250)
$font = [System.Drawing.Font]::new('Segoe UI',10)
$fontBold = [System.Drawing.Font]::new('Segoe UI',10,[Drawing.FontStyle]::Bold)
$form = [System.Windows.Forms.Form]::new()
$form.Text = 'Kleinanzeiger'
$form.ClientSize = [System.Drawing.Size]::new(640,510)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = $paper
$form.Font = $font
$form.AutoScaleMode = 'Dpi'

function Add-Label([string]$value,[int]$left,[int]$top,[int]$width,[int]$height,
                   [System.Drawing.Font]$useFont=$font,[System.Drawing.Color]$color=$ink) {
  $label = [System.Windows.Forms.Label]::new()
  $label.Text = $value
  $label.Location = [System.Drawing.Point]::new($left,$top)
  $label.Size = [System.Drawing.Size]::new($width,$height)
  $label.ForeColor = $color
  $label.Font = $useFont
  $form.Controls.Add($label)
  return $label
}
function Add-Button([string]$value,[int]$left,[int]$top,[int]$width,[int]$height,
                    [bool]$primary=$false) {
  $button = [System.Windows.Forms.Button]::new()
  $button.Text = $value
  $button.Location = [System.Drawing.Point]::new($left,$top)
  $button.Size = [System.Drawing.Size]::new($width,$height)
  $button.FlatStyle = 'Flat'
  $button.FlatAppearance.BorderSize = $(if($primary){0}else{1})
  $button.BackColor = $(if($primary){$accent}else{[Drawing.Color]::White})
  $button.ForeColor = $(if($primary){[Drawing.Color]::White}else{$ink})
  $button.Font = $fontBold
  $form.Controls.Add($button)
  return $button
}
$headingFont = [System.Drawing.Font]::new('Segoe UI',22,[Drawing.FontStyle]::Bold)
$title = Add-Label 'Kleinanzeiger' 26 20 420 43 $headingFont
$intro = Add-Label 'Deine Anzeigen verwalten – alles an einem Ort.' 28 66 585 28 $font $muted
$versionLabel = Add-Label 'Prüfe Installation …' 28 105 575 26 $font $muted
# The primary workflow fits on one compact page; secondary maintenance
# remains in this window but is hidden until deliberately requested.
$primary = Add-Button 'Kleinanzeiger starten' 28 145 584 55 $true
$update = Add-Button 'Installieren / aktualisieren' 28 216 282 43
$account = Add-Button 'Kleinanzeigen-Konto öffnen' 330 216 282 43
$products = Add-Button 'Artikel importieren' 28 273 282 43
$backup = Add-Button 'Sicherung erstellen' 330 273 282 43
$ai = Add-Button 'KI einrichten' 28 330 282 43
$advanced = Add-Button 'Weitere Aktionen' 330 330 282 43
$mail = Add-Button 'E-Mails importieren' 28 390 282 43
$labels = Add-Button 'Anzeigen vergleichen' 330 390 282 43
$productsExport = Add-Button 'Entwürfe exportieren' 28 449 282 43
$restore = Add-Button 'Sicherung wiederherstellen …' 330 449 282 43
$rollback = Add-Button 'Vorherige Programmversion verwenden …' 28 508 584 43
$statusLabel = Add-Label 'Bereit.' 28 397 584 44 $font $muted
$detailsLink = [System.Windows.Forms.LinkLabel]::new()
$detailsLink.Text = 'Fehlerdetails anzeigen'
$detailsLink.SetBounds(28,453,240,26)
$detailsLink.LinkColor = $accent
$detailsLink.Visible = $false
$form.Controls.Add($detailsLink)
$detailsLink.Add_Click({
  $shown = $(if ($script:details) { $script:details } else { $statusLabel.Text })
  [Windows.Forms.MessageBox]::Show($shown,'Kleinanzeiger – technische Details') | Out-Null
})
$script:advancedExpanded = $false
function Set-AdvancedLayout([bool]$expanded) {
  $script:advancedExpanded = $expanded
  foreach ($control in @($mail,$labels,$productsExport,$restore,$rollback)) {
    $control.Visible = $expanded
  }
  $advanced.Text = $(if($expanded){'Weniger anzeigen'}else{'Weitere Aktionen'})
  $statusLabel.SetBounds(28,$(if($expanded){567}else{397}),584,44)
  $detailsLink.SetBounds(28,$(if($expanded){626}else{453}),240,26)
  $form.ClientSize = [System.Drawing.Size]::new(640,$(if($expanded){682}else{510}))
}
Set-AdvancedLayout $false
$help = [System.Windows.Forms.LinkLabel]::new()
$help.Text = 'Handbuch'
$help.SetBounds(451,37,87,24)
$help.LinkColor = $accent
$form.Controls.Add($help)
$features = [System.Windows.Forms.LinkLabel]::new()
$features.Text = 'Funktionen'
$features.SetBounds(544,37,93,24)
$features.LinkColor = $accent
$form.Controls.Add($features)

function Set-Status([string]$message,[bool]$failure=$false) {
  $statusLabel.Text = $message
  $statusLabel.ForeColor = $(if($failure){[Drawing.Color]::FromArgb(167,51,67)}else{$muted})
  # A permanent technical link is distracting while everything is healthy.
  # On failure it is always visible below the status, never obscured by controls.
  $detailsLink.Visible = $failure
}
function Refresh-Controls {
  $busy = $script:process -and !$script:process.HasExited
  $product = $script:mode -eq 'product' -and $busy
  $installed = Get-InstalledVersion
  if ($installed) {
    $versionLabel.Text = "Installiert: Version $installed"
    if ($script:packageVersion -and [version]$installed -lt [version]$script:packageVersion) {
      $versionLabel.Text += "   |   Bereit: $($script:packageVersion)"
    }
  } else {
    $versionLabel.Text = 'Noch nicht eingerichtet – ein Klick genügt.'
  }
  # A user-selected older program version must start as selected. Updating is a separate button.
  $needsInstall = !$installed
  $primary.Text = $(if($product){'Kleinanzeiger beenden'}elseif($needsInstall){'Einrichten und starten'}else{'Kleinanzeiger starten'})
  $primary.Enabled = !$busy -or $product
  $update.Enabled = !$busy
  $advanced.Enabled = !$busy
  $backup.Enabled = !$busy -and (Test-Path -LiteralPath $db)
  $ai.Enabled = !$busy
  $mail.Enabled = !$busy -and (Test-Path -LiteralPath $db)
  $labels.Enabled = !$busy -and (Test-Path -LiteralPath $db)
  $products.Enabled = !$busy -and (Test-Path -LiteralPath $db)
  $productsExport.Enabled = !$busy -and (Test-Path -LiteralPath $db)
  $account.Enabled = !$busy
  $restore.Enabled = !$busy -and !(Test-Path -LiteralPath $db)
  $rollback.Enabled = !$busy -and (Test-Path -LiteralPath $python -PathType Leaf) -and
    (@(Get-ChildItem -LiteralPath $state -Directory -Filter '.venv-rollback-*' -ErrorAction SilentlyContinue).Count -gt 0)
}

function Show-KiDialog {
  $dialog = [System.Windows.Forms.Form]::new()
  $dialog.Text = 'KI einrichten'
  $dialog.ClientSize = [System.Drawing.Size]::new(490,225)
  $dialog.FormBorderStyle = 'FixedDialog'
  $dialog.MaximizeBox = $false
  $dialog.MinimizeBox = $false
  $dialog.StartPosition = 'CenterParent'
  $dialog.Font = $font
  $note = [System.Windows.Forms.Label]::new()
  $note.Text = 'Deinen eigenen OpenAI-API-Schlüssel eingeben. Er bleibt verschlüsselt auf diesem Windows-Konto. KI-Anfragen kosten ggf. Geld und werden immer einzeln bestätigt.'
  $note.SetBounds(20,18,450,68)
  $dialog.Controls.Add($note)
  $entry = [System.Windows.Forms.TextBox]::new()
  $entry.SetBounds(20,96,450,29)
  $entry.UseSystemPasswordChar = $true
  $dialog.Controls.Add($entry)
  $save = [System.Windows.Forms.Button]::new()
  $save.Text = 'Schlüssel speichern'
  $save.SetBounds(20,155,190,39)
  $dialog.Controls.Add($save)
  $remove = [System.Windows.Forms.Button]::new()
  $remove.Text = 'Schlüssel entfernen'
  $remove.SetBounds(226,155,244,39)
  $remove.Enabled = Test-Path -LiteralPath $keyFile
  $dialog.Controls.Add($remove)
  $save.Add_Click({
    if ($entry.Text.Length -lt 20) {
      [Windows.Forms.MessageBox]::Show('Bitte einen gültigen API-Schlüssel eingeben.','Kleinanzeiger') | Out-Null
      return
    }
    $secure = ConvertTo-SecureString $entry.Text -AsPlainText -Force
    $entry.Clear()
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $temporary = $keyFile + '.new'
    try {
      ConvertFrom-SecureString $secure | Set-Content -LiteralPath $temporary -Encoding Ascii -NoNewline
      Move-Item -LiteralPath $temporary -Destination $keyFile -Force
      $dialog.DialogResult = [Windows.Forms.DialogResult]::OK
      $dialog.Close()
    } catch {
      [Windows.Forms.MessageBox]::Show('Der Schlüssel konnte nicht gespeichert werden.','Kleinanzeiger') | Out-Null
    } finally {
      Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
      $secure.Dispose()
    }
  })
  $remove.Add_Click({
    if ([Windows.Forms.MessageBox]::Show('Gespeicherten KI-Zugang entfernen?','Kleinanzeiger',
        [Windows.Forms.MessageBoxButtons]::YesNo) -eq [Windows.Forms.DialogResult]::Yes) {
      Remove-Item -LiteralPath $keyFile -Force
      $dialog.DialogResult = [Windows.Forms.DialogResult]::OK
      $dialog.Close()
    }
  })
  $result = $dialog.ShowDialog($form)
  $dialog.Dispose()
  if ($result -eq [Windows.Forms.DialogResult]::OK) {
    Set-Status 'KI-Zugang gespeichert. Beim nächsten Start verfügbar.'
  }
}


function Open-CustomerHelp([string]$fileName) {
  if ($fileName -notin @('Kleinanzeiger-Handbuch.html','Kleinanzeiger-Funktionen.html')) {
    throw 'Unbekannte Hilfe-Datei.'
  }
  $helpFile = Join-Path $base $fileName
  if (!(Test-Path -LiteralPath $helpFile -PathType Leaf)) {
    $helpFile = Join-Path (Join-Path $base '..\docs') $fileName
  }
  if (!(Test-Path -LiteralPath $helpFile -PathType Leaf)) {
    throw 'Hilfe-Datei fehlt. Bitte das vollstaendige Kundenpaket verwenden.'
  }
  Start-Process -FilePath $helpFile
}


function Invoke-PortfolioPreview([string]$selectedFile) {
  if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Kleinanzeiger ist nicht eingerichtet.'
  }
  $payload = @{path=$selectedFile}
  $encoded = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $payload -Compress)))
  $env:KLEINANZEIGER_IMPORT_REQUEST = $encoded
  try {
    $raw = @(& $python -m mark_api.portfolio_import_cli --db $db --mode preview 2>$null) -join ''
    if ($LASTEXITCODE -ne 0 -or !$raw) {
      throw 'Importvorschau konnte nicht erstellt werden.'
    }
    return ConvertFrom-Json -InputObject $raw
  } finally {
    Remove-Item Env:KLEINANZEIGER_IMPORT_REQUEST -ErrorAction SilentlyContinue
  }
}

function Show-ProductImport {
  if (Test-ActiveProduct) {
    Set-Status 'Vor dem Artikelimport Kleinanzeiger bitte beenden.' $true
    return
  }
  $picker = [Windows.Forms.OpenFileDialog]::new()
  $picker.Title = 'Artikel aus CSV oder Excel auswaehlen'
  $picker.Filter = 'CSV- und Excel-Dateien (*.csv;*.xlsx)|*.csv;*.xlsx'
  $picker.Multiselect = $false
  try {
    if ($picker.ShowDialog($form) -ne [Windows.Forms.DialogResult]::OK) { return }
    $path = $picker.FileName
    $preview = Invoke-PortfolioPreview $path
    if ([string]$preview.status -ne 'preview') {
      $issue = @($preview.issues)[0]
      $place = $(if($issue -and $null -ne $issue.row){"Zeile $($issue.row)"}else{'Datei'})
      [Windows.Forms.MessageBox]::Show(
        "Import nicht moeglich: $place, $($issue.field) – $($issue.message)",
        'Kleinanzeiger') | Out-Null
      return
    }
    if (!$preview.can_import) {
      [Windows.Forms.MessageBox]::Show(
        "$($preview.conflict) Artikelnummern haben abweichende bereits gespeicherte Inhalte. Bitte Importdatei pruefen.",
        'Kleinanzeiger') | Out-Null
      return
    }
    $sample = (@($preview.samples) | Select-Object -First 4 |
      ForEach-Object { $_.sku + ': ' + $_.title }) -join [Environment]::NewLine
    $question = "Datei: $($preview.filename)" + [Environment]::NewLine +
      "Artikel: $($preview.total), neu: $($preview.new), unveraendert: $($preview.unchanged)" +
      [Environment]::NewLine + [Environment]::NewLine + $sample +
      [Environment]::NewLine + [Environment]::NewLine +
      "Lokal als unveroeffentlichte Entwuerfe importieren? Zuvor wird eine Sicherung erstellt."
    if ([Windows.Forms.MessageBox]::Show(
      $question,'Kleinanzeiger – Importvorschau',
      [Windows.Forms.MessageBoxButtons]::YesNo) -ne
      [Windows.Forms.DialogResult]::Yes) { return }
    Start-Worker 'ImportProducts' $false '' @{path=$path;sha256=[string]$preview.sha256}
  } finally { $picker.Dispose() }
}

function Show-ProductExport {
  if (Test-ActiveProduct) {
    Set-Status 'Vor dem Export Kleinanzeiger bitte beenden.' $true
    return
  }
  $picker = [Windows.Forms.SaveFileDialog]::new()
  $picker.Title = 'Artikelentwuerfe als CSV exportieren'
  $picker.Filter = 'CSV-Datei (*.csv)|*.csv'
  $picker.DefaultExt = 'csv'
  $picker.AddExtension = $true
  $picker.OverwritePrompt = $false
  try {
    if ($picker.ShowDialog($form) -ne [Windows.Forms.DialogResult]::OK) { return }
    if (Test-Path -LiteralPath $picker.FileName) {
      Set-Status 'Exportziel ist bereits vorhanden. Bitte neuen Dateinamen waehlen.' $true
      return
    }
    Start-Worker 'ExportProducts' $false '' @{path=$picker.FileName}
  } finally { $picker.Dispose() }
}

function Show-CustomerMailPicker {
  if (Test-ActiveProduct) {
    Set-Status 'Zum E-Mail-Import Kleinanzeiger zuerst beenden.' $true
    return
  }
  $picker = [System.Windows.Forms.OpenFileDialog]::new()
  $picker.Title = 'Kleinanzeigen-Benachrichtigungen auswaehlen'
  $picker.Filter = 'Gespeicherte E-Mails (*.eml)|*.eml'
  $picker.Multiselect = $true
  try {
    if ($picker.ShowDialog($form) -ne [Windows.Forms.DialogResult]::OK) { return }
    $selected = @($picker.FileNames)
    if ($selected.Count -lt 1 -or $selected.Count -gt 100) {
      Set-Status 'Bitte hoechstens 100 gespeicherte E-Mails waehlen.' $true
      return
    }
    if ([Windows.Forms.MessageBox]::Show(
      "$($selected.Count) lokale E-Mail-Datei(en) in die Auswertung importieren?",
      'Kleinanzeiger', [Windows.Forms.MessageBoxButtons]::YesNo) -ne
      [Windows.Forms.DialogResult]::Yes) { return }
    Start-Worker 'ImportMail' $false '' @{paths=$selected}
  } finally { $picker.Dispose() }
}

function Show-CustomerLabels {
  if (Test-ActiveProduct) {
    Set-Status 'Zum Bearbeiten der Vergleichslabels Kleinanzeiger zuerst beenden.' $true
    return
  }
  if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Kleinanzeiger ist noch nicht eingerichtet.'
  }
  $raw = & $python -m mark_api.windows_customer_choices --db $db 2>$null
  if ($LASTEXITCODE -ne 0) { throw 'Gespeicherte Anzeigen konnten nicht gelesen werden.' }
  $choices = @(($raw | ConvertFrom-Json).ads | Where-Object { $null -ne $_ })
  if ($choices.Count -eq 0) {
    Set-Status 'Zuerst Anzeigen abgleichen oder E-Mails importieren.'
    return
  }
  $dialog = [Windows.Forms.Form]::new()
  $dialog.Text = 'Vergleichslabels'
  $dialog.ClientSize = [Drawing.Size]::new(570,388)
  $dialog.StartPosition = 'CenterParent'
  $dialog.FormBorderStyle = 'FixedDialog'
  $dialog.MaximizeBox = $false
  $dialog.Font = $font
  $note = [Windows.Forms.Label]::new()
  $note.Text = 'Anzeige auswaehlen und Merkmale fuer die Auswertungen erfassen.'
  $note.SetBounds(20,17,525,30)
  $dialog.Controls.Add($note)
  $select = [Windows.Forms.ComboBox]::new()
  $select.DropDownStyle = 'DropDownList'
  $select.SetBounds(20,54,523,31)
  foreach($item in $choices) {
    $name = $(if($item.title){[string]$item.title}else{'Ohne Titel'})
    if ($item.email_only) { $name = 'E-Mail: ' + $name }
    [void]$select.Items.Add($name + '  ·  ' + [string]$item.ad_id)
  }
  $dialog.Controls.Add($select)
  $inputs = @{}
  $fieldMap = @(
    @('image_type','Bildtyp'),
    @('city','Stadt'),
    @('text_type','Texttyp'),
    @('title_type','Titeltyp')
  )
  for($n=0;$n -lt $fieldMap.Count;$n++) {
    $field = [string]$fieldMap[$n][0]
    $label = [Windows.Forms.Label]::new()
    $label.Text = [string]$fieldMap[$n][1]
    $label.SetBounds(20,(102 + $n*49),105,26)
    $dialog.Controls.Add($label)
    $entry = [Windows.Forms.TextBox]::new()
    $entry.SetBounds(131,(99 + $n*49),410,30)
    $dialog.Controls.Add($entry)
    $inputs[$field] = $entry
  }
  $fill = {
    if ($select.SelectedIndex -lt 0) { return }
    $item = $choices[$select.SelectedIndex]
    foreach($field in @('image_type','city','text_type','title_type')) {
      $inputs[$field].Text = [string]$item.labels.$field
    }
  }
  $select.Add_SelectedIndexChanged($fill)
  $select.SelectedIndex = 0
  $save = [Windows.Forms.Button]::new()
  $save.Text = 'Vergleichslabels speichern'
  $save.SetBounds(20,315,265,44)
  $dialog.Controls.Add($save)
  $cancel = [Windows.Forms.Button]::new()
  $cancel.Text = 'Abbrechen'
  $cancel.SetBounds(306,315,235,44)
  $dialog.Controls.Add($cancel)
  $cancel.Add_Click({ $dialog.Close() })
  $save.Add_Click({
    if ($select.SelectedIndex -lt 0) { return }
    $item = $choices[$select.SelectedIndex]
    $changes = @{}
    foreach($field in @('image_type','city','text_type','title_type')) {
      $before = [string]$item.labels.$field
      $after = $inputs[$field].Text.Trim()
      if ($before -cne $after) { $changes[$field] = $after }
    }
    if ($changes.Count -eq 0) {
      [Windows.Forms.MessageBox]::Show('Keine Aenderungen vorhanden.','Kleinanzeiger') | Out-Null
      return
    }
    try {
      Start-Worker 'Classify' $false '' @{ad_id=[string]$item.ad_id;labels=$changes}
      $dialog.DialogResult = [Windows.Forms.DialogResult]::OK
      $dialog.Close()
    } catch {
      [Windows.Forms.MessageBox]::Show('Vergleichslabels konnten nicht gestartet werden.','Kleinanzeiger') | Out-Null
    }
  })
  [void]$dialog.ShowDialog($form)
  $dialog.Dispose()
}

function Open-Kleinanzeigen {
  $chrome = Find-KleinanzeigerChrome -StateDirectory $state
  if (!$chrome) { throw 'Chrome fehlt. Bitte zuerst einrichten.' }
  New-Item -ItemType Directory -Path $profile -Force | Out-Null
  $args = '--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="' + $profile + '" https://www.kleinanzeigen.de/m-meine-anzeigen.html'
  Start-Process -FilePath $chrome -ArgumentList $args
  return $chrome
}
function Start-PipedProcess([Diagnostics.ProcessStartInfo]$info,[string]$kind) {
  $proc = [System.Diagnostics.Process]::new()
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $proc.StartInfo = $info
  if (!$proc.Start()) { throw 'Prozess konnte nicht gestartet werden.' }
  $script:process = $proc
  $script:mode = $kind
  $script:outTask = $proc.StandardOutput.ReadLineAsync()
  $script:errTask = $proc.StandardError.ReadLineAsync()
  Refresh-Controls
}

function Start-Worker([string]$action,[bool]$launchAfter=$false,[string]$backupName='', [object]$payload=$null) {
  $script:nextLaunch = $launchAfter
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.FileName = 'powershell.exe'
  $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    $worker + '" -Action ' + $action + ' -PackageDirectory "' + $base + '"'
  if ($backupName) { $info.Arguments += ' -BackupName "' + $backupName + '"' }
  if ($null -ne $payload) { $info.RedirectStandardInput = $true }
  $script:details = ''
  Set-Status $(switch($action){
    'Install' {'Richte Kleinanzeiger ein …'}
    'OnlineUpdate' {'Prüfe Updates und installiere sie bei Bedarf …'}
    'Backup' {'Sichere deine Anzeigen …'}
    'Restore' {'Stelle die Sicherung wieder her …'}
    'Rollback' {'Prüfe und aktiviere die ausgewählte Programmversion …'}
    'ImportMail' {'Importiere ausgewaehlte E-Mails …'}
    'Classify' {'Speichere Vergleichslabels …'}
    'ImportProducts' {'Pruefe und importiere Artikelentwuerfe …'}
    'ExportProducts' {'Exportiere Artikelentwuerfe …'}
  })
  Start-PipedProcess $info $action
  if ($null -ne $payload) {
    $json = ConvertTo-Json -InputObject $payload -Depth 5 -Compress
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
    $script:process.StandardInput.WriteLine($encoded)
    $script:process.StandardInput.Close()
  }
}

function Start-Product {
  if (Test-ActiveProduct) { throw 'Kleinanzeiger läuft bereits. Bitte die bestehende Anwendung zuerst beenden.' }
  if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Kleinanzeiger ist nicht installiert.' }
  $script:details = ''
  Set-Status 'Öffne Chrome und prüfe die lokale Browsersteuerung …'
  $statusLabel.Refresh()
  $browserReady = $false
  try {
    $chrome = Open-Kleinanzeigen
    Wait-KleinanzeigerChromeDevTools -Port 9222 -TimeoutSeconds 12
    $browserReady = $true
  } catch {
    # A browser failure is not a login failure and must not hide local work.
    # The Python launcher validates the database and never enables writes
    # without a fresh, verified owner inventory.
    $script:details = $_.Exception.Message
    Set-Status 'Browserverbindung nicht bereit. Öffne den lokalen Arbeitsbereich …'
  }
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.UseShellExecute = $false
  $info.FileName = $python
  $info.Arguments = '-m mark_api.launcher --db "' + $db + '" --cdp-port 9222 --timeout-seconds 12 --stop-on-stdin'
  if (!(Test-Path -LiteralPath $db -PathType Leaf)) { $info.Arguments += ' --init-db' }
  if (Test-Path -LiteralPath $keyFile -PathType Leaf) {
    try {
      $secret = ConvertTo-SecureString -String (Get-Content -LiteralPath $keyFile -Raw).Trim()
      $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
      try {
        $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if (!$token) { throw 'KI-Schlüssel leer.' }
        $info.EnvironmentVariables['OPENAI_API_KEY'] = $token
        $info.Arguments += ' --enable-generation'
      } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        Remove-Variable token -ErrorAction SilentlyContinue
        $secret.Dispose()
      }
    } catch {
      Set-Status 'KI-Zugang konnte nicht gelesen werden. Starte ohne KI.'
    }
  }
  $info.RedirectStandardInput = $true
  $script:openedDashboard = $false
  $script:stopRequested = $false
  if ($browserReady) { Set-Status 'Verbinde mit deinem Kleinanzeigen-Konto …' }
  else { Set-Status 'Prüfe den gespeicherten Bestand und öffne den lokalen Arbeitsbereich …' }
  Start-PipedProcess $info 'product'
}
function Stop-Product {
  if (!$script:process -or $script:process.HasExited -or $script:mode -ne 'product') { return }
  if ($script:stopRequested) { return }
  $script:stopRequested = $true
  try {
    $script:process.StandardInput.WriteLine('STOP')
    $script:process.StandardInput.Flush()
    Set-Status 'Beende Kleinanzeiger kontrolliert …'
    $primary.Enabled = $false
  } catch {
    Set-Status 'Kontrolliertes Beenden fehlgeschlagen. Bitte Anwendung schließen.' $true
  }
}

function Handle-Line([string]$line,[bool]$isError) {
  if (!$line) { return }
  # Never persist or display capability fragments, prompt bodies or keys.
  if ($line -match 'Kleinanzeiger dashboard:\s+(http://127\.0\.0\.1:\d+/\#[^\s]+)') {
    if (!$script:openedDashboard) {
      $script:openedDashboard = $true
      try {
        $dashboardAddress = $Matches[1]
        $chrome = (Find-KleinanzeigerChrome -StateDirectory $state)
        if ($chrome) {
          $arg = '--user-data-dir="' + $profile + '" "' + $dashboardAddress + '"'
          Start-Process -FilePath $chrome -ArgumentList $arg
        } else {
          # Local work only; no platform browser automation in this fallback.
          Start-Process -FilePath $dashboardAddress
        }
        Set-Status 'Kleinanzeiger ist geöffnet. Anzeigen können jetzt verwaltet werden.'
      } catch { Set-Status 'Dashboard konnte nicht geöffnet werden.' $true }
    }
    return
  }
  if ($line -like 'Kleinanzeiger offline:*') {
    Set-Status 'Lokaler Arbeitsmodus. Gespeicherte Daten können veraltet sein; Plattformänderungen sind gesperrt.'
    return
  }
  if ($line -match '(?i)(write_token|generation_token|OPENAI_API_KEY|sk-proj-|Bearer\s)') { return }
  if ($script:details.Length -lt 4000) { $script:details += $line + [Environment]::NewLine }
  if ($isError -and $script:mode -eq 'product') {
    Set-Status 'Start fehlgeschlagen – Fehlerdetails unten öffnen.' $true
  }
}
function Drain-Stream([string]$kind) {
  $taskName = $(if($kind -eq 'out'){'outTask'}else{'errTask'})
  $reader = $(if($kind -eq 'out'){$script:process.StandardOutput}else{$script:process.StandardError})
  for($i=0;$i -lt 12;$i++) {
    $task = $(if($kind -eq 'out'){$script:outTask}else{$script:errTask})
    if ($null -eq $task -or !$task.IsCompleted) { return }
    try { $line = $task.GetAwaiter().GetResult() } catch { $line = $null }
    if ($null -eq $line) {
      if ($kind -eq 'out') { $script:outTask = $null } else { $script:errTask = $null }
      return
    }
    Handle-Line $line ($kind -eq 'err')
    if ($kind -eq 'out') { $script:outTask = $reader.ReadLineAsync() }
    else { $script:errTask = $reader.ReadLineAsync() }
  }
}

function Finish-Process {
  $exit = $script:process.ExitCode
  $kind = $script:mode
  $script:process.Dispose()
  $script:process = $null
  $script:mode = ''
  $script:outTask = $null
  $script:errTask = $null
  Refresh-Controls
  if ($kind -eq 'product') {
    if ($script:stopRequested -and $exit -eq 0) {
      Set-Status 'Kleinanzeiger wurde beendet.'
    } elseif (!$script:openedDashboard) {
      if ($script:details.Length -lt 3900) {
        $script:details += "Kleinanzeiger-Prozess beendet (Exitcode $exit)." + [Environment]::NewLine
      }
      Set-Status 'Start fehlgeschlagen – Fehlerdetails unten öffnen.' $true
    } elseif ($exit -ne 0) {
      Set-Status 'Kleinanzeiger wurde unerwartet beendet.' $true
    }
    if ($script:closeAfterStop) { $form.Close() }
    return
  }
  if ($exit -ne 0) {
    Set-Status 'Aktion fehlgeschlagen. Details über Hilfe/Protokoll anzeigen.' $true
    return
  }
  if ($script:details.Contains('ALREADY_CURRENT')) {
    Set-Status 'Alles aktuell. Keine Installation nötig.'
  } else {
    Set-Status $(switch($kind){
      'Install' {'Einrichtung abgeschlossen.'}
      'OnlineUpdate' {'Update abgeschlossen. Kleinanzeiger kann gestartet werden.'}
      'Backup' {'Sicherung erfolgreich erstellt.'}
      'Restore' {'Sicherung erfolgreich wiederhergestellt.'}
      'Rollback' {'Vorherige Programmversion aktiviert. Anzeigen und Chrome-Profil sind unverändert.'}
      'ImportMail' {'E-Mails importiert. Anzeigen beim naechsten Start neu laden.'}
      'Classify' {'Vergleichslabels gespeichert. Auswertungen beim naechsten Start neu laden.'}
      'ImportProducts' {'Artikelentwuerfe gespeichert. Im Dashboard nach dem Start ansehen.'}
      'ExportProducts' {'Artikelentwuerfe exportiert. Grosse Bestaende liegen als CSV-Teile (-part002.csv usw.) im selben Ordner.'}
    })
  }
  if ($script:nextLaunch) {
    $script:nextLaunch = $false
    try { Start-Product } catch { Set-Status $_.Exception.Message $true }
  }
}

$timer = [System.Windows.Forms.Timer]::new()
$timer.Interval = 250
$timer.Add_Tick({
  if (!$script:process) { return }
  try {
    Drain-Stream 'out'
    Drain-Stream 'err'
    if ($script:process.HasExited -and $null -eq $script:outTask -and $null -eq $script:errTask) {
      Finish-Process
    }
  } catch {
    Set-Status 'Vorgang konnte nicht ausgewertet werden. Bitte erneut öffnen.' $true
  }
})
$primary.Add_Click({
  try {
    if ($script:mode -eq 'product' -and $script:process -and !$script:process.HasExited) {
      Stop-Product
    } elseif (!$script:process) {
      if (Test-ActiveProduct) {
        Set-Status 'Eine bisherige Kleinanzeiger-Sitzung läuft noch. Diese zuerst beenden.' $true
        return
      }
      $installed = Get-InstalledVersion
      if (!$installed) {
        Start-Worker 'Install' $true
      } else {
        Start-Product
      }
    }
  } catch { Set-Status $_.Exception.Message $true }
})
$update.Add_Click({
  try {
    if (Test-ActiveProduct) {
      Set-Status 'Kleinanzeiger läuft noch. Vor dem Update die bisherige Sitzung beenden.' $true
      return
    }
    $installed = Get-InstalledVersion
    if ($script:packageVersion -and
        (!$installed -or ([version]$installed -lt [version]$script:packageVersion))) {
      Start-Worker 'Install'
    } else {
      Start-Worker 'OnlineUpdate'
    }
  } catch { Set-Status $_.Exception.Message $true }
})
$backup.Add_Click({ try { Start-Worker 'Backup' } catch { Set-Status $_.Exception.Message $true } })
$mail.Add_Click({ try { Show-CustomerMailPicker } catch { Set-Status $_.Exception.Message $true } })
$labels.Add_Click({ try { Show-CustomerLabels } catch { Set-Status $_.Exception.Message $true } })
$products.Add_Click({ try { Show-ProductImport } catch { Set-Status $_.Exception.Message $true } })
$productsExport.Add_Click({ try { Show-ProductExport } catch { Set-Status $_.Exception.Message $true } })
$help.Add_Click({ try { Open-CustomerHelp 'Kleinanzeiger-Handbuch.html' } catch { Set-Status $_.Exception.Message $true } })
$features.Add_Click({ try { Open-CustomerHelp 'Kleinanzeiger-Funktionen.html' } catch { Set-Status $_.Exception.Message $true } })
$account.Add_Click({
  try { Open-Kleinanzeigen | Out-Null; Set-Status 'Bitte im Chrome-Fenster anmelden, dann Kleinanzeiger starten.' }
  catch { Set-Status $_.Exception.Message $true }
})
$ai.Add_Click({ if (!$script:process) { Show-KiDialog } })
$advanced.Add_Click({ Set-AdvancedLayout (!$script:advancedExpanded) })
$rollback.Add_Click({
  try {
    if (Test-ActiveProduct) {
      Set-Status 'Bitte Kleinanzeiger vor dem Versionswechsel vollständig beenden.' $true
      return
    }
    $folders = @(Get-ChildItem -LiteralPath $state -Directory -Filter '.venv-rollback-*' -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -match '^\.venv-rollback-\d{8}-\d{6}-\d{3}-[0-9a-f]{32}$' -and
        -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
      } | Sort-Object Name -Descending)
    if ($folders.Count -eq 0) {
      Set-Status 'Keine frühere Programmversion vorhanden.'
      return
    }
    $picker = [Windows.Forms.Form]::new()
    $picker.Text = 'Vorherige Programmversion'
    $picker.ClientSize = [Drawing.Size]::new(530,155)
    $picker.FormBorderStyle = 'FixedDialog'
    $picker.MaximizeBox = $false
    $picker.StartPosition = 'CenterParent'
    $select = [Windows.Forms.ComboBox]::new()
    $select.DropDownStyle = 'DropDownList'
    $select.SetBounds(18,17,494,32)
    $names = @()
    foreach ($folder in $folders) {
      $exe = Join-Path $folder.FullName 'Scripts\python.exe'
      if (!(Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
      try {
        $version = @(& $exe -c "import importlib.metadata as m; print(m.version('kleinanzeiger'))" 2>$null)
        if ($LASTEXITCODE -ne 0 -or $version.Count -ne 1 -or
            [string]$version[0] -notmatch '^\d+\.\d+\.\d+$') { continue }
      } catch { continue }
      [void]$select.Items.Add(("Version " + [string]$version[0] + " – " + $folder.Name.Substring(15,19)))
      $names += $folder.Name
    }
    if ($names.Count -eq 0) {
      $picker.Dispose()
      Set-Status 'Keine geprüfte vorherige Programmversion gefunden.' $true
      return
    }
    $select.SelectedIndex = 0
    $picker.Controls.Add($select)
    $ok = [Windows.Forms.Button]::new()
    $ok.Text = 'Weiter'
    $ok.SetBounds(18,75,180,36)
    $picker.Controls.Add($ok)
    $ok.Add_Click({ $picker.DialogResult = [Windows.Forms.DialogResult]::OK; $picker.Close() })
    $dialogResult = $picker.ShowDialog($form)
    $index = $select.SelectedIndex
    $picker.Dispose()
    if ($dialogResult -ne [Windows.Forms.DialogResult]::OK -or $index -lt 0) { return }
    $selected = [string]$names[$index]
    $question = "Die ausgewählte frühere Programmversion aktivieren?" +
      [Environment]::NewLine + [Environment]::NewLine +
      "Die aktuelle Programmversion wird für den Rückwechsel erhalten." +
      [Environment]::NewLine + "Anzeigen, Sicherungen und Chrome-Profil bleiben unverändert."
    if ([Windows.Forms.MessageBox]::Show($question,'Kleinanzeiger – Programmversion wechseln',
        [Windows.Forms.MessageBoxButtons]::YesNo) -ne [Windows.Forms.DialogResult]::Yes) { return }
    Start-Worker 'Rollback' $false $selected
  } catch { Set-Status $_.Exception.Message $true }
})
$restore.Add_Click({
  if (Test-Path -LiteralPath $db) {
    Set-Status 'Die Datenbank ist bereits vorhanden. Wiederherstellung gesperrt.' $true
    return
  }
  $list = @(Get-ChildItem -LiteralPath (Join-Path $state 'Backups') -Filter '*.sqlite' -File -ErrorAction SilentlyContinue)
  if ($list.Count -eq 0) { Set-Status 'Noch keine Sicherungen vorhanden.'; return }
  $picker = [System.Windows.Forms.Form]::new()
  $picker.Text = 'Sicherung wählen'
  $picker.ClientSize = [System.Drawing.Size]::new(520,140)
  $picker.StartPosition = 'CenterParent'
  $picker.FormBorderStyle = 'FixedDialog'
  $picker.MaximizeBox = $false
  $select = [System.Windows.Forms.ComboBox]::new()
  $select.DropDownStyle = 'DropDownList'
  $select.SetBounds(18,19,484,30)
  foreach($f in $list){[void]$select.Items.Add($f.Name)}
  $select.SelectedIndex = 0
  $picker.Controls.Add($select)
  $ok = [System.Windows.Forms.Button]::new()
  $ok.Text = 'Auswählen'
  $ok.SetBounds(18,74,160,35)
  $picker.Controls.Add($ok)
  $ok.Add_Click({$picker.DialogResult=[Windows.Forms.DialogResult]::OK;$picker.Close()})
  $result = $picker.ShowDialog($form)
  $selected = [string]$select.SelectedItem
  $picker.Dispose()
  if ($result -eq [Windows.Forms.DialogResult]::OK -and
      [Windows.Forms.MessageBox]::Show("Sicherung $selected in die neue leere Datenbank einspielen?",'Kleinanzeiger',
          [Windows.Forms.MessageBoxButtons]::YesNo) -eq [Windows.Forms.DialogResult]::Yes) {
    try { Start-Worker 'Restore' $false $selected }
    catch { Set-Status $_.Exception.Message $true }
  }
})
$form.Add_FormClosed({
  if ($script:singleInstanceMutex) {
    try { $script:singleInstanceMutex.ReleaseMutex() } catch { }
    $script:singleInstanceMutex.Dispose()
    $script:singleInstanceMutex = $null
  }
})
$form.Add_FormClosing({
  param($sender,$eventArgs)
  if ($script:process -and !$script:process.HasExited) {
    $eventArgs.Cancel = $true
    if ($script:mode -eq 'product') {
      if (!$script:closeAfterStop -and [Windows.Forms.MessageBox]::Show(
          'Kleinanzeiger läuft. Vor dem Schließen die laufenden Vorgänge abschließen und die Anwendung beenden?',
          'Kleinanzeiger',[Windows.Forms.MessageBoxButtons]::YesNo) -eq [Windows.Forms.DialogResult]::Yes) {
        $script:closeAfterStop = $true
        Stop-Product
      }
    } else {
      Set-Status 'Installation oder Sicherung läuft. Bitte erst abschließen.'
    }
  }
})
$form.Add_Shown({
  Refresh-Controls
  if (Test-ActiveProduct) {
    Set-Status 'Kleinanzeiger ist noch geöffnet. Für ein Update zuerst dort beenden.'
  }
})
if ($SelfTest) {
  if (!(Get-Command -Name Find-KleinanzeigerChrome -CommandType Function -ErrorAction SilentlyContinue) -or
      !(Get-Command -Name Wait-KleinanzeigerChromeDevTools -CommandType Function -ErrorAction SilentlyContinue)) {
    throw 'Chrome-Hilfe oder Startpruefung wurde nicht im gemeinsamen Fenster-Scope geladen.'
  }
  if (Test-KleinanzeigerChromeDevTools -Port 0) { throw 'Ungueltiger CDP-Port wurde akzeptiert.' }
  $script:readinessProbeCount = 0
  Wait-KleinanzeigerChromeDevTools -Port 9222 -TimeoutSeconds 1 -Probe {
    param([int]$checkedPort)
    $script:readinessProbeCount++
    return ($checkedPort -eq 9222 -and $script:readinessProbeCount -eq 2)
  }
  if ($script:readinessProbeCount -ne 2) { throw 'Chrome-Startpruefung hat nicht gewartet.' }
  $rejected = $false
  try {
    Wait-KleinanzeigerChromeDevTools -Port 9222 -TimeoutSeconds 0 -Probe { return $false }
  } catch {
    $rejected = ($_.Exception.Message -match 'Port 9222')
  }
  if (!$rejected) { throw 'Fehlende Chrome-Browsersteuerung nicht erkannt.' }
  if ($ExpectedInstalledVersion -and (Get-InstalledVersion) -ne $ExpectedInstalledVersion) {
    throw 'Die installierte Version wurde vom Kontrollfenster nicht erkannt.'
  }
  foreach ($file in @('windows-worker.ps1','windows-chrome.ps1','windows-install.ps1')) {
    if (!(Test-Path -LiteralPath (Join-Path $base $file))) { throw "Fehlendes Skript: $file" }
  }
  foreach ($file in @('Kleinanzeiger-Handbuch.html','Kleinanzeiger-Funktionen.html')) {
    if (!(Test-Path -LiteralPath (Join-Path $base $file)) -and
        !(Test-Path -LiteralPath (Join-Path (Join-Path $base '..\docs') $file))) {
      throw "Hilfe-Datei fehlt: $file"
    }
  }
  if ($script:advancedExpanded -or $form.ClientSize.Height -ne 510 -or
      $advanced.Text -ne 'Weitere Aktionen' -or $detailsLink.Visible -or
      $statusLabel.Top -ne 397 -or $detailsLink.Top -ne 453) {
    throw 'Kompakte Bedienoberflaeche ist nicht korrekt gestartet.'
  }
  Set-AdvancedLayout $true
  # Controls inherit Form.Visible; the headless SelfTest cannot assert
  # effective child Visible without showing a customer window.
  if (!$script:advancedExpanded -or $form.ClientSize.Height -ne 682 -or
      $advanced.Text -ne 'Weniger anzeigen' -or $statusLabel.Top -ne 567 -or
      $detailsLink.Top -ne 626 -or $rollback.Top -ne 508 -or
      $mail.Top -ne 390 -or $productsExport.Top -ne 449) {
    throw 'Erweiterte Kundenaktionen sind nicht vollstaendig erreichbar.'
  }
  Set-AdvancedLayout $false
  if ($form.Controls.Count -lt 12 -or $primary.Text -ne 'Kleinanzeiger starten' -or
      $null -eq $statusLabel -or $null -eq $detailsLink -or $null -eq $restore -or
      $null -eq $mail -or $null -eq $labels -or $null -eq $products -or $null -eq $productsExport -or $null -eq $rollback -or $null -eq $help -or $null -eq $features) {
    throw 'Die zentrale Windows-Oberflaeche ist unvollstaendig.'
  }
  $timer.Dispose()
  $form.Dispose()
  Write-Output 'WINDOWS_CENTER_SELFTEST_OK'
  return
}
$timer.Start()
try { [Windows.Forms.Application]::Run($form) }
finally { $timer.Stop();$timer.Dispose();$form.Dispose() }