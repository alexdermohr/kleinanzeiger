# Kleinanzeiger v0.1.1 – Windows 11 (nativ, ohne WSL)

Windows 11 x64, Python 3.12 oder 3.13 und Google Chrome werden benötigt.
Kein WSL, Ubuntu oder Docker erforderlich.

## Installation

1. Python von https://www.python.org/downloads/windows/ mit Python Launcher
   (py.exe) installieren, falls noch nicht vorhanden.
2. Das Windows-ZIP herunterladen und vollständig entpacken.
3. Installieren.cmd doppelklicken: Das SHA256-geprüfte Wheel wird unter
   %LOCALAPPDATA%\Kleinanzeiger\.venv installiert.
4. Starten.cmd doppelklicken: Ein neues, isoliertes Chrome-Profil öffnet sich
   nur mit dem Debugging-Port 127.0.0.1:9222.
5. Im neu gestarteten Chrome selbst anmelden, nur die eigene
   Anzeigenverwaltungsseite offen halten, im Fenster ENTER drücken.
6. Die anschließend angezeigte lokale Dashboard-URL im Browser öffnen.

Das Produkt bietet Synchronisation, Anzeigenverwaltung mit Create/Publish
inklusive Bildern, Titel/Beschreibung, Pause/Aktivierung, Löschen, Kennzahlen,
Historie, lokale Reaktions-E-Mails und KI-Entwürfe.

## Daten und erneuter Start

Die SQLite-Datenbank liegt unter %LOCALAPPDATA%\Kleinanzeiger\anzeigen.sqlite.
Das Skript initialisiert sie nur bei einer wirklich neuen Installation.
Nachfolgende Starts verwenden denselben Datenbestand. Keine Altbestandsdateien
einschließlich -wal/-shm ungeprüft in den Ordner kopieren.

## Windows-Datensicherung und Wiederherstellung

Im Kundenordner **Sichern.cmd** doppelklicken. Die SQLite-Sicherung wird
über die SQLite-Online-Backup-API einschließlich bereits bestätigter WAL-
Transaktionen erstellt und auf Schema, Integrität und SHA-256 kontrolliert.
Sicherung und zugehörige `.manifest.json`-Datei liegen unter
`%LOCALAPPDATA%\Kleinanzeiger\Backups`; beide Dateien zusammen behalten.
Offene beziehungsweise unklare Write-Operationen bleiben als solche
im Backup erhalten und werden nicht automatisch erneut ausgeführt.

**Wiederherstellen.cmd** nur verwenden, wenn Kleinanzeiger vollständig
beendet ist und die Ziel-Datenbank `anzeigen.sqlite` **nicht** mehr
vorhanden ist. Auch zugehörige `-wal`, `-shm` und `-journal`-Dateien
dürfen nicht vorhanden sein. Vorhandene Dateien nie automatisch ersetzen.
Die Wiederherstellung verlangt Dateiauswahl und die Eingabe `JA`,
prüft Backup und Manifest und erstellt ausschließlich eine neue SQLite-Datei.
Unbekannte oder eventuell kompromittierte Altbestände sind davon
nicht freigegeben. Nach erfolgreicher Wiederherstellung normal über
**Starten.cmd** starten, ohne `--init-db`.

## Optionale KI-Funktionen

Die KI-Python-Abhängigkeiten sind beim Windows-Kunden-Wheel optional.
Mit eigenem API-Schlüssel (OPENAI_API_KEY) und Startoption
--enable-generation können Texte und Bilder als bearbeitbare Entwürfe
generiert werden. Jeder kostenpflichtige Aufruf erfordert Einzelbestätigung;
eine Generierung veröffentlicht keine Anzeigen automatisch.

## Grenzen

Windows nutzt eigene betriebssystemweite CDP- und SQLite-Exklusivsperren.
Dies ist die normale Vollproduktinstallation und keine Linux-A6-
Root-/Mehr-UID-/Landlock-Provisionierung. Sichere Altbestandsmigration bleibt
gesperrt: https://github.com/alexdermohr/kleinanzeiger/issues/76

Der CDP-Port darf keinesfalls im Netzwerk exponiert werden. Nur das separate
Chrome-Profil verwenden. Es werden keine bestehenden Chrome-Profile übernommen
und weder Login noch MFA oder CAPTCHA automatisiert.

Die Produktkette wird mit synthetischen Plattform- und Providergegenstellen
getestet, ohne echte Anzeigenveröffentlichungen oder kostenpflichtige
KI-Aufrufe. Erforderliche Rechte des Kundenaccounts sind gesondert zu prüfen.