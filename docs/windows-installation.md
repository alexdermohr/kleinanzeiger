# Kleinanzeiger v0.1.5 – Windows 11

## Einmal entpacken, dann nur noch Kleinanzeiger öffnen

1. Das vollständige Windows-ZIP in einen eigenen Ordner entpacken.
2. **Kleinanzeiger.cmd** doppelklicken. Das Kleinanzeiger-Fenster öffnet sich.
3. **Einrichten und starten** anklicken. Falls Python 3.12/3.13 fehlt, wird
   es aus einer offiziellen, SHA-256- und signaturgeprüften Python-Datei
   automatisch bereitgestellt. Kleinanzeiger und seine Abhängigkeiten werden
   isoliert installiert. Fehlendes Chrome wird signiert von Google geladen.
4. Im geöffneten separaten Chrome-Profil **bei Kleinanzeigen anmelden**.
   Danach gegebenenfalls im Kleinanzeiger-Fenster erneut **Starten** klicken.
   Das Dashboard öffnet sich automatisch.
5. Danach den Desktop-Eintrag **Kleinanzeiger** benutzen. Der ursprünglich
   entpackte Installationsordner wird für den normalen Start nicht benötigt.

Keine Konsoleingaben, manuelles Python, Docker, WSL, Ubuntu oder Kopieren
technischer Dashboard-URLs erforderlich.

## Alles an einem Ort

Das Kleinanzeiger-Fenster bietet:

- **Kleinanzeiger starten / beenden**: öffnet das Konto und danach das
  Dashboard automatisch. Beenden nutzt den privaten stdin-Kanal und den
  vorhandenen kontrollierten Shutdown. Laufende Änderungen zuvor abschließen.
- **Installieren / aktualisieren**: prüft das offizielle GitHub-Release,
  ZIP-/Wheel-SHA-256 und Manifest. Vor einem Update wird automatisch ein
  SQLite-Backup mit Manifest erstellt.
- **E-Mails importieren**: importiert ausdrücklich ausgewählte lokale Kleinanzeigen-EML-Dateien (vorher die Anwendung beenden) und erstellt davor eine verifizierte Sicherung.
- **Anzeigen vergleichen**: wählt bestehende oder durch E-Mail-Import belegte Anzeigen aus und pflegt Bildtyp, Stadt, Texttyp und Titeltyp für Gruppenrankings (ebenfalls bei beendetem Produkt).
- **Handbuch / Funktionen**: öffnet die vollständigen HTML-Kundenunterlagen aus dem installierten Paket.
- **Sicherung erstellen**: sichert vorhandene Daten online, ohne eine
  Datenbankdatei manuell zu kopieren.
- **KI einrichten**: eigenen OpenAI-API-Schlüssel verdeckt eingeben oder
  entfernen. Er wird per Windows-DPAPI an das lokale Benutzerkonto gebunden.
  Jeder externe kostenpflichtige Text-/Bildaufruf benötigt weiterhin eine
  eigene Bestätigung im Dashboard.
- **Kleinanzeigen-Konto öffnen**: für die erste Anmeldung und MFA im
  getrennten Chrome-Profil.
- **Weitere Optionen / Wiederherstellen**: nur bei noch nicht vorhandener
  Datenbank samt WAL/SHM/Journal, mit ausdrücklicher Zustimmung und
  Manifestprüfung.

Das Dashboard bleibt die zentrale Arbeitsfläche: Anzeigenkarten auswählen,
rechts bearbeiten, neue Anzeigen erstellen sowie Analytics und KI-Entwürfe
bei Bedarf aufklappen.

Über **Mit Kleinanzeigen abgleichen** werden eigene Anzeigen direkt aus der
angemeldeten Browser-Sitzung neu gelesen. **Ansicht neu laden** aktualisiert
hingegen ausschließlich die lokal gespeicherte Darstellung.

## Bestehende Daten und Updates

Die stabil installierten Programmdateien liegen unter
%LOCALAPPDATA%\Kleinanzeiger\App, die Datenbank unter
%LOCALAPPDATA%\Kleinanzeiger\anzeigen.sqlite. Backupdateien,
WAL/SHM, Recovery-Anfragen, Medien und das separate ChromeProfile bleiben
beim Update erhalten. Laufende Dashboard-/Write-Prozesse blockieren ein
Update, bis die bisherige Anwendung kontrolliert beendet ist.

Die technische Prüfung nutzt synthetische Plattformgegenstellen: keine
echten Anzeigenänderungen, kein unbestätigter kostenpflichtiger KI-Aufruf.
Die separate Linux-A6-Altbestandsmigration (#76) ist kein Windows-Blocker.

Das vollständig integrierte Handbuch heißt `Kleinanzeiger-Handbuch.html`, die
Funktionsübersicht `Kleinanzeiger-Funktionen.html`. Beide Dateien liegen im
Windows-Paket und sind über das zentrale Kontrollfenster erreichbar.
