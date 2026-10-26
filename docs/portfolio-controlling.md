# Kleinanzeiger – P4 Portfolio-Controlling (Entwicklungsstand)

**26. Oktober 2026 – noch nicht als Windows-Kundenrelease ausgeliefert.**
Diese Erweiterung baut auf P1 CSV-/Excel-Entwürfen, P2
KI-Stapelverwaltung und P3 lokalen Verkaufsvermerken auf.

## Verfügbare lokale Berichtsfunktionen

Im zentralen Dashboard unter **Controlling** werden die letzten
**7, 30 oder 90 Kalendertage (UTC)** gewählt.
Der Bericht zeigt in einer einzigen SQLite-Lese-Generation:

- Anzahl eigener Anzeigen mit belegter Historie aus drei anerkannten
  privaten Web-Eigenbestandslesern. Eine historische Beobachtung
  beweist keinen aktuellen Online-Status.
- Anzahl **lokal importierter** Eingangsnachrichten und
  unterschiedlicher importierter Gespräche der gewählten Periode,
  nur wenn die zugehörige Anzeigen-ID in dieser eigenen Historie
  vorkommt. Ein unvollständiger E-Mail-Import ist keine vollständige
  Kleinanzeigen-Postfachstatistik.
- Tageszahlen; die Oberfläche zeigt maximal die letzten 14
  Tage, eine **UTF-8-CSV** bietet alle 7/30/90 Tage mit
  geschützten ISO-Datumszellen und Excel-kompatiblen Zeilenenden.
- Manuell als verkauft/reserviert geführte Anzeigen,
  Erlöse, bekannte Kosten und bekannte Überschüsse.
  **Achtung:** Dies sind aktuelle, manuell eingetragene
  Gesamtwerte über alle Zeiten, keine dem gewählten Fenster
  zugerechneten Verkaufsevents. Unbekannte Einkaufskosten
  werden nicht als Null oder als Gewinn fehlinterpretiert.
- Geschätzte KI-Verbrauchskosten in **USD-Cent** und
  noch reservierte Beträge aus lokalen P2-Aufträgen.
  Anbieterabrechnung nicht belegt; unklare Aufrufe werden
  separat gezählt, nicht als kostenlos abgehakt.
  USD-Schätzungen werden nicht ohne Wechselkurs in EUR
  mit Verkaufserlösen addiert.

Alle Monats-/Wochenwerte entstammen der bestehenden lokalen
SQLite-Datenbank. SQLite normalisiert Zeitzonenoffsets der
EML-Importzeitstempel auf UTC-Kalendertage. Ungültige Zeiten
führen zu einem Fehler statt zu einem Bericht mit
stillschweigend fehlenden Nachrichten.

## Bedienung nach späterer Kundenfreigabe

1. Kleinanzeiger normal starten und den eigenen Bestand
   synchronisieren. Relevante EML-Benachrichtigungen bei
   beendetem Programm über das Kontrollfenster importieren.
2. Ggf. unter **Verkäufe & Lager** manuelle Status-/Erlös-
   und Kosteneinträge kontrollieren.
3. Dashboard → **Controlling** aufklappen; 7, 30 oder 90
   Tage auswählen und **Bericht berechnen** anklicken.
4. Den eingeblendeten Quellenhinweis berücksichtigen:
   Importnachrichten und aktueller manueller Verkaufsstand
   sind unterschiedliche Daten und keine Kausalkette.
5. **Tageszahlen als CSV** exportiert ausschließlich Datum
   und Anzahl importierter Nachrichten der gewählten Periode.
   Es werden keine Nachrichteninhalte, Käufernamen oder
   kontointernen Zugangsdaten exportiert.

## Tests und Offenpunkte

Synthetisch geprüft: Grenzwerte und fehlerhafte Eingaben,
Zeitzonenumrechnung, fremde/unbelegte Anzeigen, fehlende Kosten,
KI-Schätzungen ohne Providerrechnung, korruptes EML-Datum,
Sitzungsauthentifizierung, 10.000 eigene Anzeigen ohne
vollständigen 10k-JSON-Export sowie CSV-BOM, CRLF und
Schutz gegen Tabellenformeln. Kein realer Anzeigen-/Providerwrite.

P4 ist **erst teilweise erledigt**: belastbare Live-Verkaufs-
und Kontaktmetriken, experimentelle Titel-/Preisvorschläge,
qualifizierte Kontakte, kontrollierte A/B-Tests und
automatische Wochenberichte benötigen eigene Datenquellen,
Funktionen und Test-/Sicherheitsgates. Native Windows-
Abnahme, Merge, Release und Kunden-PC-Test sind ausstehend.
Der Vaterrechner fotolabor bleibt unangetastet.
