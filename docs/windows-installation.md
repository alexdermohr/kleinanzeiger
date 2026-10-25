# Kleinanzeiger v0.1.3 – Windows 11

Native Windows-Anwendung ohne WSL, Ubuntu oder Docker.

Der bisherige praktische Windows-Test wurde vom Entwickler durchgeführt.
Eine unabhängige Kundenabnahme und echte Schreibaktionen im Kundenkonto
sind damit nicht belegt.

## Automatisch installieren

1. Windows-ZIP vollständig in einen eigenen Ordner entpacken.
2. **Installieren.cmd** doppelklicken. Es prüft die Wheel-Prüfsumme,
   richtet Python und die Abhängigkeiten ein und stellt bei Bedarf
   Google Chrome bereit. Falls Python 3.12/3.13 fehlt, lädt es den
   offiziellen, auf SHA-256 und Herstellersignatur geprüften Installer
   für Python 3.13.17 von python.org. Fehlt Chrome, wird eine signierte
   Chrome-for-Testing-Laufzeit von Google für diesen Benutzer bezogen.
   Internetverbindung erforderlich, keine WSL- oder Docker-Installation.
3. **Starten.cmd** doppelklicken; das separate Chrome-Fenster öffnet sich.
4. Dort selbst beim eigenen Kleinanzeigen-Konto anmelden und im
   Startfenster ENTER drücken. Lokale Dashboard-URL öffnen.
5. Unter **Meine Anzeigen** auf **Bearbeiten** klicken. Rechts erscheinen Titel,
   Beschreibung und Status zur Bearbeitung. Alternativ im Dropdown
   **Vorhandene Anzeige auswählen** die gewünschte Anzeige wählen.
   Die Anzeigen-ID wird automatisch und sicher übernommen.
6. **Neue Anzeige** öffnet das Erstellformular. **Auswertungen** und die
   optionale **KI** lassen sich bei Bedarf aufklappen. Technische Details
   liegen in den jeweiligen Info-Bereichen, nicht mehr im Arbeitsfluss.

Die Vollversion umfasst Anzeigen-Synchronisation, Erstellen und
Veröffentlichen, Bearbeiten, Pause/Aktivieren/Löschen, Bilder,
Dashboard, Historie, Rankings, Analytics und lokale E-Mail-Reaktionen.

## Update von v0.1.1 oder v0.1.2

1. Anwendung und deren Chrome-Fenster vollständig beenden.
2. Im alten Ordner **Sichern.cmd** ausführen und Backup sowie
   zugehörige .manifest.json-Datei sicher aufbewahren.
3. Neues ZIP in **einen neuen Ordner** entpacken.
4. Dort **Installieren.cmd** ausführen, anschließend **Starten.cmd**.

Daten liegen weiterhin unter %LOCALAPPDATA%\Kleinanzeiger:
anzeigen.sqlite (inkl. WAL/SHM), vorbereitete Medien, offene und
ungeklärte Write-Vorgänge, ChromeProfile und Backups bleiben erhalten.
Der Installer löscht sie nicht und führt keine Wiederherstellung aus.

**Wiederherstellen.cmd** funktioniert ausschließlich, wenn die
Zieldatenbank und ihre -wal/-shm/-journal-Dateien fehlen. Niemals
bestehende Daten überschreiben.

## KI anschließen (optional)

1. Eigenen API-Schlüssel auf https://platform.openai.com/api-keys
   erstellen und im OpenAI-Konto Abrechnung und Limits prüfen.
   Ein ChatGPT-Abo ist kein API-Guthaben.
2. **KI-einrichten.cmd** ausführen; Schlüssel verdeckt eingeben.
   Er wird mit Windows-DPAPI benutzergebunden verschlüsselt
   unter %LOCALAPPDATA%\Kleinanzeiger\openai-key.dpapi gespeichert.
   Nicht in Quellcode, Dashboard oder Paket-Archiv hinterlegen.
3. Mit **Starten.cmd** neu starten. Bei vorhandenem Schlüssel aktiviert
   der Starter automatisch die vorhandenen KI-Funktionen. Im Dashboard
   Text- und Bildentwürfe erzeugen und in das Formular übernehmen.
4. Jeder kostenpflichtige API-Aufruf benötigt eine separate ausdrückliche
   Bestätigung. Entwürfe veröffentlichen niemals automatisch.

Die Funktion kann durch Entfernen der Datei openai-key.dpapi bei
beendeter Anwendung deaktiviert werden. Ohne Schlüssel bleibt der
Rest des Produkts uneingeschränkt verwendbar. Nur Daten an OpenAI
übergeben, die hierfür freigegeben sind.

## Hinweise

Bei Download-Fehlern Erreichbarkeit von python.org,
googlechromelabs.github.io, storage.googleapis.com und pypi.org
prüfen. SHA- oder Signaturkontrollen nicht deaktivieren.
CDP darf nur auf 127.0.0.1 und ausschließlich im separaten
Chrome-Profil betrieben werden. Login, MFA und CAPTCHA bleiben
manuelle Schritte.

Die Plattformoperationen wurden synthetisch getestet, nicht im
Kundenkonto schreibend abgenommen. Die Linux-A6-Migration
https://github.com/alexdermohr/kleinanzeiger/issues/76 bleibt getrennt.