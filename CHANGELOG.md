# Änderungen am Kleinanzeiger

Hier steht, was sich für Nutzerinnen und Nutzer mit jeder veröffentlichten Version ändert.
Die wichtigsten Änderungen erscheinen bei einem verfügbaren Update direkt im Kontrollfenster.

## [1.0.13] - 2026-11-10

### Behoben
- Ein doppelter Klick auf »Pausieren« oder »Aktivieren« (oder eine Aktion auf eine bereits passende Anzeige) sperrt die Anzeige nicht mehr dauerhaft. Kleinanzeiger meldet verständlich, dass nichts geändert wurde, und die Anzeige bleibt bearbeitbar. Von älteren Versionen so gesperrte Anzeigen werden durch erneutes Anklicken derselben Schaltfläche wieder freigegeben, ohne erneute Plattformaktion.
- Nach einem unklaren Ergebnis bleibt eine Anzeige nicht mehr für immer gesperrt: Nach »Mit Kleinanzeigen abgleichen« lässt sich die Sperre ausdrücklich aufheben. Dabei wird nichts an Kleinanzeigen gesendet und nichts automatisch wiederholt.
- Nach dem Pausieren oder Aktivieren zeigt das Bearbeitungsfeld sofort den neuen Status an.
- Sammelaktionen »Auswahl pausieren/aktivieren« überspringen Anzeigen, die schon im gewünschten Zustand sind, statt die ganze Auswahl abzulehnen. Lehnt Kleinanzeigen eine einzelne Anzeige ohne Änderung ab, läuft der Stapel weiter; bei einem unklaren Ergebnis hält er weiterhin an. Unpassende Anzeigen werden mit ihrer Nummer genannt.

### Verbessert
- Ergebnis- und Statusmeldungen im Dashboard sind auf Deutsch und ohne Fachbegriffe formuliert (zum Beispiel »Pausieren: erledigt und von Kleinanzeigen bestätigt«, »Aktiv«/»Pausiert« statt »active«/»paused«).
- Offene Aktionen werden beim Start mit Anzeigennummer und Aktion genannt (zum Beispiel »Noch offen: Anzeige 123 (Pausieren)«) samt Hinweis, wie sie sich klären lassen.
- Die Kennzahlen für Aufrufe, Merker und Antworten zeigen »Noch keine Daten« beziehungsweise »1.234 (aus 87 Anzeigen)« statt »0 / 0 Ads«.

## [1.0.12] - 2026-11-09

### Behoben
- Bestimmte ungültige oder zu große Anfragen werden im Windows-Kontrollfenster wieder geordnet abgelehnt, statt die lokale Verbindung unerwartet zu trennen. Es werden dabei keine Plattformaktionen automatisch wiederholt.
- Sicherungen einer laufenden SQLite-Datenbank auf Btrfs-Dateisystemen werden nicht mehr allein wegen unterschiedlicher Gerätekennungen derselben Datei abgewiesen. Integritäts- und Wiederherstellungsprüfungen bleiben erhalten.

## [1.0.11] - 2026-11-09

### Behoben
- Nach einer erfolgreich bestätigten Änderung an einer eigenen Anzeige schließt das Dashboard die lokale Schreibbestätigung nun vollständig ab. Dadurch bleibt keine bereits erledigte Anfrage fälschlich als offen stehen.
- Bei einer fehlenden oder unklaren Bestätigung bleibt die betroffene Aktion gesperrt, ohne die Plattformänderung erneut auszulösen.

## [1.0.10] - 2026-11-08

### Verbessert
- Falls Chrome oder die Browsersteuerung beim Start nicht verfügbar ist, kann Mark im lokalen Arbeitsbereich weiterhin Artikel vorbereiten, Verkaufsvermerke pflegen und lokale KI-Aufträge verwalten.
- Der Offline-Arbeitsbereich ist auf die aktuelle Windows-Sitzung beschränkt. Plattform-Schreibaktionen und Kontoabgleich bleiben deaktiviert; frühere Programmversionen können weiterhin gestartet werden.

## [1.0.9] - 2026-11-08

### Neu
- Im Windows-Kontrollfenster unter »Weitere Aktionen → Schnäppchenjäger« eigene CSV-/JSON-Angebote lokal bewerten und als HTML-Bericht öffnen oder eine reguläre Kleinanzeigen-Suche im Browser starten. Kein automatisches Einsammeln.

