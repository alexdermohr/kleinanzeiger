# Schnäppchenjäger – automatische SACD-Suche und lokale Angebotsvergleiche

Der Kaufbereich ist unabhängig von der Verwaltung eigener Anzeigen. **Linux/KDE**
sucht nach Aktivierung automatisch öffentlich angebotene silberne SACD-Player,
führt eine dauerhafte Kandidatenhistorie und meldet neue Inserate oder wirkliche
Preisänderungen. **Windows** bietet weiterhin die lokale CSV-/JSON-Bewertung
und manuell zu öffnende Suchlinks. Der Kaufbereich greift nicht auf ein eigenes
Verkäuferkonto zu und führt keine Käufe aus.

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

## Datenquellen und Betriebsweise

- Kleinanzeigen erlaubt gespeicherte Suchen und E-Mail-Benachrichtigungen.
  Ein begrenzter lokaler EML-Pfad ist unten beschrieben; echte Templates sind nicht verifiziert.
- Auf Linux/KDE kann die separate öffentliche SACD-Suche nach Aktivierung
  ohne E-Mail-Zulauf arbeiten. Sie liest eine fest eingestellte Suchseite
  maximal alle 30 Minuten und erhält bei Abruffehlern den letzten Bericht.
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
Im EML-Modus wird ein zuvor importiertes günstiges Angebot genau dann einmalig nachgemeldet, wenn später eintreffende aktuelle Vergleichsangebote erstmals eine belastbare Bewertung ermöglichen. Gleichbleibende Preisversionen werden nicht wiederholt alarmiert.
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

Keine Verkaufspreisprognose, Kaufgarantie oder automatische Beschaffung.
Ohne genügend Peers lautet das Ergebnis ausdrücklich „nicht bewertbar“.

Radar- und Regressionstests mit ausschließlich synthetischen Daten:

```sh
python -m unittest discover -s tests -p 'test_bargain*.py'
```

## Lokale Suchmail-Dateien (begrenzter Parser)

Es liegen keine echten Suchmail-Vorlagen aus Nutzer-Gmail vor. Diese Erweiterung
behauptet keine Kompatibilität mit offiziellen Mailtemplates. Sie benötigt weder
Gmail-/IMAP-Anmeldung noch Netzwerk, KI, Scraper oder Drittanbieterbibliotheken.
Der Chatmail-Importer bleibt getrennt. HTML wird nur mit `HTMLParser` gelesen,
niemals gerendert; Skripte, Bilder, Anhänge und komplexe Layouts werden abgewiesen.

`examples/search-mail-radar.json` in einen eigenen Profilordner kopieren, dort
`suchmails` anlegen. Ausschließlich ausdrücklich erlaubte eigene lokale `.eml`
hineinlegen. Keine Verkäufer-Datenbank verwenden. Originaldateien werden ausschließlich gelesen. Für jedes Modell eigene Ordner, Profile, Historie
und Berichtspfade verwenden. Keine parallelen Profile mit gemeinsamen Ausgaben.

Unterstützt ist genau **ein Angebot pro Mail**:

```text
Lenovo ThinkPad T14 Gen 3
100 EUR
https://www.kleinanzeigen.de/s-anzeige/thinkpad/1234567890-278-1234
```

Oder genau dieses HTML-Fragment (ohne weitere Elemente/Attribute):

```html
<a href="https://www.kleinanzeigen.de/s-anzeige/thinkpad/1234567890-278-1234">Lenovo ThinkPad T14 Gen 3</a><p>100 EUR</p>
```

Multipart darf dieselbe Zuordnung als Text und HTML enthalten; Abweichungen
werden übersprungen. Keine automatische Extraktion aus beliebigen Newslettern.
Nur direkte HTTPS-Inseratlinks ohne Query/Fragment, numerische aus dem Link
abgeleitete IDs, positive EUR-Festpreise. VB, kostenlos, andere Währungen,
Trackinglinks, falsche Produktgruppen und unklare Zuordnungen werden diagnostiziert.
Preiswidersprüche gleicher ID/Zeit brechen den Lauf vor Historienänderungen ab.
Neuere bestätigte Beobachtungen dürfen ältere ersetzen.

