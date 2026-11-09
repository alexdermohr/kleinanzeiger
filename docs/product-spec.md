# Produktspezifikation

Stand: 09.11.2026 (offizielles Release v1.0.12; historische Prüfstände sind gekennzeichnet)

Status: **Offizielles v1.0.12 veröffentlicht und technisch einschließlich nativer Windows-11-Installation, synthetischer Online-Verkäuferkette, KI-Kosten-/Fehlerwegen, Backup/Restore und 10.000er-Inventar geprüft; reale Kundenend-Abnahme ausstehend.** Die Online-Komposition für den bestätigten eigenen Bestand, die getrennte schreibfähige Write API und die Dashboard-Bedienung ist implementiert. Der historische pauschale Starter-Blocker auf `8ea011d` ist kein Befund zur aktuellen Auslieferung. Ein erfolgreiches synthetisches Szenario ist kein Beweis, dass Marks eigenes Konto bereits produktiv abgenommen wurde.

**Aktueller v1.0.12-Nachweis vom 09.11.2026:** [Offizielles Windows-Release](https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.0.12) aus Main-Commit `545703d31ab0205a470edaaadd491404de6e0a7b`; Windows-ZIP SHA-256 `f0fe485ea65e429c84c85c61a8f73bd0da11cbe93e2746d0bc95bc10e8c9cb38`. Main-CI, Offline-Wheel-Prüfungen und native Windows-Python-3.12/3.13-Installation samt Rollback bestanden. Das unveränderte offizielle Paket bestand in einer isolierten Windows-11-VM das Update **1.0.11 → 1.0.12**, den echten Product Launcher, die **synthetische** Online-Verkäufer-/Medienkette und den 5.000-Ziffern-HTTP-Negativtest. Eine zweite isolierte Windows-11-VM mit dem installierten offiziellen Wheel prüfte Windows-Backup/Restore, KI-Generierung samt Budgets und 10.000 synthetische Anzeigen einschließlich Persistenz und Drift-Gegenproben erfolgreich. Beide VM-Läufe endeten mit QEMU Exit 0. Es erfolgten keine echten Plattformwrites, bezahlten Provideraufrufe oder belegten physischen GUI-Mausklicks. [Releaseabnahme](https://github.com/alexdermohr/kleinanzeiger/issues/240#issuecomment-6749484539) und [zusätzliche Windows-11-Gegenprobe](https://github.com/alexdermohr/kleinanzeiger/issues/240#issuecomment-6749851754).

**Historischer v1.0.11-Nachweis vom 09.11.2026:** [Offizielles Windows-Release](https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.0.11) aus Main-Commit `4291d0c6e4f16898430fe4296b03ac6532a5ad6c`; Windows-ZIP SHA-256 `6afa9d932daa0d602838f7dcfad204c5c1a02affc77c27c425fff31ade506573`. Main-CI, installierte Offline-Wheel-Checks und native Windows-Python-3.12/3.13-Tests waren erfolgreich. Eine isolierte Windows-11-VM installierte aus diesem offiziellen ZIP das Update **1.0.10 → 1.0.11**, prüfte das darin enthaltene Wheel, startete den tatsächlichen Product Launcher und absolvierte die **synthetische** Online-Verkäufer-/Medienkette erfolgreich. Diese VM-Prüfung umfasste keine belegten physischen GUI-Mausklicks und keine echten Plattformwrites. [Abnahmebelege und Abgrenzungen](https://github.com/alexdermohr/kleinanzeiger/issues/240#issuecomment-6742451315).

**Historischer v1.0.10-Nachweis vom 09.11.2026:** Drei Main-CI-Workflows waren erfolgreich (CI, installiertes Offline-Wheel und native Windows-Installation). Das unveränderte offizielle Windows-ZIP (SHA-256 `10a86367da3cea115e793b58dcefc5e80d6afc619158eafe58fb146c43c5b978`) bestand in einer isolierten Windows-11-VM ein Upgrade 1.0.9 → 1.0.10 mit Bestandserhalt und echtem GUI-Start. Bei absichtlich ausgefallener Chrome-/CDP-Steuerung startete der gesonderte tokengebundene Offline-Arbeitsmodus, erlaubte synthetisch belegte lokale Verkaufsbuchung und verweigerte Plattformaktionen sowie unbestätigte Anzeigen. Beleg: [Windows-11-Abnahme](https://github.com/alexdermohr/kleinanzeiger/issues/240#issuecomment-6733572190). Das unveränderte offizielle Wheel bewältigte separat auf Linux 10.000 synthetische Anzeigen über 3 × 200 HTTP-Seiten (p95 13,937 ms bei 603 Anfragen): [10.000er-Test](https://github.com/alexdermohr/kleinanzeiger/issues/240#issuecomment-6733618469).

**Nicht nachgewiesen:** Einrichtung und Bedienung auf Marks tatsächlichem Windows-PC, Anmeldung und Bestandsabgleich mit seinem Konto, eine positiv bestätigte echte Create-/Edit-/Status-/Delete-Plattformoperation, ein realer Provideraufruf mit Kosten sowie eine vollständige reale Kunden-Endabnahme. Diese Grenzen sind fehlende Nachweise, keine pauschalen Softwaredefekte oder zusätzliche Entwicklungsfreigabepflichten.

Quelle: `docs/requirements-source-2026-09-23.md`

## Verbindlicher Liefer- und Arbeitsmodus (D-028/D-029/D-030)

**Kundenentscheidung vom 30.10.2026:** [Verantwortung des Kunden für den realen Plattformbetrieb](kundenauftrag-plattformbetrieb.md). Die allgemeine externe Plattformfreigabe ist keine vorgelagerte Voraussetzung für Entwicklung, technische Tests oder Paketveröffentlichung. Die tatsächlichen Plattformbedingungen und Kontorechte sind dem späteren Einsatz des Kunden als Betreiber zugeordnet. Kontobezogene technische Zugangsvoraussetzungen und Schutzmechanismen werden dadurch nicht außer Kraft gesetzt.

**Produktidentität:** **Kleinanzeiger**, kanonisches Repository `alexdermohr/kleinanzeiger`, Distributionsname `kleinanzeiger`, Bedienstart `kleinanzeiger`.

**Aktuelle CLI-/Modulidentität:** Windows und neue Installationen starten über `kleinanzeiger` oder `python -m kleinanzeiger launch`, offline über `python -m kleinanzeiger offline`. Sichtbare Startmeldungen und Fehler benennen ausschließlich Kleinanzeiger. Das ursprüngliche Importpaket `mark_api` und ältere CLI-Aliase bleiben als kompatible Implementierungsgrenze bestehen; persistente Protokollidentitäten und SQLite-Schemata dürfen nicht aus optischen Gründen geändert werden. Die internen Python-Module `mark_api` und bisherige CLI-Aliase werden zur Kompatibilität erhalten.

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

Die im aktuellen Release implementierte Online-Komposition von `mark-api-launch` verwendet für den kohärenten Produktstart eine bereits laufende, bereits authentifizierte lokale Chrome-/Chromium-Sitzung über loopback-CDP. Der damals auf Main `8ea011d` beobachtete pauschale Admission-Blocker ist historische Evidenz und kein aktueller Nachweis eines gesperrten Online-Produktstarts. Er führt genau einen frischen Owner-Inventory-Read aus und persistiert bestätigte aktuelle Anzeigen sowie `ABSENT`-Transitions für historisch bekannte, nun fehlende Anzeigen. Optional importiert er danach einen vom Nutzer ausdrücklich über `--email FILE` angegebenen lokalen Batch von Kleinanzeigen-Nachrichtenkopien mit der bestehenden idempotenten E-Mail-Importlogik in denselben Store. Erst nach erfolgreichem Initial-Read und einem gegebenenfalls angeforderten erfolgreichen Reaction-Import wird zuerst die bestehende loopback-only Mark Write API gestartet und anschließend das Dashboard an deren tatsächliche Loopback-Adresse gebunden. Der Launcher erzeugt dafür pro Prozess einen neuen lokalen Write-API-Bearer sowie einen getrennten Dashboard-Write-Token. Letzterer wird ausschließlich als URL-Fragment der lokalen Dashboard-URL übergeben, vom Browser in `sessionStorage` übernommen und unmittelbar aus der sichtbaren URL entfernt; der Backend-Bearer wird nicht an Browser-JavaScript exponiert.

Die Product-Launcher Write UX unterstützt die bereits implementierten Create-, Media-, Content-, State- und Delete-Surfaces. Ihre Same-Origin-Proxy-Schicht ist strikt route-/header-/body-allowlistet, verlangt den per-process Dashboard-Token, einen passenden `Origin` und einen CSRF-Marker und öffnet kein CORS. Sie implementiert keine eigene Plattform-Write-Semantik: Capability, persistente Write-API-Idempotenz, Ownership, Confirmation, TOCTOU, Post-Readback, Media-Persistenzevidenz und `platform_retry_authorized=false` bleiben ausschließlich beim vorhandenen Write-API-/Core-Pfad. Zusätzlich persistiert der Dashboard-Proxy vor jeder Plattformweiterleitung den normalisierten UI-Request und dessen Idempotency-Key als lokalen Pending-Fence in derselben SQLite-Datei. Bei unbekanntem Transportausgang wird nicht automatisch erneut gesendet; ein zweiter Tab oder neuer Key für dieselbe Ressource wird blockiert, und eine manuelle Wiederholung verwendet exakt denselben gespeicherten Request/Key. Nach Tab- oder Dashboard-Neustart kann die UI die Pending-Records mit dem aktuellen per-process UI-Token wieder lesen; Persistenzfehler bleiben fail-closed. Ein Pending-Fence wird erst durch einen separaten lokalen Browser-ACK für exakt Scope und Idempotency-Key geräumt, nachdem die UI einen gebundenen terminalen Backend-Response oder sicheren Clientfehler tatsächlich verarbeitet hat; bei verlorenem Response oder ACK bleibt der ursprüngliche Request gebunden. Der lokale Browser-ACK hat zwei lokale Phasen: erst gespeichert/bestätigt, dann finalisiert. Seit v1.0.11 hebt die UI den Fence erst bei `finalized=true` auf; sie darf die zweite lokale ACK-Phase mit demselben Key nachholen, wiederholt aber niemals deshalb einen unklaren externen Plattform-Write. Partielles lokales Media-Staging verwirft bereits bekannte Refs über eine capability-gebundene idempotente Discard-Primitive; sie ist keine Plattformoperation. Verlorene Antworten nach bereits erfolgreichem lokalem Stage können keinen dauerhaften Handle-Leak erzeugen: ungenutzte Handles laufen nach 15 Minuten ab und werden vor dem nächsten Stage/Resolve lazy aus dem lokalen Kontingent entfernt. Ein zusätzlicher `--enable-writes`-Schalter existiert weiterhin nicht. Ein fehlgeschlagener oder unklarer Initial-Read verhindert im **Online-Launcher** weiterhin den Start der schreibfähigen HTTP-Surfaces. Beim Windows-Start mit nicht verfügbarer Chrome-Steuerung ist jedoch ein **getrennter, tokengebundener lokaler Arbeitsmodus** verfügbar: bereits gespeicherte eigene Anzeigen lesen, lokale Entwürfe, Verkaufsvermerke und KI-Aufträge bearbeiten, aber keine Plattformwrites. Fehlerhafte ausdrücklich angeforderte Mail-Batches werden konservativ abgelehnt. Manuelle und periodische Bestandsaktualisierung sind inzwischen vorhanden; aus fehlgeschlagenen Abgleichen folgt keine Eigentümer- oder Schreibfreigabe. Ein vollständig abgenommener echter Plattform-Nachrichtenweg ist davon zu unterscheiden.

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

**Stand des offiziellen v1.0.11:** Die vorhandene Online-Komposition koppelt den bestätigten Eigenbestandsabgleich an die getrennte Write API und den Dashboard-Same-Origin-Proxy. Die historische Sperre durch `_require_private_web_authorization()` auf Main `8ea011d` beschreibt nicht den aktuellen Produktstand. Die reguläre Online-Komposition und der gesonderte lokale Offline-Arbeitsmodus sind implementierte Startwege; der Offline-Modus schaltet Plattformzugriffe bewusst nicht frei. Die folgenden Vertragsgrenzen gelten für die konkreten technischen Aktionen, **nicht** als allgemeine externe Freigabevoraussetzung für Entwicklung, Tests oder Release. Ein positiver synthetischer Test darf nicht als echte Kontoabnahme ausgegeben werden.

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
11. Manuelle und periodische Bestandsaktualisierung sind im Online-Produktpfad vorhanden. Unklare oder fehlgeschlagene Abgleiche erlauben keinen blinden Write-Retry und keinen ungeprüften fortgesetzten Plattformabgleich; kontrollierte Wiederverbindung und ein frischer bestätigter Eigenbestand sind erforderlich. Die separate Windows-Offline-Variante bleibt auch ohne Browser lokal nutzbar.
12. Die Write API serialisiert ihre Prozessautorität zusätzlich über einen exklusiven OS-Lock auf der SQLite-Datei. Persistente Idempotency-Claims sind runtime-owner-gebunden und besitzen einen separaten Execution-Start-Barrier. Nur ein gleichartiger Claim eines beendeten Runtimes ohne gesetzten Barrier darf übernommen werden; gesetzter Barrier und Legacy-Claims ohne beweisbaren Owner bleiben über Neustarts fail-closed.

Die maßgeblichen Architektur-, Sicherheits- und Lieferentscheidungen sind in D-006 bis D-029 dokumentiert. Ein implementierter technischer Pfad ist keine automatische Freigabe für reale Plattformwrites außerhalb einer vom Nutzer ausgelösten Produktoperation.

## 6. Vorhandene MVP-Bausteine (noch keine vollständige Produktabnahme)

Die folgende Liste benennt implementierte Komponenten und Bedienwege der onlinefähigen Produktkomposition. Die alte Sperrbeobachtung auf `8ea011d` ist für v1.0.11 überholt. Einzeltests, erfolgreiche native Windows-Offline-Prüfungen und eine veröffentlichte Distribution ersetzen aber keine vollständige Abnahme am tatsächlichen Kundenkonto:

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

## 7. Verbleibende Abnahmen und nächste Schritte (09.11.2026)

1. **Installierte synthetische Online-Verkäuferkette für v1.0.11 nachgewiesen.** Das unveränderte offizielle Windows-ZIP bestand in einer isolierten Windows-11-VM das Update v1.0.10 → v1.0.11, die Integritätsprüfung des installierten Wheels, den realen Product Launcher und synthetische Online-Verkäufer-/Media-Abläufe. Browserseitige ACK-Gegenproben sind außerdem im nativen Windows-Regressionstest enthalten. Das sind **synthetische** Plattformaktionen und kein echter Kontotest; dieser Punkt ist kein offener technischer Blocker mehr. Weiterführende UI-/Wiederanlaufgegenproben folgen risikogerecht in Punkt 2–4.
2. **KI-Text, Bild und Bestandsoptimierung als echte Bedienfolge prüfen.** Eingabe → ausdrückliche Kostenfreigabe → Vorschlag → übernehmbare Bearbeitung → Vorschau/Diff → eindeutige Freigabe → synthetisch bestätigter Write-/Recovery-Ausgang. Provider-Fehler und Kosten-UNKNOWN gesondert behandeln. G4/G5, KI-Arbeitsradar und vorhandenen budgetierten Autopiloten nicht redundant neu entwickeln.
3. **Betrieb auf Windows und großer Bestand.** Die bereits positiv geprüfte Offline-Fallback-Kette, Installer, Backup/Restore und 10.000er-HTTP-Pagination um vollständige Online-/Unterbrechungs-/Wiederanlaufgegenproben, große Bestandslisten in der realen UI und bei Bedarf kontrollierte Wiederherstellung ergänzen.
4. **Echte Kundenabnahme:** Erst anhand der tatsächlich verfügbaren Zugangsmöglichkeiten Marks Windows-PC, Chrome-Anmeldung, Kontoabgleich, gebundene Einzelaktionen, KI-Provider und Datenwiederherstellung prüfen. Reale externe Zugriffs- und Nutzungsrechte liegen nach [D-028/D-029](kundenauftrag-plattformbetrieb.md) beim Kunden als Betreiber; eine allgemeine externe Genehmigung ist **kein technischer Entwicklungsstopp**. Ohne Zugang oder konkrete Berechtigung keine Live-Aktionen behaupten.

**Priorität:** Nach der bestandenen synthetischen Verkäuferkette nun KI-/Kosten-, Großbestands- sowie Backup-/Restore-Gegenproben und die reale Windows-Kundenabnahme ergänzen. Der Ausbau des seit v1.0.9 enthaltenen Schnäppchenjägers ist auf ausdrücklichen Kundenwunsch vom 08.11.2026 zurückgestellt. [Gesamtauftrag und Prüfnachweise #240](https://github.com/alexdermohr/kleinanzeiger/issues/240).

Die beiden fachlichen Analytics-Entscheidungen bleiben offen: `reaction_metric` für „wie viele geschrieben haben“ sowie `objective_metric` beziehungsweise eine ausdrücklich definierte Zielfunktion für „beste Lösung“. Ohne diese Festlegungen darf keine objektiv beste Variante behauptet werden.
