# Kleinanzeiger – KI-Stapelaufträge (P2)

**Stand: 26. Oktober 2026 – Entwicklungsbranch, noch kein ausgeliefertes
Kundenrelease.** Grundlage sind die lokalen CSV-/XLSX-Entwürfe aus P1.
Der Rechner fotolabor bleibt unverändert.

## Vorhandene Funktionen

- Maximal 5.000 **lokale Artikel-SKUs** pro Auftrag; SKUs sind niemals
  automatisch authentifizierte Kleinanzeigen-IDs. Auswahl über mehrere
  Katalogseiten oder alle Treffer des angewandten Suchfilters.
- SQLite-Aufträge: Idempotenz, gefrorene Originalfakten, bis zu 50 Zeilen
  pro Seite, Fortschritt, Pause/Fortsetzung/Abbruch, dauerhafte
  Budgetreservierungen und Quarantäne unklarer KI-Antworten (UNKNOWN).
- Vorschlagstexte werden erst nach ausdrücklicher Freigabe auf noch
  **unveränderte lokale Entwürfe** angewendet. Jede Änderung auf
  Kleinanzeigen verlangt weiterhin deren eigenständige autorisierte
  Write-Funktion. Keine automatische Plattformveröffentlichung.
- Optionales Textmodell gpt-5.6-terra mit explizit freizugebenden,
  kostenpflichtigen KI-Vorschlägen; keine Anbieteraufrufe beim Start.
- **Einzelschritt oder bis zu zehn sequenzielle Aufrufe pro bewusst
  bestätigtem Browser-Stapel.** Der Stop-Button beendet nach dem
  laufenden Aufruf die weiteren Anfragen. Netzwerk- oder API-Fehler,
  unbekannte Antworten und Sitzungslimits stoppen ohne automatische
  Wiederholung. Im Windows-Launcher höchstens **100 versuchte
  Text-/Bild-KI-Anfragen pro gestarteter Produktsitzung**.
- Mindestens **0,20 USD** Budgetreserve vor jeder potenziell
  kostenpflichtigen Anfrage. Anbieterpreis-Schätzung aus bestätigten
  Ein-/Ausgabe-Tokens mit Sicherheitsfaktor 2 (Stand 26.10.2026:
  2 USD bzw. 12 USD pro 1 Mio Tokens), 1.200 maximalen Ausgabetokens
  und höchstens 24 KB Eingabefakten. Diese Schätzung ist **keine
  nachgewiesene Rechnung und keine garantierte Kostenobergrenze**.
  Quelle zum Prüfen vor Ausführung:
  https://developers.openai.com/api/docs/pricing
- Browser-Jobänderungen benötigen lokale Write-Sitzung und Same-Origin-
  Token; kostenpflichtige KI-Aufrufe zusätzlich den separaten
  Generationstoken und die konkrete Zustimmung. Im getrennten
  A6-Owner/Frontend-Modus sind die lokalen Zusatz-Writes deaktiviert.

## Anleitung – noch nicht im offiziellen Kundenrelease enthalten

1. Im Windows-Kontrollfenster über **Artikel importieren** eine CSV-
   oder XLSX-Datei prüfen, sichern und lokal übernehmen.
2. Dashboard öffnen und unter **Artikelentwürfe** SKUs markieren.
   Unter **KI-Stapelaufträge** alternativ alle passenden Suchtreffer
   bis maximal 5.000 wählen.
3. Verbesserungsanweisung und Gesamtbudget in **USD** eingeben.
   Pro Anfragenschritt mindestens 0,20 USD reservieren.
   **Auftrag lokal anlegen** verursacht keine Anbieter-Kosten.
4. **Auftrag freigeben**; dieser Schritt startet noch keine KI.
   Wenn der Schlüssel im Windows-Startfenster eingerichtet ist,
   Einwilligung für die konkret gewählte Einzelanfrage oder bis zu
   zehn sequenzielle Anfragen geben. Kosten und Modell vorher prüfen.
5. **Nächsten Vorschlag** oder **KI-Stapelverarbeitung** wählen.
   Vor der Serie die angezeigte Anzahl/Budgetreservierung gesondert
   bestätigen. **Nach der laufenden Anfrage stoppen** verhindert
   weitere Aufrufe, ist aber kein Beweis einer Gebührenstornierung.
6. Original- und Vorschlagstext vor Verwendung vergleichen,
   einzelne Einträge freigeben/ablehnen und nach erneuter Bestätigung
   **nur lokal übernehmen**. Zuvor im Kontrollfenster sichern.

## Entwickler-CLI (keine Voraussetzung für Kunden)

Eine UTF-8-Auswahldatei enthält eine SKU je Zeile:

~~~powershell
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" create --skus-file "C:\Pfad\skus.txt" --instruction "Nur bekannte Fakten verbessern" --budget-cents 1000 --estimate-cents 20
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" list
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" detail JOB_ID
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" start JOB_ID
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" approve JOB_ID --sku SKU-00001
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" apply-local JOB_ID
~~~

Diese CLI führt **keine** externe KI-Anfrage aus. Bei unterbrochenem
oder unklarem Ergebnis vor einer neuen Anforderung Providerstatus und
Kosten unabhängig kontrollieren und den alten Claim bewusst klären:

~~~powershell
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" recover JOB_ID
kleinanzeiger-ki-auftraege --db "C:\Pfad\anzeigen.sqlite" settle-unknown JOB_ID --sku SKU-00001 --actual-cents 20 --checked
~~~

Der geprüfte Kostenwert darf nicht geraten werden. Das verwirft
einen unsicheren Vorschlag und erzeugt keine weitere KI-Anfrage.

## Offen vor Kundenfreigabe

- Zuerst P1 PR #219 integrieren und v0.1.7 regulär ausliefern.
- P2 über Quell-, Wheel- und native Windows-3.12/3.13-CI sowie
  PowerShell-5.1-Abnahme prüfen, Review abschließen, Version und
  HTML-Kundenhandbuch aktualisieren, unveränderliches Release erstellen.
- Eine unabhängige Anbieter-Rechnungsabstimmung und **harte** bestätigte
  Kostenobergrenzen fehlen; derzeit existieren nur Quoten,
  konservative Usage-Schätzungen und bestätigungspflichtige Aufrufe.
- Kontrollierte Plattform-Massenwrites mit Owner-, CDP-, Replay- und
  UNKNOWN-Gates fertigstellen. P3 Nachrichten/Verkäufe, P4
  Portfoliooptimierung und P5 regelbasierte Automatismen sind offen.
- Keine realen Anzeigen und keine kostenpflichtigen Anbieteraufrufe
  sind Teil der synthetischen Tests. Die native Abnahme auf dem
  tatsächlichen Kunden-Windows-PC steht aus, nicht auf fotolabor.