Der im Profil einmalig ausgewählte lokale Suchmailordner ist die Freigabe der
Eingangsquelle. Neue EML-Dateien darin werden beim nächsten Lauf ohne
Einzelfreigabe eingelesen. Eine automatische Mailzustellung kann über ein
vom Nutzer eingerichtetes Mailprogramm in diesen Ordner erfolgen; Kleinanzeiger
verbindet sich selbst nicht mit Gmail oder IMAP.

Der Absenderheader muss auf eine kleinanzeigen.de-Adresse oder Subdomain
lauten. Zusammen mit dem Date-Header und der direkten Inserat-URL ist dies nur
eine Plausibilitätsprüfung, kein Echtheits- oder DKIM-Nachweis. Ein Datum mit
Zeitzone muss aktuell sein; alte kopierte Mails werden nicht als aktuelle
Angebote eingestuft. Bei zweifelhaften Daten erscheint Importdiagnostik
statt eines Schnäppchenalarms. Vor einem Kauf den tatsächlichen Inseratpreis
und den Verkäufer prüfen.

Alte EML-Dateien außerhalb des Zeitfensters können im Eingangsordner verbleiben: sie werden anhand ihres lokalen Dateialters übersprungen. Der Ordner darf wachsen, ohne nach 100 alten Dateien die Analyse zu blockieren.

Standardmäßig maximal 48 Stunden alte Beobachtungen (konfigurierbar 1–168), keine
zukünftigen/undatierten Daten, höchstens 100 Dateien mit je 1 MB und 20 MIME-Teilen.
Unterordner werden ignoriert; das Limit von 100 Dateien gilt für aktuelle EMLs. Keine optionalen CSV/JSON-Referenzen in diesem Modus;
alle Peers müssen denselben Aktualitätsfilter passieren. Ein leerer oder vollständig
abgewiesener Eingang erzeugt einen Bericht mit Diagnostik und null Alarmen.
Lesefehler einzelner Mails erscheinen ebenfalls dort; Profil-/Manifestfehler ergeben
Exitcode 2. JSON-Beleg ist verpflichtend. Originale werden nie geändert oder gelöscht.

`notify: true` aktiviert optional das vorhandene freie `notify-send` unter Linux.
Nur tatsächlich neue/preisgeänderte, als „auffällig günstig“ klassifizierte Angebote
mit mindestens vier aktuellen passenden Peers können einen Sammelhinweis auslösen.
Windows 11 und Linux benötigen für CLI/HTML/JSON nur Python; ohne `notify-send`
entfällt der Desktop-Hinweis. Es wird nichts installiert oder automatisch aktiviert.

Historie wird vor Zustellung committed: identische Wiederholungen senden keinen
Hinweis. **Exactly-once-Zustellung ist nicht garantiert**: Absturz nach Commit oder
fehlgeschlagenes `notify-send` kann einen Hinweis verlieren; kein Zustellretry.
Berichte bleiben unabhängig vom Desktop lesbar. SQLite und HTML/JSON bilden keine
gemeinsame atomare Einheit; jede Berichtsdatei wird einzeln atomar ersetzt. Bei
Dateisperren (etwa Defender) Exitcode 2 und Rollback; ein bereits ersetzter Bericht
kann dann vom Historienstand abweichen. Erneuter Lauf regeneriert die Berichte;
erfolgreiche Laufbelege bleiben zusätzlich in `radar_runs`. Keine Hintergrundaktivierung durch den manuellen Profilaufruf.

```sh
python -m unittest discover -s tests -p 'test_search_mail.py'
```

Unter Windows 11 steht der Profilaufruf im Schnäppchenjäger-Fenster über die Schaltfläche Radar-Profil auswählen und auswerten bereit. Nach erfolgreicher Analyse öffnet sich der lokale HTML-Bericht. Auch dort ist dies nur ein lokaler Import, keine automatische Marktbeobachtung.

Schnell hintereinander eintreffende Suchmails lösen keine dauerhafte systemd-Startlimit-Sperre mehr aus. Inseratsdaten bleiben in den privaten HTML-/JSON-Berichten; das Journal bekommt nur Fehlermeldungen, keine vollständigen Ergebnislisten.

