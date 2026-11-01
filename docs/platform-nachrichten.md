# Kleinanzeiger – Konto-Nachrichten (optionaler externer Anbieter)

Kleinanzeiger kann die Chats eines **eigenen, verbundenen Kleinanzeigen-Kontos** über eine gesondert zu aktivierende **Konto-API des unabhängigen Anbieters Kleinanzeigen Agent** lesen und auf einzelne bestehende Gespräche antworten. Dies ist **keine offizielle API von kleinanzeigen.de**, sondern ein Beta-Dienst; Kleinanzeigen kann seine Oberfläche ändern oder Konten einschränken. Ob und in welchem Umfang dein Konto dafür geeignet ist, musst du selbst beurteilen. Ein Konto mit umfangreicher Verkaufshistorie sollte nicht ungeprüft einer Drittanbieter-Beta anvertraut werden.

**Diese Funktion ist standardmäßig ausgeschaltet.** Sie erfordert einen selbst eingerichteten Konto-API-Zugang unter https://kleinanzeigen-agent.de/konto-api und die Anmeldung des eigenen Kleinanzeigen-Kontos dort. Preis laut veröffentlichter Anbieterdokumentation: **9,90 € netto/Monat zuzüglich Credits** (und Steuern). Typische Aktionen: Verbindungsstatus kostenlos, Chatliste 5 Credits, Gespräch öffnen 10 Credits (kann das Gespräch als gelesen markieren), Nachricht senden 25 Credits. Angebote und Konditionen können sich ändern; vor der Buchung aktuelle Anbieterangaben prüfen. Kleinanzeiger erwirbt selbst keine Abos und kauft keine Credits.

## Einrichtung unter Windows

1. Kleinanzeiger regulär installieren und bereits eigene Anzeigen in die bestehende lokale Datenbank einlesen. Kein Konto-API-Zugang wird dadurch erzeugt.
2. Den optionalen Drittanbieterzugang eigenständig einrichten, das Konto dort verbinden und den persönlichen `klaz_live_...`-Schlüssel dort erzeugen. Den Schlüssel **nicht** in eine ChatGPT-Unterhaltung, einen Issue-Text oder einen Git-Commit einfügen.
3. Im zentralen Windows-Kontrollfenster **Weitere Aktionen → Nachrichten verwalten** wählen. Erstmals **Nachrichten-Zugang einrichten** öffnen, den Schlüssel in das maskierte Feld eingeben und für das aktuelle Windows-Konto per DPAPI lokal speichern.
4. Das Nachrichtenfenster starten. Es wird ausschließlich an `127.0.0.1:8773` gebunden; Browser- und KI-Konto-Schlüssel, normale Chrome-Sitzungen und bestehende Backups werden nicht kopiert.
5. **Verbindung prüfen** führt auf ausdrücklichen Klick eine kostenlose Statusprüfung durch. **Chats laden**, **Chat öffnen** und **Nachricht senden** sind separate manuelle Aktionen, für die zusätzliche Credits anfallen können.
6. Nur Chats, deren Anzeigen-ID einer lokal zuvor bestätigten eigenen Anzeige zugeordnet werden kann, dürfen im Fenster geöffnet und beantwortet werden. **Ein alter lokaler Anzeigenschnappschuss garantiert nicht, dass die Anzeige heute noch dir gehört.** Vor einem Versand deshalb anhand der tatsächlich aktuellen Kontositzung, Originalanzeige und Gesprächspartner selbst prüfen.

## Antworten und unbekannte Ergebnisse

- Auf Wunsch zunächst einen **neutralen lokalen Antwortentwurf** einfügen, ihn fachlich vollständig ergänzen und die reale Chatkonversation prüfen. Ein vorhandener KI-Antwortassistent im Hauptdashboard kann nach separater Modell- und Kostenfreigabe einen Entwurf erzeugen. Der Nachrichtenbereich löst aktuell selbst keinen zusätzlichen KI-Anbieteraufruf aus.
- Vor einer Nachricht wird der **exakte Antworttext im Dialog angezeigt**. Nur die anschließende ausdrückliche Bestätigung erlaubt den einen kostenpflichtigen Versandversuch. Der Provider bestätigt höchstens die Annahme einer Nachricht, nicht deren Zustellung oder dass sie gelesen wurde.
- Kleinanzeiger speichert in einer **separaten lokalen Versandjournaldatenbank** `messages-outbox.sqlite` nur Auftrags-ID, Konto-Fingerabdruck, Anzeigen-/Konversations-ID, SHA-256 des Textes und Versandzustand. **Der Nachrichtentext, Käufername und Chatverlauf werden darin nicht gespeichert.** Bestehende Backups der Hauptdatenbank enthalten das separate Versandjournal nicht automatisch; es muss mit dem Windows-Nutzerprofil erhalten bleiben. Vor einem Gerätewechsel offene Sendestatus prüfen.
- Ein unbekanntes Ergebnis nach Timeout, Prozessabsturz oder unlesbarer Providerantwort bleibt **UNKNOWN**. Kleinanzeiger schickt denselben Auftrag nie automatisch erneut und zeigt einen unbestätigten Ausgang niemals als erfolgreich versendet. Vor einem neuen bewussten Versuch den Plattformchat unabhängig überprüfen.
- Der lokale Nachrichtenbereich **versendet beim Start, beim Laden oder beim Öffnen nie automatisch**. Ein Chat-Öffnen kann beim Anbieter allerdings bereits als gelesen zählen.

## Grenzen und Abnahme

Die genannten Transport-, Datenbank- und HTTP-Wege sind mit synthetischen Nachrichten und einem simulierten Kontoanbieter testbar. **Der echte Zugang, eine Abrechnung, Marks authentifiziertes Konto und ein Versand im echten Konto sind erst nach eigenständiger Einrichtung und ausdrücklicher Freigabe der jeweiligen Nachricht abnehmbar.** Die historische EML-Auswertung im Hauptdashboard bleibt daneben unabhängig und ist ausdrücklich **kein** Live-Postfach. Ein vollautomatischer KI-Bot, der unbeaufsichtigt allen Interessenten Nachrichten schickt, ist nicht freigeschaltet.

Anbieterdokumentation: https://kleinanzeigen-agent.de/dokumentation und https://kleinanzeigen-agent.de/konto-api
