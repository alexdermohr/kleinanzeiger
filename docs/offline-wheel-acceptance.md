# Mark: installierte Offline-Wheel-Abnahme

Der Test prüft die **installierte Distribution** in einem eigenständigen
Python-3.12-venv. Er startet den echten lokalen Mark-Prozess und nutzt nur
synthetische EML-Dateien, eine temporäre SQLite-Datenbank sowie Loopback-HTTP.
Der Quellcheckout wird nicht als importiertes Produkt verwendet.

## Ausführung unter Linux

Das Paket bauen und den tatsächlich gebauten Wheelpfad verwenden:

```sh
python -m pip wheel --no-deps --wheel-dir /tmp/mark-wheels .
wheel=/tmp/mark-wheels/mark_api-0.1.0-py3-none-any.whl
python_bin="$(command -v python)"
unshare --user --map-root-user --net -- bash -c '
  set -euo pipefail
  ip link set lo up
  exec env "MARK_WHEEL_PATH=$1" "$2" -m unittest discover -s tests -p test_offline_wheel_acceptance.py -v
' bash "$wheel" "$python_bin"
```

**Sicherheitsgrenze:** Mit `unshare --user --map-root-user --net` wird
der scheinbare UID 0 der Testumgebung **nur** auf die unprivilegierte Host-UID
abgebildet. Die isolierte Netzwerknamespace enthält ausschließlich `lo`.
Der Test prüft beide Voraussetzungen und den Kernel-Routenfehler
`ENETUNREACH` beim Verbindungsversuch zu einer reservierten
Dokumentationsadresse. Aus einer sudo-gestarteten Netznamespace ließe sich
bei entsprechender Host-Berechtigung sonst wieder in die Host-Netznamespace
wechseln; eine solche Ausführung ist deshalb **nicht** zugelassen.

Falls unprivilegierte User-/Netzwerk-Namespaces auf dem Testhost nicht zulässig
sind, muss die Abnahme **fehlschlagen**. Sie darf dann weder per sudo-only
Netznamespace noch ganz ohne Isolation als PASS ausgegeben werden.

Die eigenständige GitHub-Actions-Pipeline
`.github/workflows/offline-wheel-acceptance.yml` verwendet dieselbe
Isolation. Sie verlangt explizit einen ausgeführten Test, den abschließenden
`OK`-Status und das protokollierte JSON-Resultat `"PASS"`.
Fehlendes `MARK_WHEEL_PATH`, Test-Skip, Null-Testlauf und Fehlstart
sind kein Release-PASS; der Startprozess hat einen begrenzten, nichtblockierenden
12-Sekunden-Bereitschaftscheck.

## Abgedeckte Fälle

- Installation des Wheels ohne Zugriff auf Paket-Repositories und ohne
  Python-Importe aus dem Quellcheckout
- Fehlende SQLite-Datenbank startet ohne ausdrückliches init-db nicht
- Echter Loopback-HTTP-Server, Health-/Ready-Endpunkte und deaktivierte
  Schreiboberfläche
- Zwei synthetische EML-Nachrichten, Anzeigenreaktionen, Analytics und
  UTF-8-CSV
- Lokale Stadtkategorisierung, CSV-Gruppierung, Neustart und Deduplizierung
- Schreib-/unbekannte Plattformrouten bleiben gesperrt; SQLite quick_check

Ein PASS gilt nur für den **Offline-Read-only-Produktpfad der getesteten
Revision**. Er ersetzt weder rechtmäßige Plattform-Entitlements noch sichere
CDP-/Synchronisierung, KI-Anbieter-/Kostenkontrollen, A6-Host-Recovery oder
die vollständige Produktfreigabe.
