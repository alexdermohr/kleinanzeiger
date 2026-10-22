# Produktspezifikation

Stand: 22.10.2026 (Lieferziel und technischer Arbeitsmodus aktualisiert; Ergebnisse revisionsgebunden)

Status: **Gesamtprodukt noch nicht abgenommen.** Die frühere Beobachtung eines pauschal blockierten Private-Web-Produktstarters war an `8ea011d562ab41f6c7d49e38781b81fbe76d356c` gebunden und ist kein aktueller Live-Nachweis. Neuere Integrationsstände und der spätere Liefercommit müssen frisch geprüft werden. Der vollständige Lieferumfang einschließlich Write bleibt unabhängig von der historischen Sperre verbindlich.

Quelle: `docs/requirements-source-2026-09-23.md`

## Verbindlicher Liefer- und Arbeitsmodus (D-028/D-029)

**Produktidentität:** **Kleinanzeiger**, kanonisches Repository `alexdermohr/kleinanzeiger`, Distributionsname `kleinanzeiger`, Bedienstart `kleinanzeiger`. Die internen Python-Module `mark_api` und bisherige CLI-Aliase werden zur Kompatibilität erhalten.

**Kundenerfolg ist der Maßstab:** Kleinanzeiger wird als **installierbares Gesamtprodukt mit vollständigen Write-Funktionen** geliefert. Das umfasst Create/Publish, Medien-/Bildvorbereitung und Media-Create, Titel-/Beschreibungspflege, Pause, Reaktivierung, Delete sowie Bestandssynchronisation, Analytics, Prompt-basierte Text-/Bildgenerierung und sichere Datenwiederherstellung. Ein Offline-Dashboard oder nur isoliert getestete Adapter erfüllen diesen Vertrag nicht.

**Plattformfreigabe ist kein Entwicklungsauftrag und kein pauschales technisches Gate.** Die fehlende generelle externe Kleinanzeigen-Freigabe blockiert weder die Implementierung noch die technische Abnahme dieser Funktionalität. End-to-End-Tests verwenden eine frisch installierte Kleinanzeiger-Distribution samt echter UI, API, Persistenz und zustandsbehafteten synthetischen Plattformgegenstellen, einschließlich der positiven und unklaren Write-Ausgänge. Die tatsächliche Berechtigung zum späteren externen Einsatz ist getrennt vom Kunden/Betreiber je konkretem Zugriffsweg zu klären und wird weder erfunden noch durch lokale Schalter ersetzt. Bestehende Transport-, Konto-, Login-, CSRF-, Ownership-, Idempotenz- und Recovery-Schranken bleiben unverändert; keine unberechtigten Live-Writes oder bezahlten Provideraufrufe zu Testzwecken.

**Ein gemeinsamer Lieferkandidat statt Prozessschleifen:** Bestehende relevante PRs sicher konvergieren, bestätigte Produkt-/P1-/P2-Fehler beheben, die komplette installierte Benutzerkette auf dem exakten Kandidaten testen und risikogerecht unabhängig prüfen. Zusätzliche Entwicklungszweige, Review-Ebenen oder Architektur entstehen nur bei belegtem Nutzen. Der Abschluss trennt technisch fertig und synthetisch belegt von tatsächlich autorisiertem Live-Einsatz.

## 0. Lieferziel und nachprüfbare Endabnahme (Kundenauftrag 18.10.2026)

Der Auftraggeber erwartet ein **vollständig benutzbares Produkt**, nicht nur eine lokale Offline-Auswertung und nicht nur isoliert getestete Bibliotheksbausteine. Eine allgemeine „Plattformfreigabe“ wird gemäß D-028 nicht als pauschaler **technischer** Fertigstellungsblocker gewertet. Die praktische Einsetzbarkeit eines konkreten Zugriffs muss davon getrennt berücksichtigt werden; Berechtigungen, die für eine bestimmte Schnittstelle tatsächlich erforderlich sind, werden nicht erfunden.

Die Abnahme muss an einer frischen Installation aus dem **exakten Liefercommit** folgende zusammenhängenden Pfade nachweisen:

