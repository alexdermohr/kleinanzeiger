# Schnäppchenjäger – lokale Angebote vergleichen

Der Kaufbereich ist unabhängig von der Verwaltung eigener Anzeigen. Die erste
Stufe ist ein **lokales, read-only Analysewerkzeug** für vom Nutzer bereitgestellte
Inseratdaten. Sie greift weder auf Kleinanzeigen noch auf das eigene Konto zu.

**Status:** Das Windows-Kontrollfenster öffnet unter **Weitere Aktionen → Schnäppchenjäger**
eine eigene Oberfläche. Im eigentlichen Web-Dashboard und als automatische
Angebotssuche ist die Funktion noch nicht integriert.

## Direkt unter Windows

1. Kleinanzeiger öffnen und **Weitere Aktionen → Schnäppchenjäger** wählen.
2. Das exakte Modell eingeben und mit **Suche im Browser öffnen** eine normale
   Kleinanzeigen-Suchseite öffnen. Der Browser überträgt keine Inserate zurück.
3. Angebote aus einer selbst gewählten zulässigen Quelle als CSV oder JSON
   speichern, **Datei wählen** und **Datei lokal auswerten** anklicken.
4. Der Bericht öffnet sich im Browser. Er bleibt im Unterordner **Berichte** des
   lokalen Kleinanzeiger-Anwendungsdatenordners erhalten. Ohne mindestens vier
   weitere passende Vergleichsangebote bleibt die Bewertung unbestimmt.

Dies benötigt keinen KI-Schlüssel. Es gibt keine automatisierte
Marktplatzabfrage, keine Kontozugriffe, keinen Versand und keine Käufe.

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
unbewertet bleiben. Das gilt auch für Smartphone-Speichergrößen (z. B.
Samsung S23 128GB gegenüber 512GB) und Editionen (z. B. S23 gegenüber Ultra,
Pixel 9 gegenüber Pro oder Redmi Note 13 gegenüber Pro).
Erkannte Zubehörangebote wie „Hülle für iPhone 14“ sowie Defekthinweise im
Titel oder Zustandsfeld werden ausgeschlossen. Zustände werden nur bei
identischer Angabe verglichen; „unbekannt“ bildet eine eigene Gruppe.
Ein Geräteangebot mit OVP bleibt für den Vergleich zugelassen.
Referenz ist der Median ihrer beobachteten **Angebotspreise**, nicht der
erzielte Verkaufspreis. Jeder Kandidat wird aus seiner Vergleichsgruppe
herausgenommen (einschließlich weiterer Einträge derselben Inserat-ID).
Varianten und Zustände werden einmal gruppiert und ihre Preise sortiert;
der Median ohne eigenen Preis wird ohne erneuten Vollvergleich ermittelt,
auch bei 10.000 Angeboten. Fehlende Versandkosten oder der Zustand werden markiert.
Ein deutlich günstiger Preis bedeutet ausdrücklich weder einen echten
Schnäppchenkauf noch einen sicheren Verkäufer. Ein Inserat mit 0 EUR wird
auch bei positivem Versandpreis nicht als Vergleichspreis genutzt;
explizit unterschiedliche Terabyte-Speichergrößen bleiben getrennt.

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
