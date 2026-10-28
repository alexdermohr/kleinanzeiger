# Kleinanzeiger

**Kleinanzeiger** verwaltet eigene Kleinanzeigen über eine lokale Weboberfläche: Anzeige erstellen und veröffentlichen, Bilder zuordnen, Titel und Beschreibung bearbeiten, pausieren, aktivieren, löschen, Bestand synchronisieren sowie Historie, Views, Reaktionen, E-Mail-Daten und Auswertungen einsehen. Hinzu kommen KI-Text- und Bildentwürfe sowie lokale Datensicherung und Wiederherstellung.

- **Repository:** https://github.com/alexdermohr/kleinanzeiger
- **Python:** ab 3.12; CI-Zielmatrix Python 3.12 und 3.13
- **Distribution:** `kleinanzeiger`; bestehende interne Module `mark_api` und `mark-api-*`-Kommandos bleiben kompatibel
- **Technischer Lieferstatus:** Erst die bestandenen Tests, der Installationsnachweis, ein unabhängiger Sicherheitsreview und ein freigegebener Merge des konkreten Liefercommits ergeben die Produktabnahme. Kein Ersatz durch ein rein lokales Dashboard.

## KI-Portfolio-Ausbau für hunderte bis tausende Anzeigen

Der verbindliche priorisierte Umsetzungsplan mit P0–P5, Lastgates und
anschließenden KI-/Massenaufträgen liegt in
[docs/roadmap-ki-portfolio-2026-10-26.md](docs/roadmap-ki-portfolio-2026-10-26.md).
**P0:** Anzeigen im Dashboard mit indexierten Katalogseiten (jeweils maximal
50 sichtbar), Titelsuche, ID-Suche, Statusfilter und seitenübergreifender
lokaler Mehrfachauswahl. Ein CSV-Export enthält ausschließlich ausgewählte
Anzeigen-IDs; Auswahl löst **keine** Plattformaktion aus. Die frühere
`/api/ads`-Schnittstelle bleibt für bisherige Integrationen kompatibel.

## Native Windows-Kundenanwendung (ab v0.1.8)