- **Start und Bestand:** dokumentierte Einrichtung, vorhandene SQLite-Datenbank und sichere Erstinitialisierung; benutzbarer Product Launcher, authentifizierte eigene Inventarbeobachtung, erneuter kontrollierter Sync und sichtbarer Aktualitäts-/Fehlerzustand; kein stilles Ersetzen einer verlorenen Datenbank.
- **Verwaltung:** aus dem Dashboard heraus nutzerseitig ausgelöste Create-/Media-, Titel-/Beschreibungs-, Pause-/Aktivierungs- und Delete-Operationen über die bestehende Write API. Positiv-, Ablehnungs-, unklarer Ausführungs- und Wiederanlauffall müssen jeweils korrekt bedienbar sein. Eine reine Adapter-/Mock-Existenz genügt nicht.
- **Auswertung:** eigene Anzeigen und belegte Kennzahlen, Reaktionen/Interessenten, lokale E-Mail-Importe, Labels, Gruppen, Grafiken und Top-Listen samt Zeit-/Quellenprovenienz. Die offene fachliche Zielmetrik wird vom Anwender ausdrücklich ausgewählt; ohne Auswahl wird keine objektive „beste Lösung“ behauptet.
- **Text- und Bildgenerierung:** Die ursprünglich gewünschten, aus Nutzerprompts erzeugten Titel, Beschreibungstexte und Bilder sind Bestandteil der **finalen Vollprodukt-Abnahme**, nicht bloß eines optionalen Ideenpools. Ergebnisse müssen im installierten Produkt erzeugt, angezeigt, ausgewählt beziehungsweise bearbeitet und bei Bildern an die vorhandene Anzeigen-/Media-Kette übergeben werden können. Der Dienst-/Modell-Ausfall ist bedienbar zu behandeln; aus Generierung allein folgt keine Veröffentlichung ohne Nutzeraktion. Die frühere Depriorisierung aus D-005 gilt nur für den bisherigen MVP-Zwischenschritt.
- **Betrieb und Wiederherstellung:** reproduzierbarer normaler Start/Stop, Shutdown, SQLite-Backup/Restore, konservative Reconciliation von unklaren Writes, verständliche Status- und Fehleranzeigen. Tests umfassen Prozessunterbrechung, Datenbankausfall und verlorene HTTP-Antworten entsprechend dem jeweiligen realen Risiko.
- **Belege:** installierter Paket-/CLI-/Browser-Smoke, vollständige Regressionssuite, aktuelle CI, Sicherheitsprüfungen der tatsächlich geänderten Pfade sowie ein End-to-End-Abnahmeprotokoll für die geprüfte Revision. Automatisierte Tests lösen keine unbeabsichtigten realen Anzeigenaktionen aus; eine spätere bewusste reale Kundenabnahme ist davon getrennt.

Die vorhandenen technischen Authentifizierungs-, Owner-/ID-, CSRF-/Origin-, Capability-, Idempotenz-, Confirmation-, Media- und Recovery-Schutzfunktionen sind Teil des **voll funktionsfähigen** Produkts und werden nicht zur Abkürzung der Fertigstellung abgeschaltet. Ein pauschales Freigabe-NO-GO ersetzt umgekehrt keinen konkreten Produktfehler. Unbelegte Aussagen über Plattformrechte, Testerfolg oder Produktionsreife sind kein Abnahmekriterium.

## 1. Ziel

Für den Kleinanzeigen-Account des Kunden soll eine Lösung entstehen, die Anzeigen verwaltet und Leistungsdaten in einem Dashboard auswertet. Aus den gesammelten Daten sollen Vergleiche, Ranglisten und Optimierungsvorschläge entstehen.

Die ursprüngliche MVP-Reihenfolge vom 24.09.2026 war nur ein Zwischenziel. **Für das verbindliche Endprodukt sind Text- und Bildgenerierung einschließlich Übernahme in die Anzeigenverwaltung Pflichtumfang** (D-028/D-029). Implementierung, Integration und nutzbare Funktion sind auf dem späteren Liefercommit nachzuweisen.

## 2. Belegter Funktionsumfang

### 2.1 Anzeigen

Der gewünschte Oberbegriff „Anzeigen verwalten“ ist für den vorhandenen MVP-Core inzwischen technisch präzisiert als:

