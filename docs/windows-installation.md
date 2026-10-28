# Kleinanzeiger v0.1.10 – Windows 11

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
- **Artikel importieren**: CSV-/Excel-XLSX-Datei mit Artikelnummer, Titel, Beschreibung, Kategorie und vollem Europreis wählen. Die Vorprüfung zeigt Zuordnung, neue/identische Artikel und Konflikte. Nur nach Bestätigung werden die Datensätze mit automatischem Backup als lokale unveröffentlichte Entwürfe übernommen; niemals automatisch veröffentlicht.
- **Entwürfe exportieren**: alle lokalen Artikelentwürfe als CSV zum Bearbeiten und erneuten Einlesen ausgeben. Export überschreibt keine bestehenden Dateien.
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

Für größere Bestände zeigt **Meine Anzeigen** nur 50 Anzeigen pro Seite.
Über **Suche nach Titel oder Anzeigen-ID** und **Status** kann der
lokale Bestand gefiltert werden. **Weiter / Zurück** blättert seitenweise.
Die Auswahlkästchen lassen sich über Seiten hinweg sammeln; **Auswahl
als CSV** speichert ausschließlich die ausgewählten Anzeigen-IDs.
**Auswahl leeren** setzt sie zurück. CSV-Export und Markieren allein ändern **keine** Plattformanzeige.
Mit gültiger Schreibsitzung lassen sich **1 bis 10 eigene Anzeigen** nach
vollständiger Bestätigung **nacheinander pausieren oder aktivieren**.
Vor jedem Einzelauftrag wird der lokale Stand erneut geprüft. Bei
unbekanntem Ergebnis, geändertem Status, Recovery oder auf Wunsch wird
der Stapel gestoppt, ohne automatischen Plattform-Retry. **Massenlöschen,
Preisänderungen und Sammelveröffentlichung** sind nicht enthalten.
Einzelne Anzeigen werden weiterhin über **Bearbeiten** verwaltet.

Über **Mit Kleinanzeigen abgleichen** werden eigene Anzeigen direkt aus der
angemeldeten Browser-Sitzung neu gelesen. **Ansicht neu laden** aktualisiert
hingegen ausschließlich die lokal gespeicherte Darstellung.

## Interessenten und Antwortentwürfe

Unter **Meine Anzeigen** eine eigene Anzeige suchen und **Antwort vorbereiten**
anklicken. Im Bereich **Interessenten & Antworten** einen bereits erhaltenen
Nachrichtenausschnitt ohne personenbezogene Daten eingeben und nur tatsächlich
bekannte Fakten hinzufügen. **Neutrale Antwort beginnen** ist kostenlos und
arbeitet rein lokal. **KI-Antwortentwurf erzeugen** braucht den eigenen
KI-Schlüssel, die aktive KI-Sitzung und für jeden Aufruf eine neue ausdrückliche
Zustimmung zur Promptübertragung und zu möglichen Kosten. Den Text prüfen,
bearbeiten und bei Bedarf **Antworttext kopieren**.

**Kleinanzeiger liest keine vollständigen Plattform-Chats aus und versendet
keine Nachrichten.** Die Nachricht und der Antwortentwurf werden in dieser
Ansicht nicht dauerhaft gespeichert. Der Nutzer versendet den kopierten Text
selbst im originalen Kleinanzeigen-Dialog.

## Lokale KI-Stapelaufträge

Im Dashboard **KI-Stapelaufträge** öffnen. Bis zu 5.000 lokale CSV-/XLSX-Entwürfe zu einem Auftrag sammeln, Originale und Vorschläge prüfen, einzeln freigeben und nur lokal übernehmen. Optional können nach eigener Modell-, Datenübertragungs- und Budgetbestätigung bis zu zehn KI-Aufrufe nacheinander ausgeführt werden; weitere Aufrufe lassen sich stoppen. Es werden keine Anzeigen automatisch massenhaft veröffentlicht oder gelöscht. Providerkosten sind nur konservativ geschätzt und nicht garantiert.

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

## Große Produktbestände als Entwürfe vorbereiten

CSV (UTF-8) oder Excel XLSX mit den Pflichtspalten `sku,title,description,category,price_eur` und optional `images`. Deutsche Spaltennamen `Artikelnummer,Titel,Beschreibung,Kategorie,Preis,Bilder` werden automatisch zugeordnet. Kategorie als `Haus & Garten > Dekoration`, Preis als ganze Euro, bis 5000 Artikel je Datei, bis zehn Bilddateinamen (Semikolon-getrennt). Die Bilddateinamen sind nur Hinweise und noch keine Plattformuploads. Ein bestehender Artikel mit derselben SKU und abweichendem Inhalt wird ohne Änderung abgewiesen. Im Dashboard den Bereich **Artikelentwürfe** öffnen, suchen, seitenweise durchsehen und einen Entwurf mit ausdrücklichem Klick in das separate Neuanzeigenformular übernehmen. Dieser Schritt **veröffentlicht keine Anzeige** und erteilt keine Eigentümerfreigabe. Für Details siehe [Artikelimport](portfolio-import.md).


## Wenn Chrome angemeldet ist, das Dashboard aber nicht startet

**Ab Version 0.1.10:** Kleinanzeiger erkennt mehrere offene Tabs im eigenen
Chrome-Profil und sucht den richtigen Tab **Meine Anzeigen** statt pauschal
nur ein einziges Browserfenster vorauszusetzen. Bei mehreren solchen Tabs
verwendet es eine fest ausgewählte Tab-Identität; private Tabs und offene
Anzeigen-Editoren werden nicht geschlossen oder ungefragt umgeleitet.
Der Browser- und Lesezugriff bleibt lokal und lesend. Die Oberfläche zeigt
**Fehlerdetails anzeigen** unter der Statuszeile.



Kleinanzeiger verwendet ein eigenes Chrome-Profil unter
%LOCALAPPDATA%\Kleinanzeiger\ChromeProfile. Die Anmeldung in einem anderen
Chrome-Fenster genügt nicht. Ist **Meine Anzeigen** bereits in dem von
Kleinanzeiger geöffneten Chrome-Fenster sichtbar, **ist die Meldung kein
Beweis für eine fehlende Anmeldung**.

Die Version 0.1.10 prüft nach dem Öffnen von Chrome höchstens 12 Sekunden, ob
dessen lokale Browsersteuerung auf 127.0.0.1:9222 antwortet. Erst dann
wird der eigentliche Dienst gestartet. Bei einer nicht erreichbaren Steuerung
wird ein eigener Hinweis statt einer vermeintlichen Login-Fehlermeldung
angezeigt. Falls der Dienst später scheitert, im Kontrollfenster **Details**
öffnen: dort steht die technische Fehlermeldung und der Exitcode.

Wenn die Browsersteuerung nicht erreichbar ist, zunächst **nur die von
Kleinanzeiger gestarteten Chrome-Fenster** vollständig schließen und über
**Kleinanzeigen-Konto öffnen** neu aufrufen. Das private Standard-Chrome,
das Chrome-Profil, die Datenbank und die Backups **nicht löschen**.
Ein neu aufgerufener Browser-Tab beweist noch nicht, dass das
Chrome-Debugging verbunden ist. Der Test auf einem anderen PC ersetzt keine
Abnahme auf dem Kundenrechner.