In KDE werden Suchprofile beim Monitoring nach Modell und nicht nach Hash-Ordnern ausgewählt. Unter Schnäppchenjäger → Gespeicherte E-Mail (.eml) in Suchprofil importieren kann eine eigene exportierte Nachricht direkt per Dateidialog atomar in das Profil übernommen werden; die exportierte Originaldatei bleibt unverändert. Diese Funktion ersetzt keine Mailkontoeinrichtung.

Unter Linux/KDE steht derselbe Profilaufruf zusätzlich unter Schnäppchenjäger → Suchmail-/Datei-Radar zur Verfügung. Die Kontrollzentrale zeigt neue Einträge, Preisänderungen und günstige Funde an, ohne Rohinhalte in Dialoge zu übernehmen.


## Linux/KDE: automatische lokale Verarbeitung ab 1.3.0

1. Installierten Kleinanzeiger als normalen Benutzer öffnen, **Schnäppchenjäger →
   Neues Suchprofil einrichten** wählen und das selbst gesuchte **exakte Modell**
   eingeben. Kein Modell ist vorbelegt oder bereits aktiv.
2. Die Textansicht nennt Profil und Eingangsordner. Nächste Handlung:
   **Kleinanzeigen-Suche auf Website als Suchauftrag speichern und eigene EMLs
   atomar in diesen Ordner zustellen.** Die Menüoption „Manuell im Browser suchen“
   hilft beim Öffnen der Suche; Speichern und Export/Zustellung bleiben beim Nutzer.
3. Optional die Frage nach lokalem Monitoring bestätigen. Ein systemd-User-Path
   beobachtet nur diesen Eingang; der gekoppelte Oneshot-Service führt den
   installierten `mark_api.bargain_hunter_cli --radar <profil>` aus. Kein Timer,
   kein Polling eines leeren Eingangs, keine neue App oder zusätzlicher Daemon.
4. **Schnäppchenjäger → Monitoring: Status / Starten / Stoppen** und das eingerichtete
   `profil.json` wählen. Stoppen deaktiviert den Path und stoppt den zugehörigen
   Service; Ergebnisse und Eingänge bleiben erhalten. Erneutes Starten ist möglich.

Speicherort: `$XDG_DATA_HOME/kleinanzeiger/schnaeppchenradar/`, standardmäßig
`~/.local/share/kleinanzeiger/schnaeppchenradar/`. Erst der Einrichtungsauftrag
legt einen Ordner pro SHA-256 der getrimmten Modellbezeichnung an. Darin liegen
`profil.json` (`notify: true`), `suchmails/` und nach einer Auswertung
`history.sqlite`, `bericht.html`, `bericht.json`. Groß-/Kleinschreibung und umbenannte
Modelle erhalten getrennte Identitäten. Vorhandene abweichende Konfigurationen
werden abgewiesen, fremde Datenbanken nicht übernommen. Keine Verkäuferdatenbank.
Profile und Inbox sind privat; keine Ausführung mit sudo/root vorsehen.

**Importvertrag:** Eine EML muss vollständig fertig geschrieben sein und dann
**atomar** in den Eingang veröffentlicht werden (Rename innerhalb desselben
Dateisystems oder der Zustellbefehl unten). Nicht direkt eine `.eml` im Eingang
öffnen und langsam beschreiben: ein Path-Watch kann bereits beim ersten Schreiben
auslösen. Angefangene Dateien außerhalb der Inbox halten. Unbekannte Formate bleiben
liegen und werden diagnostiziert; keine automatische Löschung oder Mail-Einzelfreigabe.

Die einzige neue CLI ist auch headless nutzbar:

```sh
kleinanzeiger-radar create 'EXAKTE EIGENE MODELLBEZEICHNUNG'
# Ausgegebenen absoluten Profilpfad als PROFIL verwenden:
kleinanzeiger-radar deliver "$PROFIL" /pfad/zum/eigenen/export.eml
kleinanzeiger-radar start "$PROFIL"
kleinanzeiger-radar status "$PROFIL"
kleinanzeiger-radar stop "$PROFIL"
```

`create` aktiviert nichts. `deliver` liest eine bereits berechtigte lokale reguläre
Datei (bis 1 MB), schreibt zunächst außerhalb der Inbox und veröffentlicht die
fertige Kopie atomar unter `<Inhalts-SHA256>.eml`. Identischer Inhalt erzeugt keine
neue Datei und kein erneutes Ereignis; gleicher Originalname mit anderem Inhalt
bleibt getrennt. Originale werden nicht verändert. Die SHA ist eine interne
Identität, keine Freigabe, die der Nutzer eingeben müsste. Unter Windows bleiben
manuelle Radar-Auswertung und Berichte verfügbar; `start/status/stop` benötigen Linux.

