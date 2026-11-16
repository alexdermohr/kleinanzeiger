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

## Wiederholbares lokales Schnäppchenradar

Das Radar läuft unter Windows und Linux über dieselbe Python-CLI. Ein vollständiges
Profil liegt in `examples/bargain-radar.json` (exakt **Lenovo ThinkPad T14 Gen 3**).
Profil in einen eigenen Arbeitsordner kopieren, daneben `eingang` anlegen und
berechtigt erhaltene CSV-/JSON-Listen im oben beschriebenen Format hineinlegen.
Alle Profilpfade beziehen sich auf den Profilordner, unabhängig vom Arbeitsverzeichnis.
`input` kann alternativ eine einzelne CSV-/JSON-Datei sein. `json_report` ist optional.
Ausgabeordner müssen bereits existieren; Ausgaben gehören außerhalb des Eingangsordners.

```sh
python -m mark_api.bargain_hunter_cli --radar /absoluter/pfad/radar.json
```

Für Cron beispielsweise (Python aus der installierten Umgebung verwenden):

```cron
*/30 * * * * /pfad/venv/bin/python -m mark_api.bargain_hunter_cli --radar /pfad/radar.json >> /pfad/radar.log 2>&1
```

Dasselbe Kommando eignet sich für systemd-Timer oder die Windows-Aufgabenplanung.
Es öffnet keinen Browser und fragt keine Website ab. Exitcode 0 bedeutet Erfolg,
2 einen Eingabe-, Datei- oder Datenbankfehler, auch bei einer Sperre nach etwa einer
Sekunde Wartezeit. Der nächste Timer-Lauf darf erneut versuchen.

Die Historie wird ausschließlich neu als eigene Radar-Datei angelegt. Niemals eine
Verkäufer-Datenbank als `history` angeben; vorhandene fremde Datenbanken werden
abgewiesen. Eine Historie ist an Modell und Bewertungsgrenzen gebunden; für ein anderes
Modell ein eigenes Profil mit eigener Historie und eigenen Berichten verwenden.
`min_peers` (3–100, Standard 4) und `discount_pct` (größer 0, kleiner 100, Standard 30)
steuern die unveränderte `assess_offers`-Bewertung.

Der erste Lauf meldet neue Inserate, ein identischer Zweitlauf null neue Treffer.
Änderungen von Angebotspreis oder Versandkosten werden mit Vorwerten gemeldet.
Reine Titel-/Quellenänderungen erzeugen keinen neuen Preistreffer. IDs sind die
Identität; fehlende Inserate werden nicht als verkauft interpretiert. Auch Preissteigerungen
werden protokolliert. JSON auf stdout und optional in `json_report` enthält Zähler und
Bewertungen der Änderungen; HTML zeigt zusätzlich Quelle, Beobachtungszeit,
Vergleichsmedian, Peer-Zahl und Unsicherheiten. Nicht zum Modell passende Inserate
werden gespeichert, aber nicht bewertet.

Pro Lauf werden nur die aktuell eingelesenen Angebote als Vergleichsgruppe genutzt,
keine angesammelten historischen Preise. Den Eingang deshalb als aktuellen Bestand
pflegen: Dateien werden weder verschoben noch gelöscht. Unterordner werden ignoriert.
Bei mehreren Dateien gewinnt für dieselbe ID die neueste zeitzonenbehaftete ISO-Zeit;
widersprüchliche und zeitlich nicht unterscheidbare Einträge werden abgewiesen.
Ältere datierte Beobachtungen überschreiben die Historie nicht und werden auch
nicht als Vergleichsangebote dieses Laufs verwendet. Für fortlaufende
Preisänderungen möglichst immer `observed_at` mit Zeitzone angeben. Ohne Zeit gilt
die aktuelle Datei als neue Beobachtung, sofern auch die Historie undatiert ist;
undatierte Daten überschreiben keine datierte Beobachtung; bei gleichem datiertem Zeitpunkt sind
abweichende Preise ein Fehler. Die Eingaben belegen weder Aktualität noch Echtheit;
alte Dateien müssen vom Nutzer entfernt bzw. aktualisiert werden.

Ein Lauf validiert alle Eingaben vor Historienänderungen. SQLite serialisiert Läufe
mit derselben Historie. Fehler rollen Historienänderungen zurück; Berichte werden je
Datei atomar ersetzt. Datenbank und Berichtsdateien bilden keine gemeinsame atomare
Einheit: Bei Abbruch zwischen den Ersetzungen können Berichte auseinanderliegen.
Nach einem fehlgeschlagenen Lauf erneut ausführen; erfolgreiche Laufberichte liegen
zusätzlich als JSON in der separaten Tabelle `radar_runs`. Es gibt keine automatische
Bereinigung dieser Historie. Unterschiedliche Historien dürfen keine Berichtspfade teilen.

Keine Verkaufspreisprognose, Kaufgarantie, automatische Beschaffung oder E-Mail-Übernahme.
Ohne genügend Peers lautet das Ergebnis ausdrücklich „nicht bewertbar“.

Radar- und Regressionstests mit ausschließlich synthetischen Daten:

```sh
python -m unittest discover -s tests -p 'test_bargain*.py'
```
