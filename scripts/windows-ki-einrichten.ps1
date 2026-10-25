$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$target = Join-Path $state 'openai-key.dpapi'
New-Item -ItemType Directory -Path $state -Force | Out-Null
Write-Host 'Optional: OpenAI API-Key fuer KI-Text- und Bildentwuerfe.'
Write-Host 'API-Aufrufe koennen Kosten verursachen; jede Generierung erfordert eine eigene Bestaetigung.'
Write-Host 'Der Schluessel wird Windows-benutzergebunden verschluesselt (DPAPI) gespeichert.'
$secret = Read-Host 'Eigenen OpenAI API-Key eingeben (Eingabe verborgen)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
  $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
  if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -lt 20) {
    throw 'API-Key ist leer oder zu kurz. Es wurde nichts gespeichert.'
  }
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
  Remove-Variable value -ErrorAction SilentlyContinue
}
$encrypted = ConvertFrom-SecureString -SecureString $secret
$temporary = $target + '.new'
try {
  Set-Content -LiteralPath $temporary -Value $encrypted -Encoding Ascii -NoNewline
  Move-Item -LiteralPath $temporary -Destination $target -Force
} finally {
  Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
Write-Host 'KI-Zugang gespeichert. Kleinanzeiger neu mit Starten.cmd starten.'
Write-Host 'Ohne API-Key kann die gesamte Anzeigenverwaltung weiterhin genutzt werden.'
