# Schnäppchenjäger – lokale Bewertungsbasis (Entwicklungsstand)

Der Kaufbereich ist unabhängig von der Verwaltung eigener Anzeigen. Die erste
Stufe ist ein **lokales, read-only Analysewerkzeug** für vom Nutzer bereitgestellte
Inseratdaten. Sie greift weder auf Kleinanzeigen noch auf das eigene Konto zu.

**Status:** Modul und Kommandozeilenbericht implementiert. Die Integration in
das normale Windows-Dashboard, die automatische Angebotszufuhr und eine
Kundenabnahme sind **noch nicht implementiert**. Keine Versionserhöhung ohne
funktionierenden Kundenweg.

## Ein Angebot vergleichen

Die Vergleichsgruppe muss aus demselben **exakten Modell und derselben
Generation** bestehen. Beispiel:

```powershell
python -m mark_api.bargain_hunter_cli --suchlink --modell "Lenovo ThinkPad T14 Gen 3" --hoechstpreis 500
python -m mark_api.bargain_hunter_cli --datei angebote.json --modell "Lenovo ThinkPad T14 Gen 3" --bericht schnäppchenbericht.html --oeffnen
```

Das erste Kommando zeigt einen normalen Suchlink. Angebote aus einer
eigenständig zulässigen Quelle werden manuell als CSV oder JSON bereitgestellt;
die Anwendung ruft die Website nicht automatisiert ab.

Beispiel `angebote.csv`:

```csv
id,title,price,url,condition,shipping_eur,observed_at,source
1234567890,Lenovo ThinkPad T14 Gen 3,300,https://www.kleinanzeigen.de/s-anzeige/beispiel/1234567890,gebraucht,7,2026-11-08T08:00:00+01:00,Manueller Export
```

Benötigt werden mindestens `id`, `title`, `url` und ein Preis zur Bewertung.
`price` darf in EUR als Zahl oder `299,00 €` vorliegen. Auch JSON-Listen und
die Struktur `{"data":{"ads":[...]}}` sind möglich; für die letztere Struktur
werden z. B. `ad_id`, `title`, `ad_url` und `price.amount` unterstützt.
Die Daten werden weder hochgeladen noch aus dem Internet nachgeladen.

Eine Liste aus nur einem Angebot ergibt ausdrücklich **keine Bewertung**.
Mindestens vier andere ausreichend vergleichbare Anzeigen sind erforderlich.
Die Bewertung trennt anhand im Titel belegbarer Angaben zusätzlich Prozessor,
Arbeitsspeicher und Speichergröße. Unbekannte Ausstattungen werden nicht als
nachweislich identische Varianten angenommen; dadurch können Treffer bewusst
unbewertet bleiben.
Referenz ist der Median ihrer beobachteten **Angebotspreise**, nicht der
erzielte Verkaufspreis. Jeder Kandidat wird aus seiner Vergleichsgruppe
herausgenommen. Fehlende Versandkosten oder der Zustand werden markiert.
Ein deutlich günstiger Preis bedeutet ausdrücklich weder einen echten
Schnäppchenkauf noch einen sicheren Verkäufer.

Die Ausgabe ist ein lokaler HTML-Bericht mit verlinkten Angeboten, den
beobachteten Quellen, dem Zeitpunkt, dem Median, der Stichprobengröße und
der Unsicherheit. `--json` gibt dieselbe Bewertung maschinenlesbar aus.

## Datenquellen und Grenzen

- Kleinanzeigen erlaubt gespeicherte Suchen und E-Mail-Benachrichtigungen.
  Eine Übernahme dieser Benachrichtigungen ist noch nicht implementiert.
- Die Nutzungsbedingungen untersagen das automatisierte Sammeln von
  Inhalten ohne ausdrückliche schriftliche Zustimmung. Deshalb kein direkter
  Crawler, keine fremden Login-Sitzungen und keine CAPTCHA-Umgehung.
- Drittanbieter-APIs können JSON liefern, sofern deren Berechtigung,
  Vertragsbedingungen und Kosten vor Nutzung gesondert geklärt sind.
  Eine solche API ist **nicht** automatisch aktiviert und kein Key nötig.
- Automatische Käufe, Nachrichten und andere Plattformaktionen sind
  nicht Teil der Bewertungsbasis.

## Tests

```sh
python3 -m unittest discover -s tests -p test_bargain_hunter.py
```

Die Testdatei verwendet den Repo-`src`-Pfad; der Test benötigt keine
Netzwerkverbindung und keine produktiven Anmeldedaten.