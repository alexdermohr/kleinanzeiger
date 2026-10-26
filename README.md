# Kleinanzeiger

**Kleinanzeiger** verwaltet eigene Kleinanzeigen über eine lokale Weboberfläche: Anzeige erstellen und veröffentlichen, Bilder zuordnen, Titel und Beschreibung bearbeiten, pausieren, aktivieren, löschen, Bestand synchronisieren sowie Historie, Views, Reaktionen, E-Mail-Daten und Auswertungen einsehen. Hinzu kommen KI-Text- und Bildentwürfe sowie lokale Datensicherung und Wiederherstellung.

- **Repository:** https://github.com/alexdermohr/kleinanzeiger
- **Python:** ab 3.12; CI-Zielmatrix Python 3.12 und 3.13
- **Distribution:** `kleinanzeiger`; bestehende interne Module `mark_api` und `mark-api-*`-Kommandos bleiben kompatibel
- **Technischer Lieferstatus:** Erst die bestandenen Tests, der Installationsnachweis, ein unabhängiger Sicherheitsreview und ein freigegebener Merge des konkreten Liefercommits ergeben die Produktabnahme. Kein Ersatz durch ein rein lokales Dashboard.

## Native Windows-Kundenanwendung (ab v0.1.5)

Die vollständige Windows-11-x64-Ausgabe benötigt **kein WSL**.
Das signatur- und prüfsummengebundene Windows-Paket ist unter
[Releases](https://github.com/alexdermohr/kleinanzeiger/releases) verfügbar.
Das ZIP vollständig entpacken und **Kleinanzeiger.cmd** starten.
Das zentrale Windows-Fenster bietet Installation, Update, Start/Stop,
Konto-Anmeldung, Backup/Restore, KI-Einrichtung, **lokalen EML-Import** und
**Vergleichslabels (Bildtyp/Stadt/Texttyp/Titeltyp) mit Anzeigenauswahl**.
Das Dashboard bündelt Anzeigenkarten, sämtliche Create/Media/Content/State/Delete-
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