- bestehende eigene Anzeigen synchronisieren,
- Titel und Beschreibung einer bestehenden Anzeige in-place aktualisieren,
- pausieren beziehungsweise reservieren,
- aktivieren,
- löschen,
- Besitzerstatus sowie Views, Merker und Replies lesen,
- Inbox/Conversation einer Anzeigen-ID zuordnen.

Create/Publish ist zusätzlich als eigener, stärker gegateter Write-Pfad implementiert. Daraus folgt keine automatische Produktionsfreigabe.

### 2.2 Textgenerierung — Pflichtumfang der Endlieferung

- Titel aus Prompts generieren.
- Beschreibungstexte aus Prompts generieren.
- Generierte Inhalte für Anzeigen verwenden.

Textgenerierung muss im installierten Produkt vom Prompt bis zum übernehmbaren und bearbeitbaren Anzeigenentwurf funktionieren. Kostenpflichtige Provideraufrufe erfordern gesonderte Zustimmung pro Aufruf.

### 2.3 Bildgenerierung — Pflichtumfang der Endlieferung

- Bilder aus Prompts generieren.
- Generierte Bilder automatisch für Anzeigen verwenden.

Generierte Bilder müssen im installierten Produkt sicher vorbereitet und in die bestehende Anzeigen-/Media-Kette übergeben werden können. Eine Übernahme veröffentlicht nichts ohne gesonderte Benutzeraktion. Der vorhandene Media-Create-Pfad ersetzt keine Bildgenerierung.

### 2.4 Dashboard und Product Launcher

Die Daten-/Analytics-Surface des Dashboards ist read-only und stellt Anzeigen-, Reaktions- und Analytics-Daten dar; Grafiken und Ranglisten verwenden ausschließlich explizit angeforderte Rohmetriken. Der standalone Einstieg `mark-api-dashboard` bleibt vollständig GET-only/read-only. Im normalen Product Launcher ergänzt dieselbe sichtbare Oberfläche eine getrennt abgesicherte Write UX, die ausschließlich an die bestehende lokale Write API delegiert.

Die implementierte, auf dem geprüften Main-Stand `8ea011d` **noch nicht ausführbare** Komposition von `mark-api-launch` ist für den kohärenten Produktstart ausgelegt für eine bereits laufende, bereits authentifizierte lokale Chrome-/Chromium-Sitzung über loopback-CDP. Er führt genau einen frischen Owner-Inventory-Read aus und persistiert bestätigte aktuelle Anzeigen sowie `ABSENT`-Transitions für historisch bekannte, nun fehlende Anzeigen. Optional importiert er danach einen vom Nutzer ausdrücklich über `--email FILE` angegebenen lokalen Batch von Kleinanzeigen-Nachrichtenkopien mit der bestehenden idempotenten E-Mail-Importlogik in denselben Store. Erst nach erfolgreichem Initial-Read und einem gegebenenfalls angeforderten erfolgreichen Reaction-Import wird zuerst die bestehende loopback-only Mark Write API gestartet und anschließend das Dashboard an deren tatsächliche Loopback-Adresse gebunden. Der Launcher erzeugt dafür pro Prozess einen neuen lokalen Write-API-Bearer sowie einen getrennten Dashboard-Write-Token. Letzterer wird ausschließlich als URL-Fragment der lokalen Dashboard-URL übergeben, vom Browser in `sessionStorage` übernommen und unmittelbar aus der sichtbaren URL entfernt; der Backend-Bearer wird nicht an Browser-JavaScript exponiert.

