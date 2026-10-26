# Kleinanzeiger – verbindlicher Ausbauplan zum KI-Portfolio-Manager

Stand: 26. Oktober 2026. Ausgangsversion: **Windows v0.1.5**, veröffentlichter
Commit `b7ee2728bd7c1c7be9c501af65c733a677573e23`.

## 1. Produktziel und Leitplanken

**Marks Ziel:** Hunderte bis Tausende eigener Anzeigen, perspektivisch bis
10.000 Datensätze, über eine **einfach bedienbare Windows-Anwendung** mit KI
verwalten, auswerten und optimieren. Der Anwender erteilt Aufträge für
Anzeigenbestände statt hunderte Einzelmasken manuell zu bedienen. Alle
Funktionen aus dem bisherigen Kundenauftrag, ausdrücklich einschließlich
Create/Publish, Medien, Content-Update, Pause/Activate und Delete, bleiben
vollständiger Lieferumfang.

**Grundsätze:** Eine Kundenoberfläche, klarer Anzeigenkatalog, ein Auftrag
mit sichtbarem Fortschritt, prüfbare Ergebnisse und verständliche Fehler.
SQLite und die vorhandene Write API werden wiederverwendet. Keine neuen
Frameworks, Prozesse, Security-Rollen oder Freigabebürokratie ohne konkret
belegten Nutzen. Keine doppelte Plattformsemantik, keine heimlich angenommene
Berechtigung. Eine fehlende allgemeine externe Plattformfreigabe ist **kein
Entwicklungs- oder synthetisches Abnahme-Gate**. Reale Plattformaktionen
erfordern eine tatsächliche authentifizierte Eigentümersitzung und
nutzerseitig definierte Handlungsregeln.

**Sicherheits- und Qualitätsbedingungen:** Kein KI-generiertes Erfinden von
Produktfakten, kein automatisches Ersetzen echter Produktfotos durch
irreführende Bilder, kein Blind-Retry unklarer Plattformwirkungen. Die
bestehenden Owner-/CSRF-/Idempotenz-/Recovery-Grenzen bleiben erhalten.
Keine kostenpflichtige KI-Aktion ohne vorherigen vom Anwender definierten
Budget- und Zustimmungskontrakt. Plattform- und KI-Tests verwenden
zustandsbehaftete synthetische Gegenstellen. Der Rechner `fotolabor`
bleibt bis zu einer gesonderten Anweisung unverändert.

## 2. Verifizierter Startzustand und Lücken

In v0.1.5 nutzbar: Windows-Einfenster-Start, Login im separaten Chrome-Profil,
eigene Anzeigen lesen und abgleichen, Einzel-Create mit Medien, Content-,
Pause-/Activate-/Delete-Write, KI-Texte und KI-Bildentwürfe, lokale EML-Importe,
Vergleichslabels, Analytics/CSV, Backups/Restore, Updates und Kundenhandbuch.

Nicht als fertige Massenfunktion ausgeben: durchsuchbarer und serverseitig
seiteneingeteilter Katalog, Mehrfachauswahl, Tabellen-/Foto-Massenimport,
persistente KI- und Plattform-Auftragswarteschlange, gruppenweise Vorschau,
Massenfreigabe, eigener KI-Posteingang, Verkaufs-/Bestandserfassung, automatische
Preisgestaltung, Gewinn-/Erfolgssteuerung und 10.000er-Lastnachweis.

Bekannte technische Engpässe:
- Das alte `/api/ads` und der Dashboard-Start bauen die gesamte
  Anzeigenliste auf und rendern alle Karten. Die neue Katalogansicht muss
  für die normale Bedienung **limitierte, stabile API-Seiten** nutzen.
- Der aktive Management-/CDP-Bestandsleser begrenzt einen Abgleich auf
  **100 Seiten** (tatsächliche Anzeigenzahl abhängig von Seitengröße).
- Der geschützte Storage-Owner ist intern auf **2.048 Snapshots pro
  synchronisierter Generation** begrenzt; Windows-native SQLite-Nutzung
  ist davon getrennt. Eine spätere Skalierung des geschützten Pfads muss
  nachgewiesen, nicht behauptet werden.