User-Units liegen in `$XDG_CONFIG_HOME/systemd/user/` (Standard `~/.config/systemd/user/`),
unter `kleinanzeiger-radar-<Profil-SHA256>.path` und `.service`. Fremde oder geänderte
Unit-Inhalte werden weder überschrieben noch gestoppt. Die Aktivierung verwendet
`systemctl --user daemon-reload` und `enable --now`; kein `--no-block`. Danach wird
der aktive Path-Status geprüft. Bei Startfehlern werden neu erstellte Units entfernt
und ein neu aktivierter Watch zurückgerollt. Vorhandene Units bleiben erhalten.
Ohne systemd-User-Session kann man Profile erstellen und den Radar manuell ausführen.

Der Service verwendet genau die installierte Python-Umgebung der Einrichtung mit
`-I`; ein reiner oder editable Quellcheckout genügt nicht. Nach Umzug/Entfernung dieser
Umgebung müssen die Units nach Prüfung neu eingerichtet werden. Der PATH enthält
`%h/.nix-profile/bin:/run/current-system/sw/bin:/usr/bin:/bin`, damit ein vorhandenes
`notify-send` auch in KDE/Nix gefunden wird. Benachrichtigungen benötigen eine laufende
Desktop-Session und sind nicht garantiert. Der Status meldet auch einen fehlgeschlagenen
Service; Details liefert `journalctl --user -u kleinanzeiger-radar-<Profil-SHA256>.service`.
Die GUI darf nach Aktivierung geschlossen werden. Außerhalb einer User-Session wird
keine Ausführung garantiert; es wird kein Linger aktiviert.

Ohne eigene gespeicherte Suche und EML-Zulauf gibt es **keine echten Alerts**. Kein
Netzwerkzugriff, Scraping, Mailbox-Login, API-Schlüssel oder kostenpflichtiger Dienst.
Echte Kleinanzeigen-Mailtemplates bleiben unbestätigt. Die oben dokumentierten
Parsergrenzen und Mindestvergleichszahlen gelten unverändert.

## Silberne SACD-Player (ab 1.4.0)

Im KDE-Menü **Schnäppchenjäger → Silberne SACD-Player beobachten** oder per
`kleinanzeiger-radar create-silver-sacd` einmalig das vordefinierte Profil
**SACD-Player silber** anlegen. Deutschland, ohne Preislimit, ohne bestimmte
Marke oder exaktes Modell. Wiederholen liefert denselben Profilpfad und erhält
alle Dateien und die Historie. Ein exaktes Modellprofil mit demselben Label
bleibt durch eine andere SHA-256-Identität getrennt.