Die Product-Launcher Write UX unterstützt die bereits implementierten Create-, Media-, Content-, State- und Delete-Surfaces. Ihre Same-Origin-Proxy-Schicht ist strikt route-/header-/body-allowlistet, verlangt den per-process Dashboard-Token, einen passenden `Origin` und einen CSRF-Marker und öffnet kein CORS. Sie implementiert keine eigene Plattform-Write-Semantik: Capability, persistente Write-API-Idempotenz, Ownership, Confirmation, TOCTOU, Post-Readback, Media-Persistenzevidenz und `platform_retry_authorized=false` bleiben ausschließlich beim vorhandenen Write-API-/Core-Pfad. Zusätzlich persistiert der Dashboard-Proxy vor jeder Plattformweiterleitung den normalisierten UI-Request und dessen Idempotency-Key als lokalen Pending-Fence in derselben SQLite-Datei. Bei unbekanntem Transportausgang wird nicht automatisch erneut gesendet; ein zweiter Tab oder neuer Key für dieselbe Ressource wird blockiert, und eine manuelle Wiederholung verwendet exakt denselben gespeicherten Request/Key. Nach Tab- oder Dashboard-Neustart kann die UI die Pending-Records mit dem aktuellen per-process UI-Token wieder lesen; Persistenzfehler bleiben fail-closed. Ein Pending-Fence wird erst durch einen separaten lokalen Browser-ACK für exakt Scope und Idempotency-Key geräumt, nachdem die UI einen gebundenen terminalen Backend-Response oder sicheren Clientfehler tatsächlich verarbeitet hat; bei verlorenem Response oder ACK bleibt der ursprüngliche Request gebunden. Partielles lokales Media-Staging verwirft bereits bekannte Refs über eine capability-gebundene idempotente Discard-Primitive; sie ist keine Plattformoperation. Verlorene Antworten nach bereits erfolgreichem lokalem Stage können keinen dauerhaften Handle-Leak erzeugen: ungenutzte Handles laufen nach 15 Minuten ab und werden vor dem nächsten Stage/Resolve lazy aus dem lokalen Kontingent entfernt. Ein zusätzlicher `--enable-writes`-Schalter existiert weiterhin nicht. Ein fehlgeschlagener oder unklarer Initial-Read oder ein fehlerhafter ausdrücklich angeforderter Mail-Batch bricht den Start vor beiden HTTP-Surfaces ab. Mailbox-Suche, Messaging-Gateway-Reads und periodische Aktualisierung bleiben außerhalb dieses Slices.

### 2.5 Datensammlung und Auswertung

Gewünscht und technisch abgebildet sind mindestens:

- Aufrufe,
- getrennte Rohmetriken für Konversationen, eindeutige Interessenten und eingehende Nachrichten,
- daraus berechnete deskriptive Kennzahlen,
- Grafiken,
- Top-Listen.

Der Datenvertrag hält `conversation_count`, `unique_buyer_count` und `inbound_message_count` getrennt. `reaction_metric` ist standardmäßig nicht gesetzt und darf nur explizit auf eine dieser drei Rohmetriken gebunden werden. Keine davon ist technisch oder fachlich als Default bevorzugt.

Zusätzlich bleiben E-Mail-basierte Projektionen als eigene, source-explizite Metriken getrennt und werden nicht mit den ReactionSnapshot-Werten vermischt. Der normale Produktstart kann diese Evidenz aus ausdrücklich angegebenen lokalen `.eml`-Dateien importieren. Eine so lokal belegte Email-only-Anzeigen-ID darf explizite Klassifikationslabels tragen und an E-Mail-Metrik-Gruppenrankings teilnehmen; dadurch wird sie weder zu einer Owner-/Bestandsidentität noch erhält sie Views-, `ReactionSnapshot`- oder Write-Semantik.

### 2.6 Vergleichsdimensionen

Top-Listen beziehungsweise Vergleiche sind insbesondere nach folgenden expliziten Merkmalen möglich:

- Bild-Typ,
- Stadt,
- Text-Typ,
- Titel-Typ.

### 2.7 Empfehlungen

Aus den Daten sollen Vorschläge für die besten beziehungsweise erfolgversprechendsten Lösungen abgeleitet werden.

Dafür ist eine explizite `objective_metric` erforderlich. Der Contract setzt keine Zielmetrik als Default; ohne konfigurierte Zielgröße bleibt die Optimierungssemantik neutral und fail-closed. Rohmetriken und explizit angeforderte deskriptive Rankings bleiben davon getrennt und begründen weder Kausalität noch automatisch eine Qualitätsaussage.

## 3. Funktionale Anforderungen

