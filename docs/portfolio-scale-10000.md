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
Linux-A6-StorageOwner-Actor-Protokollpfad behält vorerst seine
bisherigen **2.048** Einträge je Generation. Dessen Erweiterung
verlangt eine separate Ende-zu-Ende-Abnahme der gesamten
UID-/RPC-/Recovery-Kette; die Windows-Verbesserung umgeht diese
Begrenzung nicht.

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

Die native Windows-3.12-/3.13-Abnahme führt die neuen synthetischen
Browser-Paginierungstests über die bereits bestehende
Katalog-Testgruppe aus, ohne GitHub-Workflow-Berechtigungen zu verändern.