Die vollständige Windows-11-x64-Ausgabe benötigt **kein WSL**.
Das signatur- und prüfsummengebundene Windows-Paket ist unter
[Releases](https://github.com/alexdermohr/kleinanzeiger/releases) verfügbar.
Das ZIP vollständig entpacken und **Kleinanzeiger.cmd** starten.
Das zentrale Windows-Fenster bietet Installation, Update, Start/Stop,
Konto-Anmeldung, Backup/Restore, KI-Einrichtung, **lokalen EML-Import** und
**Vergleichslabels (Bildtyp/Stadt/Texttyp/Titeltyp) mit Anzeigenauswahl**.
Das Dashboard bündelt einen such- und filterbaren Anzeigenkatalog,
Seitenwechsel, lokale Mehrfachauswahl, sämtliche Create/Media/Content/State/Delete-
Funktionen, manuellen Live-Abgleich, Analytics und optionale KI-Entwürfe.
Vollständiges **HTML-Benutzerhandbuch** und **HTML-Funktionsübersicht** liegen im
Windows-ZIP und sind im Kontrollfenster direkt erreichbar.
Bei E-Mail-Import und Labelpflege wird der laufende Produktprozess
kontrolliert beendet; der getrennte lokale SQLite-CLI-Pfad verwendet
keine zusätzliche HTTP-/Browser-Plattformautorität.

Kurzanleitung: [docs/windows-installation.md](docs/windows-installation.md).
Kundenunterlagen: [Handbuch](docs/Kleinanzeiger-Handbuch.html) und
[Funktionsübersicht](docs/Kleinanzeiger-Funktionen.html).
Die separate Linux-A6-Migration gehört nicht zur Windows-Neuinstallation.

## Installation und Bedienstart

Aus dem Repository, in einer isolierten Python-Umgebung:

```bash
python -m pip install '.[private-web,generation]'
```

Eine lokale Chrome-/Chromium-Sitzung muss bereits vom jeweiligen Anwender angemeldet sein und den Chrome-DevTools-Port nur auf Loopback bereitstellen. Kleinanzeiger erledigt weder Login noch MFA oder CAPTCHA. Ein neues Datenbankverzeichnis beziehungsweise eine neue Datenbank nur ausdrücklich mit `--init-db` anlegen. Normale Starts verwenden die vorhandene Datenbank ohne dieses Flag:

```bash
kleinanzeiger --db /pfad/kleinanzeiger.sqlite --init-db --cdp-port 9222
kleinanzeiger --db /pfad/kleinanzeiger.sqlite --cdp-port 9222
```

Der Starter zeigt die lokale Dashboard-URL mit einem sitzungsgebundenen Zugriffstoken an. Der Start aktiviert standardmäßig die **vollständige Anzeigenverwaltung einschließlich Write und Media**. Wer ausnahmsweise nur lesen möchte, verwendet `--read-only`.

Der normale `kleinanzeiger`-Starter nutzt zur Kompatibilität SQLite unter
der aufrufenden lokalen UID. Für den streng getrennten, durch unabhängige
Owner-/Actor-/Witness-Rollen abgesicherten Kundenstart steht zusätzlich der
vollständig installierte und synthetisch abgenommene **geschützte
Produktlauncher** bereit.

### Geschützter Kundenstart (A6)

Auf einem dafür vorbereiteten Linux-Host mit Landlock ABI 4 oder neuer,
funktionsfähigen Netzwerk-/PID-Namespaces, freier Rollentrennung der UIDs
50042–50047 und einem **root-eigenen, nicht beschreibbaren installierten Wheel**
als Root in einer kontrollierten Shell starten. Der CDP-Port muss auf
`127.0.0.1` erreichbar sein und einem bereits angemeldeten Kundenbrowser
gehören.

```bash
# Nur für eine wirklich neue, noch nicht vorhandene Zustandswurzel:
kleinanzeiger-protected --state-root /var/lib/kleinanzeiger --init-new --cdp-port 9222

# Jeder reguläre Neustart ohne --init-new:
kleinanzeiger-protected --state-root /var/lib/kleinanzeiger --cdp-port 9222
```

Der geschützte Starter stellt das volle lokale Dashboard über
`http://127.0.0.1:8875` bereit und gibt eine private Sitzungstoken-URL aus.
Die geschützte Anzeigenverwaltung einschließlich Create/Publish, Content,
Pause/Activate und Delete wurde unter realen getrennten UIDs über den
produktiven CDP-Adapter gegen zustandsbehaftete, synthetische Browsergegenstellen
aus einem frischen Wheel getestet. Medienvorbereitung und ein getrennt
getesteter Media-Create-Pfad sind vorhanden. Ein unbekanntes Write-Ergebnis
löst **keinen** zweiten Plattform-Submit aus.

Für die ausdrücklich optionale Text- und Bildgenerierung die Zusatzabhängigkeit
`kleinanzeiger[generation]` installieren, den eigenen API-Schlüssel nur im
vertrauenswürdigen Root-Startkontext setzen und zusätzlich starten:

```bash
export OPENAI_API_KEY=...  # nicht in Git, Screenshots oder Logdateien ablegen
kleinanzeiger-protected --state-root /var/lib/kleinanzeiger --cdp-port 9222 --enable-generation
```

Die KI arbeitet in einer sechsten separaten Nicht-Owner-UID mit auf HTTPS-Port
443 begrenztem TCP-Egress und peer-authentifiziertem Unix-Socket. Das Dashboard
und der SQLite-Owner bleiben ohne externen Netzwerkzugang. Jeder KI-Aufruf
braucht eine neue, sichtbare Kostenbestätigung; Entwürfe werden nie automatisch
veröffentlicht. Der installierte KI-Pfad ist nur mit **synthetischem, niemals
kostenpflichtigem Provider** abgenommen; der konkrete externe API-Schlüssel
und Kundenaccount werden hier nicht zum Test eingesetzt. Kein paralleler
Kleinanzeiger-Prozess darf denselben CDP-Target-Claim verwenden.

**Liefergrenze:** Der native geschützte Start und die synthetische, installierte
Kundenkette sind belegbar. Bestehende Legacy-Datenbanken werden durch
`--init-new` nicht übernommen. Ein Host-Cutover mit alten
FD-/MAP_SHARED-/WAL-/SHM-Rechten und die unabhängige Sicherheitsfreigabe sind
weiterhin gesondert nachzuweisen. Ein externer Plattformfreigabeprozess ist
nicht Teil der technischen Implementierung.

**Geschütztes A6-Frontend (separater installierter Einstieg):** Das Wheel
stellt zusätzlich `kleinanzeiger-protected-frontend --config-fd FD` bereit.
Diesen Prozess startet ausschließlich ein vertrauenswürdiger Supervisor mit
einer eng begrenzten JSON-Konfiguration über einen privaten geerbten
Dateideskriptor; sie enthält den UID-geprüften Storage-Owner-Socket, den
bestehenden Owner-Write-API-Port, die Principal-Identität und die beiden
Sitzungstokens. Der Prozess liest **keine** SQLite-Datei und nimmt keine
Browseraktion selbst vor; vorhandene unabhängig attestierte Actor-, Witness-
und Owner-Prozesse sind zwingende Voraussetzung. Der eingebaute Selbsttest
verweigert fehlende/falsche Gegenstellen. Die installierte synthetische
Mehr-UID-Abnahme benutzt dieselbe produktive Frontend-Komposition. **Dieser
zusätzliche Einstieg ist kein eigenständiger Host-Bootstrap und gibt weder
den Draft-Container-Cutover noch einen Host-Restore frei.**

Unter `Anzeigen verwalten` kann der Anwender eine Anzeige mit Kategorie, Titel, Beschreibung, Festpreis und Bildern erstellen oder eine bestehende eigene Anzeige ändern, pausieren, aktivieren und löschen. In **Meine Anzeigen** können nach expliziter Gesamtvorschau bis zu **10 eigene Anzeigen** kontrolliert **nacheinander pausiert, aktiviert oder nach zusätzlicher Eingabe von `LÖSCHEN N` endgültig gelöscht** werden, mit Readback vor jeder Aktion, ID-Protokoll der bestätigten Schritte und Stop bei unklarem Ergebnis; keine automatischen Massenpublikationen, Preisänderungen oder unbeaufsichtigten Löschungen. Externe Aktionen benötigen eine ausdrückliche Benutzerentscheidung; Idempotenz, Write-Bestätigung und unklare Ausgänge bleiben konservativ behandelt. Bei einem unbekannten Ausgang ist zunächst die vorhandene Reconciliation zu verwenden – **kein blinder erneuter Publish oder Delete**.

Im zentralen Bereich **Meine Anzeigen → Wiedervorlagen** können bestätigte eigene Anzeigen einen **eigens gewählten** Prüftermin und eine kurze Notiz erhalten. Die Termine werden lokal und revisionsgesichert gespeichert, sind bis zu 10.000 Einträge stark und nach ID in 50er-Seiten mit überfälligen/bald fälligen Einträgen abrufbar. **Das ist kein Plattform-Ablaufdatum**: Solange die Eigentümerbeobachtung kein verlässliches Ablaufdatum liefert, bleibt dieses ausdrücklich unbekannt. Es erfolgt weder eine automatische Verlängerung noch ein Anbieteraufruf oder Plattform-Write.

## Text- und Bildentwürfe im selben Dashboard

Generierung erfordert die optionale Provider-Abhängigkeit und einen eigenen API-Schlüssel des Kunden im Startprozess. Der Modus wird nur auf ausdrücklichen Wunsch aktiviert:

```bash
export OPENAI_API_KEY=...
kleinanzeiger --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --enable-generation
```

Der Starter gibt eine gemeinsame Dashboard-URL für Anzeigenverwaltung und Generierung aus. Text und Bilder entstehen zuerst als editierbare, unveröffentlichte Entwürfe und können in das Anzeigenformular übernommen werden. **Jeder einzelne kostenpflichtige Provideraufruf** benötigt eine erneute Bestätigung nach der sichtbaren Modell-/Kosteninformation. Durch die Übernahme wird keine Anzeige automatisch veröffentlicht. Synthetische Tests dürfen weder echte Kosten noch echte Plattformaktionen auslösen.

## Interessenten und KI-Antwortentwürfe ohne automatischen Versand

In **Meine Anzeigen** führt **Antwort vorbereiten** in das zentrale Formular **Interessenten & Antworten** mit bereits ausgewählter eigener Anzeigen-ID. Eine erhaltene Anfrage wird dort manuell eingegeben, ohne private Kontaktdaten oder Zahlungsinformationen. Ein neutraler Textbaustein funktioniert ohne KI und ohne Kosten. Optional erzeugt der vorhandene KI-Anbieter nach **erneuter Einzelfreigabe von Modellkosten und Textübertragung** einen bearbeitbaren Antwortentwurf. Vor dem KI-Aufruf wird die eigene Anzeige über die exakte lokale ID nochmals geprüft; Quota, Provider-Einzelnutzung, Fehlersperre und keine automatischen Retries gelten unverändert. Der Antworttext wird **nicht an Kleinanzeigen versendet**, sondern nur zur eigenständigen Prüfung und Übernahme kopiert. Es gibt **keinen vollständigen Plattform-Postfachabruf oder automatischen Nachrichtenversand**.

## Sichere Bestandssynchronisation

Ein gesonderter read-only-Befehl nutzt das bestehende Sync-Journal und dokumentiert Zeitpunkt, Herkunft und Ausgang jeder Beobachtung; beschädigte oder nicht eindeutig bestätigte Zustände werden nicht als Erfolg gespeichert:

```bash
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --once
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --interval-seconds 300 --immediate
```

**Nicht parallel zum Produktstarter auf demselben Browserziel ausführen.** Browser-/CDP-Zugriffe verwenden eine exklusive Zielreservierung; nach einem unbekannten Browser-/Netzwerkausgang erfolgt keine automatische Wiederholung. Die lokale SQLite-Datei wird durch Sync niemals stillschweigend neu erzeugt. `SIGINT` und `SIGTERM` können den Polling-Prozess kontrolliert beenden.

## Backup, Daten und zusätzliche Werkzeuge

`kleinanzeiger-backup --help` zeigt die vorhandenen Backup-/Restore-Kommandos und deren Sicherheitsvoraussetzungen. Bestehende Sicherheitsidentitäten, offene Write-Anfragen und Recovery-Fences dürfen beim Sichern, Wiederherstellen und nach Neustarts nicht verlorengehen. Restore nie unkontrolliert über eine aktive Sitzung ausführen.

Für die noch nicht freigegebene A6-Hostumstellung gibt es eine **rein negative Linux-Kernel-Diagnose**: `python -m mark_api.a6_host_preflight --db /absoluter/pfad/kleinanzeiger.sqlite`. Sie prüft angeheftete Identitäten von SQLite-Hauptdatei, WAL und SHM gegen alte schreibbare Deskriptoren, gelöschte/umbenannte Inodes und schreibbare `MAP_SHARED`-Zuordnungen laufender Prozesse. Fehlt eine geschützte Datei oder ist ein Prozess nicht überprüfbar, wird konservativ abgewiesen. Das Programm endet **immer mit Code 2 und erteilt ausdrücklich keine Freigabe**: Ein endlicher Scan kann weder die vollständige PID-/Mount-Namespace-Sicht noch den Entzug historischer Schreibrechte, eine gesicherte Backup-Quelle oder echte A6-Quieszenz beweisen.

Zusätzliche CLI-Kommandos: `kleinanzeiger-dashboard` für ein eigenständiges Dashboard, `kleinanzeiger-offline` für ausschließlich lokal importierte EML-/JSON-Daten ohne Plattformkontakt, `kleinanzeiger-import-mail`, `kleinanzeiger-classify`, `kleinanzeiger-generate` und `kleinanzeiger-local-smoke`. Der Offline-Modus ist ein **zusätzliches Werkzeug und kein Ersatz** für die vollständige Anzeigenverwaltung.

## Technische Abnahme und Sicherheit

Die Entwicklung und technische Abnahme sind unabhängig von einer allgemeinen externen Plattformgenehmigung. Sämtliche Mark-eigenen Read-/Write-/Media-/Sync-Funktionen werden auch ohne ein echtes Kleinanzeigen-Konto mit **zustandsbehafteten synthetischen Gegenstellen** getestet, einschließlich unklarer HTTP-Antwort, Restart, Idempotenz, beschädigter Datenbank und Recovery. Echte Fremdplattform-Zugriffe benötigen eine geeignete authentifizierte Kundensitzung und die jeweilige tatsächliche Berechtigung des Betreibers; Tests verwenden keine fingierten Credentials, keine Login-Umgehung und keinen kostenpflichtigen Provider.

Die erforderlichen CI- und Sicherheitsreviews gelten stets nur für den **konkret geprüften Commit**. Ein Merge darf nicht allein wegen grüner CI an den Review-/Captain-Gates vorbei erfolgen. Offene technische Sicherheitsbefunde werden nicht durch einen pauschalen Plattformhinweis ersetzt.

**Normative Anforderungen:** [Produktspezifikation](docs/product-spec.md), [Entscheidungen D-028 und D-029](docs/DECISIONS.md). Spezialisierte Hintergründe: [Generierung](docs/generation.md), [Synchronisation](docs/periodic-sync.md).