- **FR-01:** Anzeigen über Oberfläche und Write API erstellen und nach expliziter Benutzeraktion veröffentlichen; alle Create-/Owner-/Transport-Sicherheitsprüfungen bleiben verpflichtend.
- **FR-02:** Anzeigen löschen; Delete bleibt zusätzlich ID-gebunden freigabepflichtig.
- **FR-03:** Anzeigen gemäß Abschnitt 2.1 verwalten.
- **FR-04:** Titel anhand eines Prompts generieren und in einen Anzeigenentwurf übernehmen.
- **FR-05:** Beschreibungstexte anhand eines Prompts generieren und in einen Anzeigenentwurf übernehmen.
- **FR-06:** Bilder anhand eines Prompts generieren und sicher privat speichern.
- **FR-07:** Generierte Bilder in die Anzeigen-/Media-Vorbereitung integrieren, ohne sie ohne Benutzerbestätigung zu veröffentlichen.
- **FR-08:** Aufrufzahlen erfassen, soweit der gewählte Datenpfad diese bereitstellt.
- **FR-09:** `conversation_count`, `unique_buyer_count` und `inbound_message_count` getrennt erfassen; die fachliche Bedeutung von „wie viele geschrieben haben“ nur über eine explizite `reaction_metric` festlegen.
- **FR-10:** Kennzahlen und Grafiken im Dashboard visualisieren.
- **FR-11:** Top-Listen nach Bild-Typ, Stadt, Text-Typ und Titel-Typ nur gegen eine explizit ausgewählte Rohmetrik erzeugen.
- **FR-12:** Optimierungsvorschläge nur gegen eine explizit konfigurierte `objective_metric` ableiten; ohne Zielmetrik keine „beste Lösung“ behaupten.

## 4. Verbleibende fachliche Detailentscheidungen

Issue #1 enthält nur noch zwei fachliche Restentscheidungen:

1. Welche der getrennten Rohmetriken `conversation_count`, `unique_buyer_count` oder `inbound_message_count` soll fachlich „wie viele geschrieben haben“ bedeuten?
2. Welche explizite `objective_metric` oder später ausdrücklich definierte Zielfunktion bestimmt objektiv, wann eine Variante als „beste Lösung“ gilt?

Diese beiden Entscheidungen bleiben bewusst menschliche Produktentscheidungen. Das Repository setzt dafür keinen Default.

Frühere offene Punkte zu Verwaltungsscope, Integrationsarchitektur, technischen Freigaberegeln, Runtime-Komposition und Fehler-/Retry-Semantik sind inzwischen durch die implementierten Contracts und die Entscheidungen in `docs/DECISIONS.md` konkretisiert oder als separate Betriebs-/Freigabegates abgegrenzt. Sie sind keine offenen Detail-Akzeptanzpunkte von Issue #1 mehr.

## 5. Technische Gates und Betriebsgrenzen

**Stand auf Main `8ea011d`:** Die folgenden Launcher-/Write-Gate-Verträge (insbesondere Punkte 1, 7 und 9) beschreiben den implementierten technischen Aufbau, **nicht** einen derzeit erreichbaren Product-Launcher. `_require_private_web_authorization()` blockiert den normalen Start und den programmatischen Builder vor deren Ausführung. Der bestehende Offline-Modus bleibt separat nutzbar. Eine spätere Freischaltung setzt frische Tests und technische Abnahme voraus.

Der aktuelle Core und die Runtime-Surfaces erzwingen insbesondere:

