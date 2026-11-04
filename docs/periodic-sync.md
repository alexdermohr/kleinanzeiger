# Kleinanzeiger – regelmäßige Bestandssynchronisation

**Technische Funktion:** Der installierte Befehl `kleinanzeiger-sync` verwendet denselben abgesicherten, rein lesenden Besitzerbestand-Adapter und das SQLite-Synchronisationsjournal wie Kleinanzeiger. Er benötigt eine bereits vom Anwender authentifizierte lokale Chrome-/Chromium-Sitzung mit exakt lokal gebundenem CDP-Port. Der Befehl startet keinen Browser und führt weder Login/MFA/CAPTCHA noch Anzeigenänderungen aus.

## Automatisch im normalen Windows-Kundenprogramm

Nach einem **erfolgreichen authentifizierten Kundenstart** gleicht Kleinanzeiger
den eigenen Bestand regelmäßig mit demselben bereits geöffneten, serialisierten
Chrome-/CDP-Zugang wie „Mit Kleinanzeigen abgleichen“ ab. Der Standard
richtet sich nach der letzten vollständig bestätigten Anzeigenzahl:
bei kleinen Beständen mindestens alle fünf Minuten, bei 1.000 Anzeigen
etwa alle zwölf Minuten, bei 5.000 Anzeigen stündlich und bei 10.000 Anzeigen
alle zwei Stunden. Das begrenzt vollständige Bestandsbeobachtungen rechnerisch
auf ungefähr 120.000 Anzeigen-Snapshots pro Tag, statt bei 10.000 Anzeigen
alle fünf Minuten 2,88 Millionen lokale Snapshots pro Tag zu erzeugen.
Nach jedem erfolgreichen Hintergrundabgleich wird das Standardintervall
an den aktuellen Bestand angepasst. Es entsteht weder ein zweiter
Browseroperator noch ein Plattform-Write.

- Ein abweichendes festes Intervall lässt sich bewusst mit
  `--owner-sync-interval-seconds 900` auf 15 Minuten einstellen;
  60 bis 86.400 Sekunden sind zulässig. Bei großen Beständen kann ein
  sehr kurzes festes Intervall die Datenbank stark wachsen lassen.
- Mit `--owner-sync-interval-seconds 0` wird nur die Automatik ausgeschaltet.
- Ein belegter CDP-Zugang (durch laufenden Write oder manuellen Read) lässt
  den nächsten Zyklus ausfallen, statt konkurrierende Reads anzustauen.
- Fehlerhafte oder uneindeutige Bestandsreads verändern den letzten
  bestätigten lokalen Bestand nicht. Automatische Folgeversuche pausieren,
  bis „Mit Kleinanzeigen abgleichen“ erneut **erfolgreich** durchgeführt wurde.
- Der lokale Offline-Arbeitsbereich benutzt den Browser nicht periodisch.

Die separate CLI `kleinanzeiger-sync` bleibt für kontrollierte Einzel- und
Spezialläufe verfügbar; sie darf nicht parallel zum Kundenstarter dasselbe
Chrome-Profil oder CDP-Ziel beanspruchen.

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