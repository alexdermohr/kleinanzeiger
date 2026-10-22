# Kleinanzeiger

**Kleinanzeiger** verwaltet eigene Kleinanzeigen über eine lokale Weboberfläche: Anzeige erstellen und veröffentlichen, Bilder zuordnen, Titel und Beschreibung bearbeiten, pausieren, aktivieren, löschen, Bestand synchronisieren sowie Historie, Views, Reaktionen, E-Mail-Daten und Auswertungen einsehen. Hinzu kommen KI-Text- und Bildentwürfe sowie lokale Datensicherung und Wiederherstellung.

- **Repository:** https://github.com/alexdermohr/kleinanzeiger
- **Python:** ab 3.12; CI-Zielmatrix Python 3.12 und 3.13
- **Distribution:** `kleinanzeiger`; bestehende interne Module `mark_api` und `mark-api-*`-Kommandos bleiben kompatibel
- **Technischer Lieferstatus:** Erst die bestandenen Tests, der Installationsnachweis, ein unabhängiger Sicherheitsreview und ein freigegebener Merge des konkreten Liefercommits ergeben die Produktabnahme. Kein Ersatz durch ein rein lokales Dashboard.

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

Unter `Anzeigen verwalten` kann der Anwender eine Anzeige mit Kategorie, Titel, Beschreibung, Festpreis und Bildern erstellen oder eine bestehende eigene Anzeige ändern, pausieren, aktivieren und löschen. Externe Aktionen benötigen eine ausdrückliche Benutzerentscheidung; Idempotenz, Write-Bestätigung und unklare Ausgänge bleiben konservativ behandelt. Bei einem unbekannten Ausgang ist zunächst die vorhandene Reconciliation zu verwenden – **kein blinder erneuter Publish oder Delete**.

## Text- und Bildentwürfe im selben Dashboard

Generierung erfordert die optionale Provider-Abhängigkeit und einen eigenen API-Schlüssel des Kunden im Startprozess. Der Modus wird nur auf ausdrücklichen Wunsch aktiviert:

```bash
export OPENAI_API_KEY=...
kleinanzeiger --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --enable-generation
```

Der Starter gibt eine gemeinsame Dashboard-URL für Anzeigenverwaltung und Generierung aus. Text und Bilder entstehen zuerst als editierbare, unveröffentlichte Entwürfe und können in das Anzeigenformular übernommen werden. **Jeder einzelne kostenpflichtige Provideraufruf** benötigt eine erneute Bestätigung nach der sichtbaren Modell-/Kosteninformation. Durch die Übernahme wird keine Anzeige automatisch veröffentlicht. Synthetische Tests dürfen weder echte Kosten noch echte Plattformaktionen auslösen.

## Sichere Bestandssynchronisation

Ein gesonderter read-only-Befehl nutzt das bestehende Sync-Journal und dokumentiert Zeitpunkt, Herkunft und Ausgang jeder Beobachtung; beschädigte oder nicht eindeutig bestätigte Zustände werden nicht als Erfolg gespeichert:

```bash
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --once
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --interval-seconds 300 --immediate
```

**Nicht parallel zum Produktstarter auf demselben Browserziel ausführen.** Browser-/CDP-Zugriffe verwenden eine exklusive Zielreservierung; nach einem unbekannten Browser-/Netzwerkausgang erfolgt keine automatische Wiederholung. Die lokale SQLite-Datei wird durch Sync niemals stillschweigend neu erzeugt. `SIGINT` und `SIGTERM` können den Polling-Prozess kontrolliert beenden.

## Backup, Daten und zusätzliche Werkzeuge

`kleinanzeiger-backup --help` zeigt die vorhandenen Backup-/Restore-Kommandos und deren Sicherheitsvoraussetzungen. Bestehende Sicherheitsidentitäten, offene Write-Anfragen und Recovery-Fences dürfen beim Sichern, Wiederherstellen und nach Neustarts nicht verlorengehen. Restore nie unkontrolliert über eine aktive Sitzung ausführen.

Zusätzliche CLI-Kommandos: `kleinanzeiger-dashboard` für ein eigenständiges Dashboard, `kleinanzeiger-offline` für ausschließlich lokal importierte EML-/JSON-Daten ohne Plattformkontakt, `kleinanzeiger-import-mail`, `kleinanzeiger-classify`, `kleinanzeiger-generate` und `kleinanzeiger-local-smoke`. Der Offline-Modus ist ein **zusätzliches Werkzeug und kein Ersatz** für die vollständige Anzeigenverwaltung.

## Technische Abnahme und Sicherheit

Die Entwicklung und technische Abnahme sind unabhängig von einer allgemeinen externen Plattformgenehmigung. Sämtliche Mark-eigenen Read-/Write-/Media-/Sync-Funktionen werden auch ohne ein echtes Kleinanzeigen-Konto mit **zustandsbehafteten synthetischen Gegenstellen** getestet, einschließlich unklarer HTTP-Antwort, Restart, Idempotenz, beschädigter Datenbank und Recovery. Echte Fremdplattform-Zugriffe benötigen eine geeignete authentifizierte Kundensitzung und die jeweilige tatsächliche Berechtigung des Betreibers; Tests verwenden keine fingierten Credentials, keine Login-Umgehung und keinen kostenpflichtigen Provider.

Die erforderlichen CI- und Sicherheitsreviews gelten stets nur für den **konkret geprüften Commit**. Ein Merge darf nicht allein wegen grüner CI an den Review-/Captain-Gates vorbei erfolgen. Offene technische Sicherheitsbefunde werden nicht durch einen pauschalen Plattformhinweis ersetzt.

**Normative Anforderungen:** [Produktspezifikation](docs/product-spec.md), [Entscheidungen D-028 und D-029](docs/DECISIONS.md). Spezialisierte Hintergründe: [Generierung](docs/generation.md), [Synchronisation](docs/periodic-sync.md).