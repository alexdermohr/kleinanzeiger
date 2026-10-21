# Mark lokal starten (ohne Plattformzugriff)

Der Standardstart `python -m mark_api` verwendet ausschließlich die bestehende
Offline-Laufzeit. Er benötigt weder Chrome/CDP noch Zugangsdaten oder eine
Kleinanzeigen-Plattformberechtigung. Er startet keinen Browser und führt
keinerlei automatische Online-Abfragen, Anzeigenänderungen oder Publikationen
aus.

Installation in einer Python-3.12+-Umgebung:

```bash
python -m pip install .
python -m mark_api --help
python -m mark_api --db "$HOME/mark.sqlite" --init-db
```

Bei späteren Starts das einmalige `--init-db` weglassen. Der neue SQLite-Speicher
wird ausschließlich bei ausdrücklicher Angabe dieser Option angelegt.

Wer eine oder mehrere bereits lokal vorhandene, vertrauenswürdig beschaffte
Kleinanzeigen-E-Mail-Benachrichtigungen (`.eml`) auswerten möchte:

```bash
python -m mark_api --db "$HOME/mark.sqlite" --email "$HOME/Downloads/nachricht.eml"
```

Anschließend zeigt die CLI die lokale Dashboard-Adresse an, standardmäßig
`http://127.0.0.1:8765/`. Der Host ist ausschließlich Loopback. Der Import
ist dedupliziert, und Anzeigenreaktionen/Analytics erscheinen nur, soweit
durch lokale Daten belegt. CSV-Exporte sind lokal. Schreib-HTTP-Routen und
spezielle Browser-/CDP-Transportpfade werden nicht aktiviert.

## Sicherung der lokalen SQLite-Daten

Mark und sämtliche anderen Schreiber zunächst regulär stoppen. Nur für eine
bereits vorhandene Datenbank und ein **noch nicht existierendes** Sicherungsziel
ist der installierte, explizit ausgelöste Offline-Backup-Befehl vorgesehen:

```bash
mark-api-backup --db "$HOME/mark.sqlite" --backup "$HOME/mark-sicherung.sqlite"
```

Die Sicherung enthält auch offene beziehungsweise unklare Write-Journale.
Nicht über eine laufende Datenbank kopieren und bei unklarem Ergebnis keinen
blinden Wiederholungsversuch starten. Der Befehl ersetzt **nicht** die noch
fehlende unabhängige Host-/A6-Recovery-Freigabe. Siehe
[Backup, Fehler und Wiederherstellung](offline-backup.md).

## Opt-in-Text- und Bildentwürfe

Die Offline-Text- und Bildgenerierung ist bereits Teil des installierbaren
Produkts. Sie bleibt beim normalen Start deaktiviert. Für ihre bewusste
Aktivierung ist ein separat installierter SDK-Zusatz und ein eigenes,
berechtigtes Providerkonto erforderlich:

```bash
python -m pip install '.[generation]'
# OPENAI_API_KEY nur privat in der Prozessumgebung bereitstellen.
python -m mark_api --db "$HOME/mark.sqlite" --enable-generation
```

Auch die Aktivierung selbst ruft noch keinen Provider auf. **Jede einzelne**
KI-Anfrage erfordert zusätzlich eine ausdrückliche Bestätigung zu
Datenübertragung und möglichen Kosten. Ergebnisse sind ungeprüfte,
unveröffentlichte Entwürfe; es erfolgt keine automatische Anzeigenänderung.
Sitzungstoken aus der generierten Dashboard-Adresse vertraulich behandeln.
Details: [Text- und Bildentwürfe](generation.md).

**Klare Grenzen:** Das ist ein funktionierender lokaler Teil von Mark,
nicht der vollständige automatisch mit Kleinanzeigen synchronisierende
Produktbetrieb. Ein eingeloggter Browser und CLI-Flags erteilen keine
Plattform-Automatisierungserlaubnis. Bis zum Nachweis individueller
Kontoberechtigung und eines sicheren offiziellen Transports bleiben
automatischer Besitzerabgleich, Plattformschreibaktionen und die
PR-127-/PR-133-Laufzeiten gesperrt. Die sichere unabhängige
Host-Wiederherstellung ist ebenfalls noch nicht abgenommen.
