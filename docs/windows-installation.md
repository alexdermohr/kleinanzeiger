# Kleinanzeiger – Windows 11

**Version 1.0.2 · Windows 11 (64 Bit) · ohne WSL**

## Erste Einrichtung

1. Das offizielle `Kleinanzeiger-v1.0.2-Windows.zip` von [GitHub Releases](https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.0.2) herunterladen und **vollständig entpacken**.
2. `Kleinanzeiger.cmd` doppelklicken. `START-HIER.html` bietet eine kurze Einführung.
3. Im Kontrollfenster **Einrichten und starten** wählen. Wenn Python oder Chrome fehlt, braucht die Einrichtung Internet.
4. **Kleinanzeigen-Konto öffnen**: Im eigens gestarteten Chrome anmelden und gegebenenfalls die Sicherheitsbestätigung durchführen.
5. Kleinanzeiger starten, im Dashboard **Mit Kleinanzeigen abgleichen** anklicken und den Bestand prüfen.

Das Dashboard zeigt den **letzten bestätigten Abgleich mit Datum und Uhrzeit**. Bei fehlender Anmeldung oder Browser-/Internetverbindung stehen konkrete nächste Schritte direkt beim Status. Ein gescheiterter Versuch ersetzt den Zeitpunkt des letzten erfolgreichen Abgleichs nicht.

## Im Alltag

- **Anzeigen bearbeiten:** Unter **Meine Anzeigen**. Änderungen und Löschungen erfordern eine bewusste Bestätigung.
- **Artikel importieren:** CSV-/Excel-Entwürfe bleiben lokal bis zur Veröffentlichung.
- **KI einrichten:** Eigener Anbieterzugang erforderlich; mögliche Kosten werden vor dem Aufruf angezeigt.
- **Sicherung erstellen:** Für deinen lokalen Datenbestand empfohlen.
- **Handbuch / Funktionen:** Direkt im Kontrollfenster oder als HTML-Dateien im entpackten Ordner.

## Aktualisieren

Beim Öffnen prüft das Kontrollfenster im Hintergrund, ob eine neuere offizielle Version verfügbar ist. Ist das der Fall, zeigt es die Version und einen Updateknopf. Die Installation beginnt nur nach deinem Klick; ohne Netzwerk bleibt das Programm benutzbar.


Kleinanzeiger zuerst beenden, dann **Installieren / aktualisieren** wählen. Das Update prüft die neue Programmversion und erhält vorhandene Daten, Chrome-Profilordner und eine vorherige Programmumgebung als Rückfallmöglichkeit.

## Wenn etwas nicht funktioniert

- **Browsersteuerung:** Prüfen, ob das **Kleinanzeiger-Chrome**, nicht nur dein normales Chrome, angemeldet ist.
- **Keine Anzeigen sichtbar:** Anmeldung, Internetverbindung und Filter prüfen; einen erneuten kontrollierten Abgleich starten.
- **Lokaler Arbeitsmodus:** Vorhandene Daten sind lesbar, Plattformänderungen gesperrt. Für Schreibaktionen nach bestätigtem Abgleich neu starten.
- **Fehler nach einem Update:** Im Kontrollfenster **Fehlerdetails anzeigen** oder **Diagnose kopieren** wählen. Keine Passwörter, Tokens oder privaten Anzeigeninhalte weitergeben.
- **Daten wiederherstellen:** Nur über **Sicherung wiederherstellen**, niemals laufende Datenbankdateien überschreiben. Profile, Datenbank oder Backups nicht löschen.

Mehr dazu im [Benutzerhandbuch](Kleinanzeiger-Handbuch.html) und der [Funktionsübersicht](Kleinanzeiger-Funktionen.html).
