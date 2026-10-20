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

```sh
MARK_WHEEL_PATH=/tmp/mark-wheels/mark_api-0.1.0-py3-none-any.whl python -m unittest discover -s tests -p test_offline_wheel_acceptance.py -v
```

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