1. Die generischen Core-/Runtime-Surfaces bleiben fail-safe default-off. Der normale Produktstart `mark-api-launch` öffnet die bestehenden Capability-, API-, Core- und Media-Gates dagegen ausdrücklich und automatisch für seine lokale, loopback-only Write-Komposition; es gibt keinen zusätzlichen Write-Opt-in im Produktpfad.
2. ID-gebundene Writes verwenden einen frischen Owner-Pre-Read, genau einen Mutationsversuch und einen frischen target-bound Post-Readback; Create/Delete folgen zusätzlich dem in D-019 festgelegten Confirmation-Vertrag ohne künstlich duplizierte Management-Runtime.
3. Ein unklarer oder `AMBIGUOUS` Ausgang autorisiert keinen Blind-Retry.
4. Bei Delete ist die authentifizierte, exakt pfad-ID-gebundene Nutzeroperation selbst die Freigabe; die stabile Idempotency-ID liefert intern die Audit-/Authorization-Referenz.
5. Create/Publish und Media-Create besitzen eigene, strengere Reconciliation- und Persistenzevidenz.
6. Login, MFA, CAPTCHA und sonstige Sicherheitschallenges werden nicht automatisiert oder umgangen.
7. Die schreibfähige HTTP-Surface bleibt als separate loopback-only Write API erhalten. Der Product Launcher bindet die sichtbare Dashboard Write UX ausschließlich über einen Same-Origin-Proxy daran; der Browser erhält nicht den Backend-Bearer, sondern einen getrennten per-process Dashboard-Token. Beide Tokens werden nicht in SQLite persistiert. Standalone `mark-api-dashboard` bleibt GET-only/read-only.
8. Die Analytics-Entscheidungen `reaction_metric` und `objective_metric` bleiben unabhängig von den Write-Gates und standardmäßig ungesetzt.
9. `mark-api-launch` verwendet eine bereits authentifizierte loopback-CDP-Sitzung, führt genau einen fail-closed Initial-Inventory-Sync aus und kann danach ausdrücklich angegebene lokale `.eml`-Reaction-Evidenz batch-atomar importieren. Erst anschließend startet er die default-on Write API und danach das daran gebundene Dashboard. Die Dashboard Write UX delegiert ausschließlich an diese Write API, führt keine automatischen Retries aus und eröffnet weder CORS noch einen privaten/mobile Messaging-Readpfad. Der Launcher startet keinen Browser und automatisiert keinen Login. Automatisierte Tests dieses Slices führen keine Kleinanzeigen-Plattformwrites aus.
10. E-Mail-Evidenz bleibt source-explizit: sie erzeugt keine Owner-Identität, keinen `AdSnapshot` und keinen `unique_buyer_count`; `reaction_metric` bleibt default-off. Lokale Klassifikation darf an eine durch Owner-Snapshot **oder** importiertes E-Mail-Event belegte ID gebunden werden. Email-only-IDs nehmen nur an Gruppenrankings von Metriken teil, deren eigene Ranking-Population sie enthält; insbesondere nicht an Views-/Owner-/`ReactionSnapshot`-Gruppen.
11. Periodische Synchronisation sowie die weitergehende Freshness-/Recovery-UX bleiben spätere Produktslices. Ein Media-Submit-`UNKNOWN` autorisiert weiterhin keinen Write-Retry; beim Launcher-Shutdown ist höchstens die bestehende observation-only Media-Reconciliation vor erneutem Cleanup zulässig.
12. Die Write API serialisiert ihre Prozessautorität zusätzlich über einen exklusiven OS-Lock auf der SQLite-Datei. Persistente Idempotency-Claims sind runtime-owner-gebunden und besitzen einen separaten Execution-Start-Barrier. Nur ein gleichartiger Claim eines beendeten Runtimes ohne gesetzten Barrier darf übernommen werden; gesetzter Barrier und Legacy-Claims ohne beweisbaren Owner bleiben über Neustarts fail-closed.

Die maßgeblichen Architektur-, Sicherheits- und Lieferentscheidungen sind in D-006 bis D-029 dokumentiert. Ein implementierter technischer Pfad ist keine automatische Freigabe für reale Plattformwrites außerhalb einer vom Nutzer ausgelösten Produktoperation.

## 6. Vorhandene MVP-Bausteine (noch keine vollständige Produktabnahme)

Die folgende Liste bezeichnet implementierte Verträge/Komponenten und historisch getestete Einzelpfade. Insbesondere die Launcher-/Default-on-Elemente 10 bis 12, 14 und 16 sind auf Main `8ea011d` wegen des vorgeschalteten pauschalen Startup-Abbruchs **nicht als Endnutzerpfad ausführbar**. Aus ihrer Existenz folgt daher weder vollständige installierte Nutzbarkeit noch eine neue Plattformberechtigung:

