# Kleinanzeiger – regelmäßige Bestandssynchronisation

**Technische Funktion:** Der installierte Befehl `kleinanzeiger-sync` verwendet denselben abgesicherten, rein lesenden Besitzerbestand-Adapter und das SQLite-Synchronisationsjournal wie Kleinanzeiger. Er benötigt eine bereits vom Anwender authentifizierte lokale Chrome-/Chromium-Sitzung mit exakt lokal gebundenem CDP-Port. Der Befehl startet keinen Browser und führt weder Login/MFA/CAPTCHA noch Anzeigenänderungen aus.

## Benutzung

```bash
# Einzelne bestätigte Bestandsbeobachtung auf bestehender Datenbank
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --once

# Initialer Read und anschließende Aktualisierung alle fünf Minuten
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --immediate --interval-seconds 300

# Ohne --immediate: erster Read nach dem ersten Intervall
kleinanzeiger-sync --db /pfad/kleinanzeiger.sqlite --cdp-port 9222 --interval-seconds 300
```

Das zulässige Intervall beträgt 60 bis 86.400 Sekunden. Ein neuer Poll beginnt erst nach Abschluss des vorigen Durchlaufs; keine Catch-up-Bursts. **Die SQLite-Datei muss bereits existieren** und wird durch diesen Einstieg niemals stillschweigend ersetzt oder neu angelegt.

**Keine parallelen Chrome-Operatoren:** Den Synchronisierer nicht gleichzeitig mit dem Produktstarter oder einem anderen Writer auf demselben Browserziel ausführen. Die bestehende exklusive CDP-Zielreservierung verweigert konkurrierende Zugriffe der eigenen Prozesse. Sie ersetzt keine Browser- oder Account-Authentifizierung und garantiert keine Koordination mit fremden Programmen.

## Fehler- und Wiederanlaufvertrag

- Vor jedem Browserread wird ein `in_progress`-Versuch mit Zeitpunkt und Quelle im vorhandenen SQLite-Journal angelegt.
- Nur ein erfolgreich bestätigter Besitzerread speichert Bestands-Snapshots und `success_empty` beziehungsweise `success_nonempty` atomar mit dem Journalabschluss. Eigene, bisher vorhandene Anzeigen können erst aufgrund bestätigter Abwesenheit auf `ABSENT` wechseln.
- Fehlgeschlagene, beschädigte, unauthentifizierte oder uneindeutige Reads überschreiben weder die bisherige Anzeigenhistorie noch die letzte erfolgreiche Beobachtung. Sie beenden den Polling-Lauf ohne automatischen Blind-Retry und zeigen einen begrenzten Fehler.
- Ein durch Prozessabbruch offener Versuch bleibt `in_progress`/ungeklärt, niemals ein behaupteter Erfolg. Ein erneuter Aufruf prüft den tatsächlichen Bestand neu und verwendet keine vorherige unbekannte Antwort als Bestätigung.
- `SIGINT` und `SIGTERM` stoppen weitere Polls kontrolliert. Ein bereits begonnener Read kann nur nach seiner eigenen konservativen Abbruch-/Journalbehandlung enden; `SIGKILL` kann einen ungeklärten Datensatz hinterlassen.

Die Analyseoberfläche zeigt **Synchronisationszeit und Herkunft getrennt von der Aktualität einzelner Kennzahlen**. Eine lokale E-Mail- oder Katalogdatei erzeugt keinen bestätigten Besitzerstatus. Für die vollständige Endabnahme sind die installierte Dashboard-/Write-/Sync-Kette sowie Backup und Wiederherstellung auf demselben Liefercommit zu prüfen.
