# Abnahme: Installiertes Mark-Offline-Wheel

Das Wheel wird in einer isolierten Python-3.12-Umgebung installiert und als eigener
Prozess mit dem offiziellen lokalen Einstiegspunkt gestartet. Die Tests verwenden
ausschließlich synthetische E-Mail-Dateien. Es gibt keinen Zugriff auf die
Kleinanzeigen-Plattform oder einen KI-Provider.

## Lokaler Ablauf

Wheel bauen:

```sh
python -m pip wheel --no-deps --wheel-dir /tmp/mark-wheels .
```

Den tatsächlich erzeugten Wheelnamen in MARK_WHEEL_PATH einsetzen:

Nur unter Linux mit `sudo`, `unshare`, `ip` und `runuser` ausführen.
`unshare --net` legt einen separaten Netzwerkraum ohne ausgehende
Netzwerkschnittstellen an; ausschließlich `lo` wird aktiviert. Der
Python-Test wird anschließend mit normalen Nutzerrechten gestartet.
Ohne diese Isolation **schlägt der Test absichtlich fehl**:

```sh
wheel=/tmp/mark-wheels/mark_api-0.1.0-py3-none-any.whl
runner_user="$(id -un)"
python_bin="$(command -v python)"
sudo -n unshare --net -- bash -c '
  set -euo pipefail
  ip link set lo up
  exec runuser -u "$1" -- env "MARK_WHEEL_PATH=$2" "$3" -m unittest discover -s tests -p test_offline_wheel_acceptance.py -v
' bash "$runner_user" "$wheel" "$python_bin"
```

Der Test prüft zusätzlich per Kernel-Netzwerkschnittstellenliste, dass nur
\`lo\` erreichbar ist. Proxy- und Token-Umgebungsbereinigung ist lediglich
eine zweite Schutzschicht und ersetzt die Netzwerknamespace nicht.

Die eigenständige GitHub-Actions-Pipeline `offline-wheel-acceptance.yml`
führt dieselbe Prüfung nach dem Wheel-Build aus.

## Nachweisumfang

- Separater Venv, isolierter Modulimport aus site-packages
- Fehlende Datenbank verweigert Start ohne ausdrückliches init-db
- Echtes Loopback-HTTP mit Health- und Ready-Endpunkten
- Zwei lokal synthetisierte Nachrichten, SQLite, eindeutige Zählung
- Anzeigenanalyse, UTF-8-CSV, lokale Stadtkategorisierung
- HTTP-Schreibversuche und unbekannte Write-Routen werden abgelehnt
- Neustart, deduplizierter Import und SQLite-Integritätskontrolle

PASS gilt ausschließlich für den **Offline-Read-only-Produktpfad** der
getesteten Wheel-Revision. Platform-Entitlement, generative Kosten,
CDP-Synchronisation, A6-Wiederherstellung und vollständige
Produktabnahme bleiben getrennte Freigabegates.