1. Synchronisation und read-only Besitzer-/Bestandsdaten,
2. ID-gebundenes Content-Update,
3. Pause/Reservierung und Aktivierung,
4. Delete mit intern gebundener Nutzerfreigabe und D-019-Confirmation,
5. separat gegatetes Create/Publish einschließlich optionalem Media-Pfad,
6. Views, Merker, Replies und getrennte Reaktionsmetriken,
7. Inbox-/Conversation-Zuordnung und source-explizite E-Mail-Projektionen,
8. append-only SQLite-Snapshots und crash-idempotente lokale Write-Receipts,
9. Analytics-Rohmetriken und Vergleiche nach Bild-Typ, Stadt, Text-Typ und Titel-Typ,
10. read-only Daten-/Analytics-Dashboard plus im Product Launcher Same-Origin Write UX für die bestehenden Create-/Media-/Content-/State-/Delete-Surfaces, Grafiken, Top-Listen und lokale Runtime-Smokes,
11. installierter `mark-api-launch`-Startpfad mit genau einem bestätigten Startup-Inventory-Sync vor Dashboard und Write API,
12. Default-on Produktkomposition der bestehenden Create-, Media-, Content-, State- und Delete-Surfaces mit prozesslokalem Bearer und unveränderten Idempotency-/Confirmation-/No-Blind-Retry-Grenzen.
13. Interne Auth-/Idempotency-Härtung mit exklusiver SQLite-Write-Runtime, runtime-owner-gebundenen Claims, persistiertem Execution-Start-Barrier und fail-closed Legacy-Migration.
14. Im normalen Launcher optionaler, ausdrücklich dateigebundener Reaction-Import lokaler Kleinanzeigen-E-Mail-Kopien vor den HTTP-Surfaces; idempotent, ohne Mailbox-/Messaging-Netzwerkzugriff und ohne erfundene Owner- oder Buyer-Fakten.
15. Explizite lokale Klassifikation von Email-only-IDs mit source-expliziten E-Mail-Gruppenrankings, ohne `AdSnapshot`, Owner-/Presence-Folgerung oder Ausweitung auf nicht belegte Metriken.
16. Same-Origin Dashboard Write UX mit getrenntem UI-Token, serverseitiger Bearer-Injektion, strikter Proxy-Allowlist und exakt gebundener UI-Idempotenz ohne automatische Wiederholung bei unbekanntem Transportausgang.

Die frühere MVP-Depriorisierung der Text- und Bildgenerierung schränkt den verbindlichen vollständigen Lieferumfang **nicht** ein (D-028/D-029).

Reale sichtbare Testanzeigen, Testnachrichten oder zyklische Plattformwrites werden nicht als normale Regressionstests verwendet.

## 7. Nächste Produktphase

Die bislang implementierte Launcher-Komposition soll den kohärenten Startpfad aus Initial-Sync, read-only Daten-/Analytics-Dashboard, Same-Origin Write UX und default-on Write-Komposition schließen; auf Main `8ea011d` verhindert das pauschale Admission-Gate ihre Ausführung. Der Endnutzerpfad muss nach der gezielten Korrektur erneut installiert und Ende-zu-Ende getestet werden. Die bestehenden Authentizitäts-, Ownership-, Confirmation-, TOCTOU-, Readback- und No-Blind-Retry-Sicherheiten bleiben erhalten; D-024 härtet zusätzlich die persistente Idempotenz über Prozessabbruch und Neustart.

Die interne Auth-/Idempotency-Härtung, der explizite lokale Reaction-Data-Startup-Pfad, Email-only Classification und Dashboard Write UX sind implementierte Bausteine. Ihre Integration in einen tatsächlich startbaren und vollständig geprüften Produktpfad sowie wiederholbarer Sync/Freshness, Recovery UX, SQLite Lifecycle, Shutdown, Packaging, Full E2E und der finale Produktaudit bleiben konkrete Lieferarbeiten.

Die beiden fachlichen Analytics-Entscheidungen bleiben weiterhin offen: `reaction_metric` für „wie viele geschrieben haben“ und `objective_metric` beziehungsweise eine ausdrücklich definierte Zielfunktion für „beste Lösung“. Ohne diese Festlegungen darf keine objective-gebundene Empfehlung als fachlich gewollt behauptet werden.