[Offizielle Suche: SACD Player silber](https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=SACD+Player+silber)
öffnen → **Suchauftrag speichern**. Dies erfordert die echte Interaktion im
eigenen Kleinanzeigen-Konto. Die native Funktion und Benachrichtigungen sind in
[der offiziellen Hilfe](https://hilfe.kleinanzeigen.de/hc/de/articles/17102975598492)
beschrieben; E-Mail-Mitteilungen verwaltet man unter
[Suchaufträge](https://hilfe.kleinanzeigen.de/hc/de/articles/17105076741788).
Die Anwendung meldet sich weder bei Kleinanzeigen noch bei einer Mailbox an.

Der Dialog und das Profil nennen den privaten Inboxpfad `suchmails` neben
`profil.json`. Eigene exportierte EMLs mit
`kleinanzeiger-radar deliver /absoluter/pfad/profil.json /pfad/export.eml`
atomar zustellen. Optional **Monitoring → Starten / Status / Stoppen** nutzen
oder `kleinanzeiger-radar start|status|stop /absoluter/pfad/profil.json`
(jeweils genau eine Aktion). Erstellung allein startet nichts. Linux nutzt das
bestehende systemd.path-/oneshot-Paar ohne Polling. Stop erhält Dateien und DB.

Die zusätzliche Titelprüfung verlangt einen plausiblen SACD-Player und explizit
`silber…` oder `silver…`. Fehlende oder widersprüchliche Farbangaben erzeugen
Diagnosen. Keine Hersteller-Whitelist und keine Bildfarberkennung. Tonträger,
Zubehör, Lautsprecher und Defektanzeigen werden ausgeschlossen. ND8006 ist ein
bewusster Negativfall: DSD-Dateiwiedergabe bedeutet keine SACD-Disc-Wiedergabe
([Marantz-Datenblatt](https://www.marantz.com/on/demandware.static/-/Library-Sites-marantz_northamerica_shared/default/dw62bb25a8/archive-downloads/nd8006_product_info_sheet_en01.pdf)).
Text ist kein Funktions- oder Fotobeweis: Originalanzeige selbst prüfen.

Kategorieangebote sind **normalerweise nicht bewertbar**. Erst wenn mindestens
**vier weitere unterschiedliche Inserate desselben belegbaren Herstellermodells**
mit gleichem Zustandsfeld innerhalb von 72 Stunden beobachtet wurden, erhält ein
Kandidat einen Median dieser **Angebotspreise** und einen dazu berechneten Rabatt.
Ab 30 Prozent unter diesem Modellmedian gilt er als `auffällig günstig`.
Verschiedene Modelle, alte Beobachtungen und ungeklärte Modellbezeichnungen
werden nicht als Preispeers zusammengerechnet. Versand, Funktion und echte
Verfügbarkeit sind damit nicht belegt.

`reference_type=category_unpriced` bedeutet weiterhin fehlende Vergleichsdaten;
bei ausreichend Vergleichsdaten lautet es `category_asking_prices`.
`candidate_events` zählt neue Inserate, Preisänderungen und erstmalig bewertbare
auffällige Angebote; `hits` zählt nur nach Modellvergleich auffällig günstige
Ereignisse. SQLite-Historie, HTML/JSON und die Desktop-Benachrichtigung
deduplizieren je Angebotspreisversion. Der erste öffentliche Lauf bildet
ausschließlich eine Bestandsaufnahme. Exakte Modellprofile bleiben unabhängig.

Der Parser bleibt streng bei **einem Angebot je Mail** in den oben beschriebenen
Layouts. Aktueller Date-/From-Header und direkter Inseratlink sind erforderlich;
Header beweisen keine Echtheit, Tracking wird nicht aufgelöst. Echte offizielle
Suchmailvorlagen sind noch nicht verifiziert. Ohne echten EML-Zulauf besteht
keine laufende Marktbeobachtung; ein Mailboximport hat nicht stattgefunden.
Das separate EML-Profil kann unter Linux/KDE eingerichtet und überwacht werden.
Ohne eigene gespeicherte Suche und tatsächlichen EML-Transport entstehen daraus
keine neuen Marktangebote. Für die direkte öffentliche Suche ist kein EML-Zulauf nötig.

## Direkte silberne SACD-Suche (ab 1.5.0)

Die native Linux/KDE-Kontrollzentrale bietet unter Schnäppchenjäger die
Aktion **Silberne SACD-Player direkt suchen (automatisch)**. Ein neues,
getrenntes Profil nutzt eine öffentlich erreichbare Ergebnisseite.
Die bereits vorhandene EML-Suche wird weder verändert noch abgeschaltet.

Einrichtung per Kommandozeile:

- kleinanzeiger-radar create-public-sacd
- kleinanzeiger-radar run-public /absoluter/pfad/profil.json
- kleinanzeiger-radar start /absoluter/pfad/profil.json
- kleinanzeiger-radar status /absoluter/pfad/profil.json
- kleinanzeiger-radar stop /absoluter/pfad/profil.json

Die Profilerstellung ist idempotent. Nur das bewusste Starten aktiviert
einen systemd-User-Timer; dessen Intervall beträgt 30 Minuten. Seit 1.6.0
rotieren drei feste öffentliche Suchanfragen; seit 1.7.0 sind es
**SACD Player silber**, **SACD silber** und die breitere Suche **SACD Player**.
Die dritte Suchvariante ersetzt die zuvor ergebnisarme Marantz-Silber-Abfrage.
**Je Timerlauf findet genau ein Abruf**
statt, nicht drei parallele Abfragen. Keine Chrome-Session,
kein Konto, keine E-Mail, keine Drittanbieter-API, keine Proxies,
keine Captcha-Lösung, keine Hintergrundretries.

Der erste erfolgreiche Lauf ist nur eine Bestandsaufnahme. Weitere
Abrufe prüfen neue IDs und wirkliche Änderungen des angebotenen
Festpreises. VB-/Gratispreise, Defektgeräte, Zubehör, SACD-Discs,
Marantz ND8006 und ausdrücklich schwarze Titel werden verworfen.
Plausible SACD-Modelle mit **fehlender Silberangabe** erscheinen seit 1.7.0
ausschließlich unter **Farbe prüfen** im Kandidatenbericht: nur zur Prüfung
des Originalfotos, ohne Einstufung als silbernes Gerät, ohne SQLite-Kandidaten
und ohne automatische Marktbenachrichtigung. CD-only, Zubehör und defekte
Geräte gelangen auch dort nicht hinein. Ab 1.7.1 bleiben kürzlich
beobachtete Prüfangebote über die nächsten Suchvarianten hinweg sichtbar,
soweit sie in den letzten sechs erfolgreichen Radarläufen und höchstens
72 Stunden zuvor beobachtet wurden. Zuletzt beobachtete Angebote sind
nicht als aktuell verfügbar bestätigt; wird dieselbe ID später als silberner
SACD-Player erkannt, verschwindet sie aus der getrennten Prüfliste.
Ein Angebotsmedian ist ausschließlich bei mindestens vier frischen weiteren
Inseraten desselben Modells verfügbar. Sonst gibt es keine statistische
Schnäppchenwertung.
Nicht mehr angezeigte Inserate werden nicht automatisch als verkauft
interpretiert. Versandkosten und technische Wiedergabefähigkeit bleiben
ungeprüft.

Daten liegen separat im Profilordner als angebote.json,
history.sqlite, bericht.json und bericht.html. Persönliche Preisziele
werden zusätzlich als **preisziele.json** atomar und nur für den Benutzer
gespeichert; das unveränderte Profil und die Angebotsdatenbank bleiben bestehen.
Bei HTTP-/Layoutfehler
werden die vorherigen Daten nicht überschrieben. Historische Kandidaten
sind keine bestätigte aktuelle Verfügbarkeit. Der Bericht öffnet den
Original-Link zur eigenständigen Prüfung.

**Nutzung:** Der Kandidatenbericht sortiert beobachtete Angebotspreise
aufsteigend, mit Original-Link und Zeitangabe. Die Sortierung ist keine
modellübergreifende Schnäppchenbewertung. Unter **Schnäppchenjäger → Monitoring**
stehen **Kandidatenbericht öffnen**, **Benachrichtigung testen** und
**Persönliches Modellpreisziel setzen / löschen** zur Verfügung.

### Eigenes Modellpreisziel statt unbelegtem Vergleichspreis (ab 1.7.0)

1. Im KDE-Schnäppchenjäger das öffentliche SACD-Profil unter **Monitoring**
   auswählen und **Persönliches Modellpreisziel setzen / löschen** öffnen.
2. Eines der tatsächlich im Bericht erkannten Herstellermodelle auswählen
   und den gewünschten Höchstpreis in EUR eingeben (beispielsweise 250).
   Das leere Eingabefeld entfernt dieses Ziel. Weitere Modellziele bleiben bestehen.
3. Ab dem nächsten erfolgreichen regulären Suchlauf erscheinen frische
   Festpreisangebote innerhalb des Limits als **persönliches Preisziel erreicht**.
   Bei einem neu erreichten Ziel wird genau eine Meldung je Angebots- und
   Zielpreisversion erzeugt. Der erste Lauf einer neuen Suche bleibt wie bisher
   eine stille Bestandsaufnahme.

Das ist **deine persönliche Kaufgrenze**, kein ermittelter Marktwert und kein
bewiesener Rabatt. Die Modellpreis-Medianwertung mit vier fremden frischen
Inseraten bleibt unabhängig erhalten. Stale Inserate werden nicht neu
als Zieltreffer alarmiert. Versandkosten und Gerätezustand bitte am Link prüfen.
Die Testmeldung erzeugt weder eine neue Anzeige noch eine Änderung der Historie.