### Behoben
- Zubehör, Defekte und abweichende Modell-, Editions- oder Speichervarianten werden nicht mehr als gleichwertige Preisvergleiche ausgegeben.
- Die Auswertung großer Bestände ist schneller; unbekannte Zustandsangaben gelten nicht als belegte Übereinstimmung. Nullpreis-Angebote mit Versand und voneinander abweichende Terabyte-Speicher werden nicht mehr als Preisvergleich missverstanden.

## [1.0.8] - 2026-11-08

### Verbessert
- Im Windows-Kontrollfenster kann eine ausgewählte Sicherung auch bei vorhandenem Datenbestand wiederhergestellt werden.
- Vor der Wiederherstellung wird der aktuelle Bestand automatisch als separate Sicherung mit Prüfnachweis aufbewahrt. Ein ungültiger Sicherungsstand wird vor dem Austausch abgewiesen.

## [1.0.7] - 2026-11-08

### Behoben
- Wenn beim Kontoabgleich der verwendete Chrome-Reiter nicht „Meine Anzeigen“ zeigt, erklärt Kleinanzeiger den gezielten nächsten Schritt statt fälschlich einen Internetausfall zu vermuten.
- Der fehlgeschlagene Abgleich verändert vorhandene Anzeigen nicht und startet keine automatische Wiederholung.

## [1.0.6] - 2026-11-08

### Verbessert
- Im Bestandsvergleich führt „Anzeige prüfen“ direkt zur exakt zugehörigen Eigenanzeige, auch bei großen Beständen mit mehr als 50 Suchtreffern.
- Veraltete oder mehrdeutige lokale Treffer werden nicht als passende Anzeige dargestellt; der Vergleich startet keine Konto-, Plattform- oder KI-Aktion.

## [1.0.5] - 2026-11-08

### Verbessert
- Der Hinweis zum Kontoabgleich schlägt die Schaltfläche nur vor, wenn sie in der aktuellen Sitzung tatsächlich nutzbar ist.
- Ohne Berechtigung für den Kontoabgleich erklärt das Kontrollfenster den lokalen Lesemodus. Gespeicherte Anzeigen bleiben verfügbar.

## [1.0.4] - 2026-11-07

### Verbessert
- Die Änderungsanzeige passt ihre Kurzbeschreibung automatisch an die verfügbare Breite an. „Alle Änderungen“ bleibt auch bei längeren Versionshinweisen gut lesbar und anklickbar.
- Die vollständige Liste der Änderungen ist weiterhin vor und nach einem Update erreichbar.

## [1.0.3] - 2026-11-07

### Neu
- Das Kontrollfenster zeigt die wichtigste Änderung einer verfügbaren Version direkt neben dem Updatehinweis.
- Über „Alle Änderungen ansehen“ sind die vollständigen Versionshinweise vor und nach einem Update abrufbar.
- Jede neue Windows-Veröffentlichung erhält ihre Versionshinweise verbindlich aus diesem Changelog.

### Verbessert
- Updatehinweise erscheinen weiterhin ohne automatische Installation. Ohne Internet bleibt die Anwendung bedienbar.

## [1.0.2] - 2026-11-07

### Verbessert
- Der letzte bestätigte Kontoabgleich wird gut sichtbar mit Datum und Uhrzeit angezeigt.
- Bei Anmelde- oder Verbindungsproblemen erscheinen verständliche Hinweise zum weiteren Vorgehen.
- Ein fehlgeschlagener neuer Abgleich überschreibt nicht den Zeitpunkt des letzten erfolgreichen Abgleichs.

## [1.0.1] - 2026-11-07

### Neu
- Kleinanzeiger erkennt neuere offizielle Versionen beim Öffnen des Kontrollfensters automatisch.
- Die verfügbare Versionsnummer und ein Updateknopf werden angezeigt. Die Installation erfolgt weiterhin nur auf Klick.

## [1.0.0] - 2026-11-07

### Erste Kundenversion
- Eigene Kleinanzeigen verwalten, Bestand abgleichen, Artikel importieren, Kennzahlen und Berichte ansehen.
- Entwürfe mit optionaler KI vorbereiten; kostenpflichtige Aufrufe nur nach ausdrücklicher Freigabe.
- Windows-Einrichtung mit Kundenhandbuch, Sicherung und Wiederherstellung.

Unbeaufsichtigte Massenveröffentlichungen und automatischer Nachrichtenversand sind nicht enthalten.