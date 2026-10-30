# Kleinanzeiger – Kundenauftrag und Verantwortlichkeit beim Plattformbetrieb

**Entscheidungsstand:** 30. Oktober 2026
**Geltungsbereich:** Produktentwicklung, technische Prüfung und späterer Betrieb durch den Kunden
**Bezug:** `docs/DECISIONS.md`, insbesondere D-028 und D-029, sowie `docs/product-spec.md`

## 1. Verbindlicher Entwicklungsauftrag

Kleinanzeiger wird nach den fachlichen Anforderungen des Kunden als vollständig bedienbares Produkt entwickelt. Dazu gehören die Verwaltung und Synchronisation eigener Anzeigen, Erstellen, Bearbeiten, Medienverwaltung, Veröffentlichung, Statuswechsel und Löschen sowie KI-Verarbeitung, Stapelabläufe, Kommunikation, Auswertungen, Sicherung und Wiederherstellung.

Eine **allgemeine externe Plattformfreigabe ist keine vorgelagerte Anforderung für die Implementierung, die synthetische technische Prüfung oder die Softwareveröffentlichung**. Funktionen werden nicht allein deshalb gestrichen, dauerhaft deaktiviert oder als optional verschoben, weil eine pauschale Genehmigung durch den Plattformbetreiber nicht vorgelegt wurde. Der tatsächliche Entwicklungsfortschritt wird anhand funktionsfähiger Bedienwege und reproduzierbarer Tests bewertet.

## 2. Verantwortung des Kunden als Betreiber

Der Kunde entscheidet über den späteren Einsatz mit seinen Konten und trägt die Verantwortung, die für den konkreten Einsatz relevanten Plattformbedingungen, Zustimmungen, Kontoberechtigungen und gegebenenfalls offiziellen Schnittstellenberechtigungen zu klären und einzuhalten. Die Beschaffung einer allgemeinen Betreiberfreigabe ist **nicht Gegenstand des Softwareentwicklungsauftrags**.

Diese Aufgabentrennung ist keine Zusicherung, dass eine bestimmte externe Nutzung erlaubt ist. Sie ersetzt keine tatsächlich erforderlichen Zugangsdaten, Kontoberechtigungen oder gesetzlichen Pflichten. Der Entwicklungsstand und die reale Betriebsberechtigung werden daher nicht miteinander verwechselt.

## 3. Technische Umsetzung ohne pauschalen Freigabe-Blocker

- Alle beauftragten Funktionen dürfen unabhängig von einer allgemeinen externen Genehmigungsfrage technisch implementiert werden.
- Tests nutzen kontrollierte, zustandsbehaftete Gegenstellen und prüfen erfolgreiche, abgelehnte, unterbrochene sowie unklare Abläufe ohne unbeabsichtigte echte Plattformaktionen.
- Nur tatsächlich vorhandene, für den konkreten Transport benötigte Anmeldung, Zugriffsdaten und technische Autorisierung werden bei echter Nutzung vorausgesetzt. Weder eine fremde Identität noch ein nicht erteiltes Zugriffsrecht wird simuliert oder behauptet.
- Authentifizierung, Mehrfaktoranmeldung, Konto- und Anzeigenzuordnung, Datenintegrität, Idempotenz, Benutzerbestätigungen, Kostenfreigaben und Recovery-Schutz bleiben wirksam. Schutz vor ungewollten Kosten, Datenverlust und Doppelaktionen ist keine pauschale Plattformfreigabesperre.
- Ein ungeklärter externer Betriebsstatus allein gilt nicht als Nachweis eines Softwarefehlers; tatsächlich reproduzierte Funktionsmängel bleiben unabhängig davon zu beheben.

## 4. Dokumentation und Abnahme

Projektberichte unterscheiden klar: **implementiert**, **synthetisch geprüft**, **in einen Release integriert** und **auf dem realen Kunden-PC sowie dem tatsächlichen Konto abgenommen**. Eine externe Genehmigung wird nicht als Voraussetzung für die ersten drei Stufen dargestellt. Die vierte Stufe kann nur durch tatsächlich durchgeführte Abnahme belegt werden.

Historische Entscheidungen und technische Befunde werden nicht nachträglich gelöscht oder umgedeutet. Soweit ältere Arbeitsaufträge eine *allgemeine* Plattformgenehmigung als pauschalen Entwicklungsstopp formulieren, gilt die vorstehende Aufgabenverteilung zusammen mit D-028/D-029. Konkrete ungeklärte Zugangs-, Sicherheitsschutz- und Betriebsfragen bleiben getrennt nachvollziehbar.

**Leitlinie:** Vollständige Produktentwicklung wie vom Kunden beauftragt; eigenständige Verantwortung des Kunden für die Bedingungen seines tatsächlichen Plattformbetriebs; keine erfundenen Zugriffsrechte und keine Abschaltung notwendiger technischer Schutzmechanismen.