- Die Analytik misst Ansichten/Reaktionen, nicht schon bestätigte Verkäufe,
  Umsatz, Deckungsbeiträge oder echte Kausalität.

## 3. Umsetzung in verbindlicher Reihenfolge

| Phase | Lieferinhalt | Akzeptanz |
| --- | --- | --- |
| **P0 – Katalog & Bedienbarkeit** | Serverseitige Seiten, Suche nach ID/Titel, Statusfilter, stabile Reihenfolge, Mehrfachauswahl für spätere Aktionen, sichtbare Auswahlanzahl. Vorhandene Einzel-Writes unverändert. | 100/1.000/5.000/10.000 synthetische Anzeigen, höchstens eine Seite pro UI-Datenabruf, kein Vollkatalog-JSON beim normalen Seitenwechsel, keine Plattformaktion durch Auswahl. Getestete HTTP-Filter-/Cursor-Fehlerpfade. |
| **P1 – Datenaufnahme** | CSV-/Excel-Import mit nachvollziehbarem Mapping, Herkunft, Pflichtfeldern, Dublettenprüfung und trockenem Probelauf; optional Fotos aus Ordnern verbinden. Anzeigen zunächst **Entwürfe**, niemals unbestätigte Publikation. | 1.000 synthetische Artikel atomar/fortsetzbar übernehmen; ungültige Zeilen zeigen Fehler, keine Teildatenverluste, Export/Roundtrip. |
| **P2 – Stapelaktionen/Agentenaufträge** | Persistente Jobliste mit ID, Fortschritt, Pause/Fortsetzung, kontrollierten Einzel-Writes, Original-/Neudaten, Änderungen vor Veröffentlichung als Sammelvorschau; KI-Titel-/Beschreibung-/Fotoanalyse über eigene Budgetkontrolle. | Unterbrechung/Neustart/Doppelklick: kein zweiter Plattform-Submit bei UNKNOWN; Audit der bestätigten Änderungen; Kostenquote/Limit pro Auftrag. |
| **P3 – Interessenten & Verkauf** | Account-bezogene Leseschnittstellen für erlaubte Konversationen, Priorisierung, sichere KI-Antwortentwürfe, Reserviert/Verkauft-Status, Lagerort, Verkaufserlös. | Keine erfundenen Nachrichten oder Kundendaten. Ausgehende Nachrichten nur nach expliziter Freigabe/autorisierten Regeln; Korrespondenz zur Anzeigen-ID geprüft. |
| **P4 – Optimierung & Controlling** | Auswertungen für qualifizierte Kontakte, Verkäufe, Standzeit, Erlös, Kosten und Gewinn; Preis- und Titelvorschläge, kontrollierte zeitliche Tests, Wochenberichte. | Zielmetrik bewusst konfiguriert; Quellen/Zeiträume sichtbar; fehlende Daten von Null getrennt; keine unbegründeten Kausalaussagen. |
| **P5 – Betriebsreife und Erweiterung** | Profilbasierte Vorlagen, zentrale Benachrichtigungen, kostenbewusster Autopilot, mögliche weitere Märkte/Konten als sauber getrennte Identitäten. | Restore nach definiertem Ausfall, Mehrkonten-Isolation, lange Listen ohne Browser-/SQLite-Speicherwachstum, belastbare Profil- und Budgetgrenzen. |

**Keine künstlichen Versionstermine:** Neue Version erst dann vergeben, wenn die
jeweiligen integrierten Funktionen im installierten Produkt getestet, nach
`main` gemergt und mit prüfbarem Release veröffentlicht wurden. `v0.1.5`
bleibt unveränderlich. Feature-PRs sind inkrementell, kein monolithischer
Neubau. Ein optionaler Meilenstein `v0.1.6` ist P0.

## 4. Startauftrag – P0 (bereits zur Umsetzung freigegeben)

1. Nur lesenden Katalog-Endpunkt mit validiertem `limit`, `after`, `q`
   und `status` hinzufügen. IDs aus bestehendem SQLite-Bestand **indexiert
   und seitenweise** lesen, bestehende `AdView`-Projektion wiederverwenden.
   Keine zweite Write-/Owner-API. Die bestehende `/api/ads`-Route für
   Kompatibilität nicht verändern.
