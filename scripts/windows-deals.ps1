param([switch]$SelfTest, [string]$ExpectedInstalledVersion = '')
$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$python = Join-Path $state '.venv\Scripts\python.exe'
# All analysis happens inside the installed Wheel; no platform read or write.
if ($SelfTest) {
  if ($ExpectedInstalledVersion) {
    if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Kleinanzeiger ist nicht installiert.' }
    $version = @(& $python -c "import importlib.metadata as m; print(m.version('kleinanzeiger'))" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $version.Count -ne 1 -or
        ([string]$version[0]).Trim() -cne $ExpectedInstalledVersion) {
      throw 'Schnäppchenjäger ist nicht in der erwarteten installierten Version.'
    }
    $model = 'Lenovo ThinkPad T14 Gen 3'
    $search = @(& $python -m mark_api.bargain_hunter_cli --modell $model --suchlink 2>&1)
    if ($LASTEXITCODE -ne 0 -or $search.Count -ne 1 -or
        !([string]$search[0]).StartsWith('https://www.kleinanzeigen.de/s-suchanfrage.html?')) {
      throw 'Schnäppchenjäger-Suchlink aus installiertem Wheel defekt.'
    }
    $temp = Join-Path $env:TEMP ('Kleinanzeiger-Deals-SelfTest-' + [guid]::NewGuid().ToString('N'))
    New-Item -Path $temp -ItemType Directory | Out-Null
    try {
      $source = Join-Path $temp 'synthetische-angebote.csv'
      $report = Join-Path $temp 'lokaler-bericht.html'
      $lines = [System.Collections.Generic.List[string]]::new()
      $lines.Add('id,title,price,url,condition,shipping_eur,observed_at,source')
      foreach ($n in 1..5) {
        $amount = $(if ($n -eq 1) { '90' } else { '200' })
        $lines.Add("$n,Lenovo ThinkPad T14 Gen 3 i5 16GB RAM 512GB SSD,$amount,https://www.kleinanzeigen.de/s-anzeige/beispiel/$n,gebraucht,0,2026-11-08T08:00:00+01:00,Synthetischer Windows-Test")
      }
      [IO.File]::WriteAllLines($source,[string[]]$lines.ToArray(),[Text.UTF8Encoding]::new($false))
      $result = @(& $python -m mark_api.bargain_hunter_cli --datei $source --modell $model --bericht $report 2>&1)
      if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $report -PathType Leaf)) {
        throw 'Schnäppchenjäger kann aus CSV keinen lokalen HTML-Bericht erstellen.'
      }
      $html = [IO.File]::ReadAllText($report,[Text.Encoding]::UTF8)
      if (!$html.Contains('auffällig günstig') -or !$html.Contains('Synthetischer Windows-Test')) {
        throw 'Der lokale Bericht enthält nicht die erwartete Bewertung.'
      }
    } finally {
      Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  Write-Output 'DEALS_CUSTOMER_UI_SELFTEST_PASS'
  return
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
  [Windows.Forms.MessageBox]::Show('Bitte Kleinanzeiger zuerst im Kontrollfenster einrichten.',
    'Kleinanzeiger – Schnäppchenjäger') | Out-Null
  return
}
$dialog = [Windows.Forms.Form]::new()
$dialog.Text = 'Kleinanzeiger – Schnäppchenjäger'
$dialog.ClientSize = [Drawing.Size]::new(632,404)
$dialog.FormBorderStyle = 'FixedDialog'
$dialog.MaximizeBox = $false
$dialog.StartPosition = 'CenterScreen'
$dialog.AutoScaleMode = 'Dpi'
$dialog.Font = [Drawing.Font]::new('Segoe UI',10)
$dialog.BackColor = [Drawing.Color]::FromArgb(245,248,250)
function Add-DealsLabel([string]$text,[int]$x,[int]$y,[int]$w,[int]$h) {
  $item = [Windows.Forms.Label]::new()
  $item.Text = $text
  $item.SetBounds($x,$y,$w,$h)
  $dialog.Controls.Add($item)
  return $item
}
function Add-DealsButton([string]$text,[int]$x,[int]$y,[int]$w,[int]$h) {
  $item = [Windows.Forms.Button]::new()
  $item.Text = $text
  $item.SetBounds($x,$y,$w,$h)
  $item.FlatStyle = 'Flat'
  $dialog.Controls.Add($item)
  return $item
}
$title = Add-DealsLabel 'Schnäppchenjäger' 22 16 575 36
$title.Font = [Drawing.Font]::new('Segoe UI',18,[Drawing.FontStyle]::Bold)
[void](Add-DealsLabel 'Exaktes Modell, z. B. Lenovo ThinkPad T14 Gen 3' 22 62 585 24)
$modelInput = [Windows.Forms.TextBox]::new()
$modelInput.SetBounds(22,89,584,31)
$dialog.Controls.Add($modelInput)
[void](Add-DealsLabel 'Eigene Angebotsdatei (CSV oder JSON)' 22 131 585 25)
$fileInput = [Windows.Forms.TextBox]::new()
$fileInput.ReadOnly = $true
$fileInput.SetBounds(22,159,435,31)
$dialog.Controls.Add($fileInput)
$choose = Add-DealsButton 'Datei wählen …' 468 158 138 33
$note = Add-DealsLabel 'Nur selbst bereitgestellte Angebote werden verglichen. Keine automatische Marktplatzsuche, keine Kontozugriffe, keine Käufe. Die Auswertung beruht auf beobachteten Angebotspreisen.' 22 206 584 61
$note.ForeColor = [Drawing.Color]::FromArgb(88,107,115)
$searchButton = Add-DealsButton 'Suche im Browser öffnen' 22 279 281 44
$checkButton = Add-DealsButton 'Datei lokal auswerten' 325 279 281 44
$statusLabel = Add-DealsLabel 'Modell eingeben; anschließend suchen oder eine Angebotsdatei prüfen.' 22 341 584 49
$statusLabel.ForeColor = [Drawing.Color]::FromArgb(17,103,122)
function Get-DealsModel {
  $model = $modelInput.Text.Trim()
  if ($model.Length -lt 3 -or $model.Length -gt 160 -or $model -match '[\x00-\x1F]') {
    throw 'Bitte eine exakte Modellbezeichnung eingeben (3–160 Zeichen).'
  }
  return $model
}
$choose.Add_Click({
  $picker = [Windows.Forms.OpenFileDialog]::new()
  try {
    $picker.Title = 'Angebote aus eigener CSV- oder JSON-Datei auswählen'
    $picker.Filter = 'Angebote (*.csv;*.json)|*.csv;*.json|CSV (*.csv)|*.csv|JSON (*.json)|*.json'
    $picker.Multiselect = $false
    $picker.CheckFileExists = $true
    if ($picker.ShowDialog($dialog) -eq [Windows.Forms.DialogResult]::OK) {
      $fileInput.Text = $picker.FileName
      $statusLabel.Text = 'Datei gewählt. Auswertung startet erst mit Ihrem Klick.'
    }
  } finally { $picker.Dispose() }
})
$searchButton.Add_Click({
  try {
    $model = Get-DealsModel
    $result = @(& $python -m mark_api.bargain_hunter_cli --modell $model --suchlink 2>&1)
    if ($LASTEXITCODE -ne 0 -or $result.Count -ne 1) {
      throw 'Der Suchlink konnte nicht erstellt werden.'
    }
    $url = ([string]$result[0]).Trim()
    $target = $null
    if (![Uri]::TryCreate($url,[UriKind]::Absolute,[ref]$target) -or
        $target.Scheme -cne 'https' -or $target.Host -cne 'www.kleinanzeigen.de' -or
        $target.AbsolutePath -cne '/s-suchanfrage.html') {
      throw 'Ungültiger Suchlink – kein Browseraufruf erfolgt.'
    }
    Start-Process -FilePath $url | Out-Null
    $statusLabel.Text = 'Kleinanzeigen-Suchseite geöffnet. Angebote werden nicht automatisch eingelesen.'
  } catch { $statusLabel.Text = $_.Exception.Message }
})
$checkButton.Add_Click({
  $checkButton.Enabled = $false
  try {
    $model = Get-DealsModel
    $source = $fileInput.Text.Trim()
    if (!$source -or !(Test-Path -LiteralPath $source -PathType Leaf) -or
        [IO.Path]::GetExtension($source).ToLowerInvariant() -notin @('.csv','.json')) {
      throw 'Bitte erst eine vorhandene Angebotsdatei (.csv oder .json) wählen.'
    }
    $sourceFile = Get-Item -LiteralPath $source
    if ($sourceFile.Length -gt 8000000) { throw 'Die Angebotsdatei ist größer als 8 MB.' }
    $reports = Join-Path $state 'Berichte'
    New-Item -Path $reports -ItemType Directory -Force | Out-Null
    $name = 'Kleinanzeiger-Schnaeppchen-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.html'
    $report = Join-Path $reports $name
    $out = @(& $python -m mark_api.bargain_hunter_cli --datei $source --modell $model --bericht $report 2>&1)
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $report -PathType Leaf)) {
      $message = ($out | Select-Object -Last 2 | Out-String).Trim()
      if ($message.Length -gt 190) { $message = $message.Substring(0,190) + '…' }
      if (!$message) { $message = 'Ungültige Angebotsdatei oder fehlende Vergleichsdaten.' }
      throw $message
    }
    Start-Process -FilePath $report | Out-Null
    $statusLabel.Text = 'HTML-Bericht erstellt und geöffnet. Alle Angebotsdaten bleiben lokal.'
  } catch { $statusLabel.Text = $_.Exception.Message }
  finally { $checkButton.Enabled = $true }
})
[Windows.Forms.Application]::Run($dialog)
$dialog.Dispose()
