# Mark lokal starten (ohne Plattformzugriff)

Der Standardstart `python -m mark_api` verwendet ausschließlich die bestehende
Offline-Laufzeit. Er benötigt weder Chrome/CDP noch Zugangsdaten oder eine
Kleinanzeigen-Plattformberechtigung. Er startet keinen Browser und führt
keinerlei automatische Online-Abfragen, Anzeigenänderungen oder Publikationen
aus.

Installation in einer Python-3.12-Umgebung:

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

**Klare Grenzen:** Das ist ein funktionierender lokaler Teil von Mark,
nicht der vollständige automatisch mit Kleinanzeigen synchronisierende
Produktbetrieb. Ein eingeloggter Browser und CLI-Flags erteilen keine
Plattform-Automatisierungserlaubnis. Bis zum Nachweis individueller
Kontoberechtigung und eines sicheren offiziellen Transports bleiben
automatischer Besitzerabgleich, Plattformschreibaktionen und die
PR-127-/PR-133-Laufzeiten gesperrt. Generative Text- und Bildvorschläge
sind ein unabhängiger, separat freizuschaltender Produktbaustein
(siehe `docs/generation.md`, sobald dieser gemergt ist).