2. Dashboard verwendet für die normale Anzeigenliste maximal 50 Datensätze
   pro Anfrage, mit Suchfeld, Statusfilter, Weiter-/Zurück-Navigation und
   sichtbaren Einträgen. Das bisherige Anzeigen-Auswahlformular, die
   synchronisierten Karten, die Sicherheitsbestätigungen und der
   Recovery-Status bleiben korrekt. Keine Auswahl darf einen Write auslösen.
3. Mehrfachauswahl von Anzeigen-IDs ausschließlich als lokale Arbeitsliste
   mit Vorschau/Zählung; keine neue Bulk-Write-Route. Bei Filtersprung
   keine heimliche Publikation, bei Löschen keine automatisch wiederholte
   Aktion.
4. Synthetischer HTTP-/SQLite-/Node-Test auf funktionalen Seiten, Filter,
   ungültige Requests, DB-Corruption, 1.000–10.000 Anzeigen und optionalen
   StorageOwner-Pfad. Windows-Installations- und vorhandene Write-E2E-Tests
   unverändert durchlaufen lassen.
5. Dokumentation und Windows-Handbuch um die neue Ansicht ergänzen.
   Prüfung auf exakt dem PR-HEAD, regulärer GitHub-Schutz-Merge und
   eigenständiger Release, falls Version geändert. `fotolabor` nicht anfassen.

## 5. Produkt- und Qualitätskennzahlen

Technische Messgrößen pro Stufe: DB-/API-Laufzeit p50/p95,
Anzeigen-Datensätze pro HTTP-Antwort, browserseitige DOM-Knotenanzahl,
Memory Peak, Gesamtzeit eines Bestandsabgleichs, bestätigte vs. UNKNOWN
Writes, wiederaufnehmbare vs. gesperrte Jobs.

Geschäftliche Messgrößen erst nach belegter Datengrundlage: qualifizierte
Anfragen/100 Aufrufe, Verkauf pro 100 Anzeigen, Median bis Verkauf,
Erlös/Deckungsbeitrag, Kosten der KI je erfolgreichem Verkauf. Vorerst
bleiben `reaction_metric` und `objective_metric` frei wählbar und ohne
willkürlichen Standard.

## 6. Testgates und Lieferdefinition

- **Funktional:** vorhandene Dashboard-Create/Edit/Pause/Activate/Delete,
  Bildpfad, KI-Entwurf und Sync weiterhin integrierbar.
- **Skalierung:** 100, 1.000, 5.000 und 10.000 synthetische Anzeigen;
  Seitengrenzen, Unicode-Suche, Selektion und Navigation ohne Datenverlust.
- **Robustheit:** unklare Plattformantwort, SQLite-Fehler und Neustart
  dürfen keinen Blind-Retry oder eine vermeintlich leere erfolgreiche
  Kundendatenbank erzeugen.
- **Kunde:** keine technischen Token-/Browser-URLs im UI, einheitliche
  Windows-Bedienung, klare Handbuchfunktion; Endnutzer nicht mit internen
  Security-Protokollen belasten.
- **Integration:** kein ungetesteter `main`-Push, keine Tag-Überschreibung,
  kein Einsatz auf `fotolabor`; frühere v0.1.x-Releases bleiben verfügbar.

**Arbeitsnachweis:** Fortschritte und noch offene Phasen werden in dieser
Datei oder den dazu verknüpften Issues/PRs fortgeschrieben; Entwurf,
implementiert, synthetisch getestet, gemergt und offiziell veröffentlicht
sind getrennte Zustände. Nur tatsächlich belegte Gates gelten als erfüllt.
## 7. Implementierungsprotokoll – 26.10.2026

- **P0 offiziell veröffentlicht:** `v0.1.6`, PR #213 und Windows-Release auf Commit
  `8c6c163b320dc3a3f3f66dd83224ec8bd094c809`.
  Serverseitige Katalogseiten, Suche, Filter, Mehrfachauswahl und
  10.000 synthetische Bestandsdatensätze sind getestet.
