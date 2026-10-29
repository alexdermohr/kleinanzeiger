# Lokaler Autopilot-Tagesplan – priorisierte Prüfliste

Dieser Entwicklungsstand ergänzt die bestehende Dashboard-Seite **Bestandsänderungen**
um **Priorisierter Tagesplan (lokal und kostenlos)**. Das ist eine deterministische
Aufgabenliste aus bereits gespeicherten lokalen Fakten. Sie ist **keine** autonome
KI-Ausführung und kein Ersatz für eine Plattformberechtigung. Ein Klick auf
**Aufgaben prüfen** löst nur eine lokal authentifizierte GET-Abfrage aus.

## Reihenfolge der Prüfschritte

1. **Unklare oder noch laufende KI-Anfragen:** Im persistenten
   Bestands-KI-Auftrag sind `unknown` oder `inflight` gespeichert.
   Externes Ergebnis und Kosten unabhängig prüfen; niemals neu versuchen,
   eine Schätzung als Rechnung ausgeben oder einen offenen Claim automatisch klären.
2. **Neue importierte E-Mail-Benachrichtigungen:** Mindestens eine historische
   belegte Eigenbestandsbeobachtung ist vorhanden, und der lokale EML-Gesprächsstand
   ist seit der letzten lokalen Prüfung neu. Das ist **kein** Live-Inbox-Read,
   keine aktuelle Eigentumsfreigabe und kein Sendestatus.
3. **Überfällige manuelle Wiedervorlagen:** Ein selbst eingetragener Termin ist
   vergangen. Es handelt sich ausdrücklich **nicht** um ein Plattform-Ablaufdatum.
4. **Gespeicherte KI-Vorschläge:** Ein bestehender KI-Vorschlag hat den lokalen
   Status `proposed` und wartet auf Original-/Vorschlagsvergleich und lokale
   Freigabe/Ablehnung. Daraus folgt keine Veröffentlichungsberechtigung.
5. **Manuelle Wiedervorlagen der nächsten sieben Tage.**

Das Dashboard zeigt insgesamt offene Punkte und ihre Arten an. Es kann
über **Nächste 50 Aufgaben** in globaler Prioritäts- und ID-Reihenfolge durch
größere Bestände blättern. Jede Seite liest eine neue SQLite-Snapshot-Transaktion;
bei zwischenzeitlichen Änderungen **Aufgaben prüfen** zum Neustart wählen.
Ein einzelner Aufgabenzähler darf nicht als Anzahl unterschiedlicher Anzeigen
interpretiert werden, da eine Anzeige mehrere lokale Prüfpunkte haben kann.

## Technische Grenzen

Der read-only Endpunkt `GET /api/portfolio/attention?offset=0&limit=50`
nutzt die vorhandene lokale Dashboard-Token-Prüfung und ist auf 50 Einträge je
Seite begrenzt. `offset` darf höchstens 100.000 sein. Er erzeugt keine
Tabellen, mutiert weder Anzeigen noch Reviews und liest keine neuen Plattform-
oder Anbieterinformationen. Der Antwortvertrag setzt `platform_writes=0`,
`provider_requests=0`, `provider_cost_cents=0`,
`platform_messages_read=false` und
`automatic_execution_authorized=false`.

Nur IDs, lokal gespeicherte Phasen, manuelle Termine und EML-Ereignis-IDs
werden ausgegeben: keine Käufertexte, personenbezogenen E-Mail-Inhalte,
KI-Prompts, freien Notizen oder Passwörter. Jede angezeigt Handlung verlangt
die separaten bestehenden Prüf- und Bestätigungsschritte im jeweiligen Bereich.

Für echte automatisierte Plattform-Reads/-Writes fehlen weiterhin die
externe Berechtigungsfreigabe (#111) sowie Teile des unabhängigen
Recovery-/SQLite-Sicherheitsnachweises (#76); die reale Konto- und Windows-
Endabnahme auf Marks Rechner bleibt offen. Es wurden ausschließlich lokale
synthetische Tests durchgeführt, keine kostenpflichtigen KI-Aufrufe oder
Kleinanzeigen-Aktionen.

## Tests

- Keine zusätzlichen Tabellen bei leerer SQLite-Datenbank.
- Alle fünf Kategorien, Priorität und EML-Eigentumshinweis ohne Live-Behauptung.
- 10.000 synthetische Wiedervorlagen und letzte 50 Aufgaben.
- HTTP-Tokenpflicht, strikte Query-Validierung, korrupte Datumswerte → HTTP 503.
- Node/JS-DOM-Test: nur `textContent`, kein HTML-Code aus Daten,
  seitenweises Laden, Fehler löscht veraltete Einträge, keine Plattformwrites.
