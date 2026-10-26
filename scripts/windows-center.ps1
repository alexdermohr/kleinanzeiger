param([switch]$SelfTest)
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
    $result = & $python -c 'import importlib.metadata as m; print(m.version("kleinanzeiger"))' 2>$null
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
$form.ClientSize = [System.Drawing.Size]::new(640,445)
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
$primary = Add-Button 'Kleinanzeiger starten' 28 145 584 55 $true
$update = Add-Button 'Installieren / aktualisieren' 28 216 282 43
$backup = Add-Button 'Sicherung erstellen' 330 216 282 43
$ai = Add-Button 'KI einrichten' 28 273 282 43
$account = Add-Button 'Kleinanzeigen-Konto öffnen' 330 273 282 43
$statusLabel = Add-Label 'Bereit.' 28 330 584 44 $font $muted
$detailsLink = [System.Windows.Forms.LinkLabel]::new()
$detailsLink.Text = 'Details'
$detailsLink.SetBounds(541,353,71,26)
$detailsLink.LinkColor = $accent
$form.Controls.Add($detailsLink)
$detailsLink.Add_Click({
  $shown = $(if ($script:details) { $script:details } else { 'Keine zusätzlichen Informationen.' })
  [Windows.Forms.MessageBox]::Show($shown,'Kleinanzeiger – technische Details') | Out-Null
})
$advanced = Add-Button 'Weitere Optionen' 28 396 200 32
$advanced.Font = [System.Drawing.Font]::new('Segoe UI',8.5)
$restore = Add-Button 'Sicherung wiederherstellen …' 248 396 280 32
$restore.Font = [System.Drawing.Font]::new('Segoe UI',8.5)
$restore.Visible = $false

function Set-Status([string]$message,[bool]$failure=$false) {
  $statusLabel.Text = $message
  $statusLabel.ForeColor = $(if($failure){[Drawing.Color]::FromArgb(167,51,67)}else{$muted})
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
  $needsInstall = !$installed -or ($script:packageVersion -and [version]$installed -lt [version]$script:packageVersion)
  $primary.Text = $(if($product){'Kleinanzeiger beenden'}elseif($needsInstall){'Einrichten und starten'}else{'Kleinanzeiger starten'})
  $primary.Enabled = !$busy -or $product
  $update.Enabled = !$busy
  $backup.Enabled = !$busy -and (Test-Path -LiteralPath $db)
  $ai.Enabled = !$busy
  $account.Enabled = !$busy
  $restore.Enabled = !$busy -and !(Test-Path -LiteralPath $db)
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

function Open-Kleinanzeigen {
  . (Join-Path $base 'windows-chrome.ps1')
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

function Start-Worker([string]$action,[bool]$launchAfter=$false,[string]$backupName='') {
  $script:nextLaunch = $launchAfter
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.FileName = 'powershell.exe'
  $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    $worker + '" -Action ' + $action + ' -PackageDirectory "' + $base + '"'
  if ($backupName) { $info.Arguments += ' -BackupName "' + $backupName + '"' }
  $script:details = ''
  Set-Status $(switch($action){
    'Install' {'Richte Kleinanzeiger ein …'}
    'OnlineUpdate' {'Prüfe Updates und installiere sie bei Bedarf …'}
    'Backup' {'Sichere deine Anzeigen …'}
    'Restore' {'Stelle die Sicherung wieder her …'}
  })
  Start-PipedProcess $info $action
}

function Start-Product {
  if (Test-ActiveProduct) { throw 'Kleinanzeiger läuft bereits. Bitte die bestehende Anwendung zuerst beenden.' }
  if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Kleinanzeiger ist nicht installiert.' }
  $chrome = Open-Kleinanzeigen
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.UseShellExecute = $false
  $info.FileName = $python
  $info.Arguments = '-m mark_api.launcher --db "' + $db + '" --cdp-port 9222 --stop-on-stdin'
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
  $script:details = ''
  Set-Status 'Verbinde mit deinem Kleinanzeigen-Konto …'
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
        $chrome = (Find-KleinanzeigerChrome -StateDirectory $state)
        $arg = '--user-data-dir="' + $profile + '" "' + $Matches[1] + '"'
        Start-Process -FilePath $chrome -ArgumentList $arg
        Set-Status 'Kleinanzeiger ist geöffnet. Anzeigen können jetzt verwaltet werden.'
      } catch { Set-Status 'Dashboard konnte nicht geöffnet werden.' $true }
    }
    return
  }
  if ($line -match '(?i)(write_token|generation_token|OPENAI_API_KEY|sk-proj-|Bearer\s)') { return }
  if ($script:details.Length -lt 4000) { $script:details += $line + [Environment]::NewLine }
  if ($isError -and $script:mode -eq 'product') {
    Set-Status 'Start fehlgeschlagen. Im Browser anmelden und erneut starten.' $true
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
      Set-Status 'Konnte nicht starten. Falls nötig in Chrome anmelden und erneut versuchen.' $true
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
      if (!$installed -or ($script:packageVersion -and
          [version]$installed -lt [version]$script:packageVersion)) {
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
$account.Add_Click({
  try { Open-Kleinanzeigen | Out-Null; Set-Status 'Bitte im Chrome-Fenster anmelden, dann Kleinanzeiger starten.' }
  catch { Set-Status $_.Exception.Message $true }
})
$ai.Add_Click({ if (!$script:process) { Show-KiDialog } })
$advanced.Add_Click({ $restore.Visible = !$restore.Visible })
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
  foreach ($file in @('windows-worker.ps1','windows-chrome.ps1','windows-install.ps1')) {
    if (!(Test-Path -LiteralPath (Join-Path $base $file))) { throw "Fehlendes Skript: $file" }
  }
  if ($form.Controls.Count -lt 12 -or $primary.Text -ne 'Kleinanzeiger starten' -or
      $null -eq $statusLabel -or $null -eq $detailsLink -or $null -eq $restore) {
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