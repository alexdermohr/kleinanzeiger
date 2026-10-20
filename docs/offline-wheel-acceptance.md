# Mark: installierte Offline-Wheel-Abnahme

Die Prüfung installiert das tatsächlich gebaute Wheel in einem temporären
Python-3.12-venv. Das Produkt startet als separater lokaler Prozess mit
synthetischen EML-Nachrichten, SQLite und Loopback-HTTP, ohne Quellimporte.

## Lokaler Test bei erlaubten unprivilegierten Usernamespaces

Das gebaute Wheel auswählen und die Python-Tests nur in einer User- und
Netzwerknamespace starten. In diesem Beispiel wird kein Host-root benötigt:

```sh
python -m pip wheel --no-deps --wheel-dir /tmp/mark-wheels .
wheel=/tmp/mark-wheels/mark_api-0.1.0-py3-none-any.whl
python_bin="$(command -v python)"
unshare --user --map-root-user --net -- bash -c '
  set -euo pipefail
  ip link set lo up
  exec env "MARK_WHEEL_PATH=$1" "$2" -m unittest discover -s tests -p offline_wheel_acceptance.py -v
' bash "$wheel" "$python_bin"
```

Einige GitHub-Runner verweigern unprivilegierte Usernamespace-UID-Mappings.
Ein fehlgeschlagener Namespace-Test darf **nicht** ohne Isolation neu laufen.

## GitHub-Actions-Sicherheitsgrenze

Der CI-Workflow verwendet auf solchen Runnern eine andere, gleichfalls
kernelgebundene Variante: Nur die festen Setup-Befehle laufen als Host-root,
um eine neue Netzwerk-, PID- und Mountnamespace anzulegen und lo zu aktivieren.
Unmittelbar vor dem ersten Test-/Wheel-Python-Prozess setzt setpriv die
unprivilegierte Runner-UID/GID, löscht Zusatzgruppen, alle Linux-Capabilities
(einschließlich der Bounding-Menge) und aktiviert PR_SET_NO_NEW_PRIVS.
Dies verhindert eine spätere sudo- oder setuid-Eskalation und einen setns()
zur Host-Netzwerknamespace. Ein host-root-runuser ohne diese Kernel-Guards
wäre dagegen nicht ausreichend.

Die Prüfung selbst verifiziert die Nichteskalation:

- Entweder Usernamespace-UID0 ist auf einen nichtroot-Hostnutzer gemappt,
  oder die effektive UID ist nichtroot, NoNewPrivs ist 1 und sämtliche
  Capability-Mengen sind null.
- Es ist nur lo als Netzwerkschnittstelle sichtbar.
- Der Socket-Routenversuch zur reservierten Testadresse 192.0.2.1
  ergibt ENETUNREACH statt eines Netzwerkkontakts.
- Ein fehlendes MARK_WHEEL_PATH führt zum Testfehler statt zu SKIP.
- GitHub CI verlangt explizit Ran 1 test, OK und das JSON-Resultat PASS.
- Der Launcher-Banner wird nonblocking und mit monotonic-Deadline geprüft.

Der Wheel-Build mit pip erfolgt *vor* der isolierten Laufzeitprüfung;
das Nachladen der Build-Abhängigkeiten ist nicht Teil des Offline-Run-Claims.

## Geprüfte Funktionen und Freigabegrenze

Der E2E-Pfad deckt lokale Datenbankerstellung nur auf ausdrückliche Option,
EML-Import/Deduplizierung, echte Health-/Ready- und Dashboard-Routen,
Analytics/CSV, lokale Stadtklassifikation und SQLite-quick_check ab.
HTTP-Schreibversuche und deaktivierte Plattformrouten müssen abgewiesen werden.

Ein PASS gilt ausschließlich für die Offline-Read-only-Laufzeit des getesteten
Wheels. Plattformberechtigung, CDP-Synchronisierung, KI-Kostenkontrolle,
A6-Recovery und abschließende Produktfreigabe benötigen eigene Nachweise.