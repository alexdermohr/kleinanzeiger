# Eigenbestand mit 10.000 Anzeigen – Windows-Paginierung

## Funktionsumfang

Der Windows-Kandidat liest aus der bereits angemeldeten, ausdrücklich
freigegebenen privaten Browser-Sitzung bis zu **12.000 eindeutig
identifizierbare eigene Anzeigen** über maximal **256 Seiten** der
Kleinanzeigen-Eigenbestandsliste. Damit sind beispielsweise 200 Seiten
zu je 50 Anzeigen, also 10.000 Anzeigen, vollständig erfassbar.

Die bisherigen Grenzen von 100 Seiten konnten bei größeren Portfolios
vorzeitig scheitern. Die neue Grenze gilt auch für die geschützte
Einzelanzeigensuche im Management-Editor; ein Eintrag auf Seite 200
wird nicht allein wegen der früheren Seitengrenze ausgeschlossen.

Diese Erweiterung ändert ausschließlich das Auslesen und die
Seitennavigation. Sie startet weder eine Veröffentlichung noch eine
kostenpflichtige KI-Anfrage und sendet keine Käufernachrichten.

## Voraussetzungen und Abbruchverhalten

- Die Plattform muss die vollständige eigene Anzeigenliste im
  Management-JSON mit konsistenter Seitengesamtzahl liefern.
- Alle Seiten müssen erfolgreich gelesen und genau derselben
  Bestandsbeobachtung zugeordnet sein. Fehlende, wechselnde oder
  widersprüchliche Seiten und doppelte Anzeigen-IDs führen zu
  einem Fehler statt zu einer als vollständig ausgegebenen Teilliste.
- Die Grenze bleibt fest: über 256 Seiten oder über 12.000 Einträge
  wird ohne ungeprüfte Fortsetzung abgebrochen.
- Die Prüfung behauptet keine Live-Abnahme am Kundenkonto. Alle
  10.000er-Simulationen verwenden lokale synthetische JSON-Antworten.
- Bestehende Daten, Anzeigeneigentum und Wiederholschutz bleiben
  erhalten. Vor dem Abgleich großer Bestände eine Sicherung erstellen.

## Technischer Geltungsbereich

Dieser Patch erweitert den nativen privaten CDP-Reader sowie den
streng zugelassenen Management-Adapter. Der unabhängige
Linux-A6-StorageOwner-Pfad wurde inzwischen in der bestätigten
Inventargenerationsgrenze von **2.048 auf 12.000** Datensätze erweitert
(`storage_owner._MAX_SYNC_ITEMS = 12_000`, synthetische
Direkt-/Unix-Socket-Regressionen in
`tests/test_storage_owner_capacity_10000.py`). Die separate
**Inbox-Batchgrenze bleibt bei 2.048** Datensätzen. Die Erweiterung ist
keine Behauptung über einen tatsächlich mit 10.000 Anzeigen angemeldeten
Kundenaccount oder eine produktive Plattformberechtigung.

Die lokale Windows-SQLite-Katalogdarstellung und das Controlling
besitzen bereits separate, erfolgreich getestete 10.000er-Paginierungs-
und Report-Szenarien. Dies ist keine Zusage einer live verifizierten
Kleinanzeigen-Massenaktualisierung oder eines automatischen
Anzeigen-Autopiloten.

## Synthetische Abnahme

Die neuen Tests prüfen 200 Seiten mit 50 Anzeigen,
10.000 persistierte SQLite-Beobachtungen, eine weitere
200-Seiten-Verifikation des isolierten Adapters sowie harte
Negativfälle für 257 Seiten, mehr als 12.000 IDs, doppelte IDs und
wechselnde Pagination. Kein echtes Konto wird angesprochen.

Die native Windows-3.12-/3.13-Abnahme führt die synthetischen
Browser-Paginierungstests über die bestehende
Katalog-Testgruppe aus. Zusätzliche heim-pc-Lastmessungen müssen
ihren exakten Quellstand, Datenumfang, P95 und Speicherverbrauch nennen.
Keine noch nicht gemessene Laufzeit wird als garantiert bezeichnet.


## Frische heim-pc-Messung – 6. November 2026

**Quellbasis:** Windows v0.1.45 / `main 44b8ac86` mit dem lokalen
P3-/P4-Funktionspatch im isolierten Arbeitszweig; Testtask
`26325b931af44fdf9aaf1aba` auf **heim-pc**.
Der Test verwendete ausschließlich eine temporäre SQLite-Datenbank mit
**10.000 synthetischen eigenen Anzeigen**, **10.000 synthetischen
EML-Ereignissen** und **10.000 lokalen Eigenanzeigen-Preisregeln**
(ca. 4,34 MB). Die Messung umfasst keine Netzwerklatenz, Plattform-
Cookies, echten Besitzerabgleich oder Verteilung über mehrere Konten.

| Testweg | Messungen | P95 (ms) |
| --- | ---: | ---: |
| 10.000 Anzeigen, indexierte Katalogseiten à 50 | 200 | 0,556 |
| 10.000 Preisregeln, Seiten à 50 | 200 | 3,751 |
| Lokaler authentifizierter HTTP-Preisplan, 50 Einträge | 16 | 3,671 |
| Lokaler authentifizierter HTTP-7-Tage-Bericht | 16 | 59,379 |
| Python-Bericht 7 Tage | 20 | 59,249 |
| Python-Bericht 30 Tage | 20 | 76,587 |
| Python-Bericht 90 Tage | 20 | 78,721 |

P95 nach aufsteigend sortierten Laufzeiten, Rang
`ceil(0,95 × Stichprobenzahl)`; bei Python-Berichten und HTTP
wurden die ersten zwei Aufrufe als Warm-up ausgelassen.
**Peak-RSS:** 39.040 KiB im isolierten Messprozess;
**tracemalloc-Peak nach Datengenerierung:** 182.706 Bytes.
Der 90-Tage-Report enthielt maximal 9.188 JSON-Bytes. Die Ergebnisse
belegen diesen synthetischen Workload, nicht allgemeine SLA-Grenzen.

Zusätzlich wurden mit `tests/test_storage_owner_capacity_10000.py`
auf heim-pc vier A6-Regressionen frisch ausgeführt (Task
`c2ac33a1ee4e45128555ff26`, **4/4 erfolgreich**).
12.000 direkt gespeicherte Inventarbeobachtungen benötigten 3,853 s,
mit 0,889 ms gemessenem RPC-Dispatch-P95 und rund 13,76 MB
tracemalloc-Peak; eine 10.000er-Unix-Socket-Generation dauerte 2,501 s.
Der **12.001.** Eintrag wurde vor einem Owner-RPC abgewiesen.
Dies testet die Grenze, nicht eine reale Plattformantwort.

**Noch nicht bewiesen:** Chrome/CDP mit einem echten 10.000er-Konto,
p95 unter längerem Netzwerkausfall, Windows-Gast-Speicherverbrauch
sowie wiederaufgenommene echte Plattformschreibvorgänge.