- **P1 umgesetzt (Veröffentlichung erst nach PR-/CI-Gates):** gesonderte,
  `ad_snapshots`-unabhängige Tabelle `portfolio_drafts` innerhalb derselben
  sicherbaren SQLite-Datei, CSV/XLSX-Spaltenmapping, UTF-8-CSV,
  bis 5000 Entwürfe je Datei, SHA-256-gebundener Dry-run und atomarer
  Import mit konfliktfestem SKU, seitenweise Leseschnittstelle,
  Windows-Importvorschau, Entwurfs-CSV-Export und Übernahme ins
  bereits geschützte Neuanzeigenformular. Sämtliche Artikel bleiben
  **lokale, unveröffentlichte Entwürfe**.
- **P1 Mediengrenze:** Die optionale Spalte `images` speichert für den
  menschlichen Abgleich nur Dateinamen; kein automatisches Laden,
  Kopieren oder Hochladen von Bilddateien. Eine separate spätere
  Medienzuordnung muss Quellenbindung, Typ, Hash und Vorschau
  nachweisen. Dies ist kein Hindernis für den restlichen CSV/XLSX-Import.
- **P2 nächster Entwicklungsschritt:** aus lokalen SKUs stabile,
  persistierte und wiederaufnehmbare Aufträge mit *entwurf-bis-freigabe*
  bauen; KI-Kosten und Plattformänderungen bleiben getrennt
  und vor jedem tatsächlich kostenpflichtigen oder schreibenden Schritt
  ausdrücklich budget- und benutzergebunden.


## Umsetzungslage nach dem P1-/P2-Fortsetzungsauftrag (26.10.2026)

- **P0:** v0.1.6 offiziell veröffentlicht.
- **P1:** PR #219, geprüfter Branch-HEAD
  eca4d716bb010bd0c44f781b15a633ea98a58eb1.
  Windows-, Wheel- und Quell-CI grün, Review-Funde geklärt;
  geschützter Merge noch blockiert. v0.1.7 nicht als veröffentlicht
  kennzeichnen.
- **P2:** Auf dem isolierten Entwicklungsbranch
  feat/portfolio-ai-batch-source-20261026: lokale durable 5000er-Jobs,
  idempotente Auswahl, Budgetreserven, UNKNOWN-Quarantäne,
  50er-Vorschauen, Freigaben und atomare lokale Anwendung.
  Die zentrale Windows-Browseroberfläche bietet zusätzlich einzeln
  oder je bestätigter Serie bis zu 10 sequenzielle kostenpflichtige
  KI-Entwürfe an (maximal 100 Provider-Versuche je Produktsitzung).
  Tokenverbrauch wird konservativ in USD geschätzt, ist keine
  unabhängige Rechnung. **Nicht ausgeliefert**, bis Native CI,
  Release und P1-Abhängigkeit geklärt sind.
- **Weiter offen:** Provider-Rechnungsnachweise/hartes Budget,
  echte autorisierte Plattform-Massenwrites, P3, P4, P5 sowie
  Kunden-PC-Endabnahme. Siehe docs/portfolio-ki-auftraege.md.
  Der Vaterrechner fotolabor bleibt unangetastet.

- **P3 in Entwicklung:** Lokale manuelle Verkauf-/Reserviert-Vermerke,
  Lagerort, Erlös, bekannte Kosten und Teilgewinn sowie authentifizierte
  50er-Seiten und exact-ID-Revisionsabfragen implementiert. Diese lokalen
  Angaben sind keine bestätigten Plattformverkäufe. Nachrichtenintegration
  und native Windows-Abnahme bleiben offen. Siehe docs/portfolio-verkaeufe.md.

- **P4 Teilimplementation:** Read-only 7/30/90-Tage-Controlling
  mit EML-Belegzählung, aktuellem manuellem Verkaufsstand und
  separat ausgewiesenen USD-KI-Schätzungen sowie CSV-Export.
  10.000 synthetische Anzeigen ohne Vollbestands-JSON getestet.
  Keine behauptete Kausalität, kein automatischer Preis-/Titeltest;
  diese folgen erst nach zusätzlichen unabhängigen Datenquellen.
  Siehe docs/portfolio-controlling.md; noch kein Release.
