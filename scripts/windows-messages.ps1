param([switch]$SelfTest)
$ErrorActionPreference = 'Stop'
$state = Join-Path $env:LOCALAPPDATA 'Kleinanzeiger'
$db = Join-Path $state 'anzeigen.sqlite'
$python = Join-Path $state '.venv\Scripts\python.exe'
$keyFile = Join-Path $state 'klaz-api-key.dpapi'
$journal = Join-Path $state 'messages-outbox.sqlite'

if ($SelfTest) {
  if (!(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Das installierte Kunden-Python fehlt.'
  }
  & $python -c "import mark_api.platform_messages as m; assert callable(m.main)"
  if ($LASTEXITCODE -ne 0) { throw 'Die Nachrichtenfunktion ist nicht installiert.' }
  Write-Output 'WINDOWS_MESSAGES_SELFTEST_OK'
  return
}
if (!(Test-Path -LiteralPath $python -PathType Leaf) -or
    !(Test-Path -LiteralPath $db -PathType Leaf)) {
  throw 'Kleinanzeiger zuerst regulär einrichten und eigene Anzeigen abgleichen.'
}
if (!(Test-Path -LiteralPath $keyFile -PathType Leaf)) {
  throw 'Konto-API noch nicht eingerichtet. Im Kleinanzeiger-Fenster Nachrichten-Zugang einrichten.'
}

# DPAPI: the secret remains in the current user's Windows credential context.
# No command-line argument, diagnostic message or URL ever contains the key.
$secure = Get-Content -LiteralPath $keyFile -Raw | ConvertTo-SecureString
$credential = [PSCredential]::new('kleinanzeigen-agent',$secure)
try {
  $env:KLAZ_API_KEY = $credential.GetNetworkCredential().Password
  if ($env:KLAZ_API_KEY -notmatch '^klaz_live_[A-Za-z0-9_-]{8,200}$') {
    throw 'Gespeicherter Konto-API-Schlüssel ist ungültig. Zugang erneut einrichten.'
  }
  # This process is intentionally independent of the managed Chrome/CDP profile.
  # Opening this local page alone never fetches paid chats or sends a message.
  & $python -m mark_api.platform_messages --db $db --outbox $journal --enable-account-api --port 8773
  if ($LASTEXITCODE -ne 0) { throw 'Nachrichtenfenster konnte nicht gestartet werden.' }
} finally {
  Remove-Item Env:KLAZ_API_KEY -ErrorAction SilentlyContinue
  $credential = $null
  $secure.Dispose()
}
