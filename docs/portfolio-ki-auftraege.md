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
# KI-Optimierung bestehender eigener Anzeigen

Neben lokalen CSV-Entwürfen unterstützt das Dashboard nun eine getrennte,
persistente Arbeitsliste für bereits veröffentlichte Eigenanzeigen. Die HTTP-
Schnittstelle liegt unter `/api/portfolio/existing-ai/` und verwendet dieselben
Dashboard-Sitzungs-, CSRF- und Origin-Prüfungen wie die übrigen lokalen
Portfolio-Befehle.

Aufgenommen werden höchstens 5.000 Anzeigen, deren jeweils neueste Beobachtung
aus einer der vier verifizierten Eigenbestandsquellen (Management, Product
Launcher, Owner Inventory oder Periodic Readonly) stammt, den
Status `active` oder `paused` hat und vollständigen Originaltext enthält.
Fremde, lokale Import-, fehlende und `UNKNOWN`-Quellen werden abgewiesen. Beim
Erstellen friert der Auftrag Anzeigen-ID, Beobachtungs-ID und -zeit, Quelle,
Status, Titel und Beschreibung samt SHA-256 ein. Derselbe `request_key` ist nur
für exakt dieselbe Nutzlast idempotent.

Ein bestätigter Provider-Aufruf reserviert sein Budget vor dem Aufruf. Pro
ausdrücklich bestätigtem UI-Stapel sind höchstens zehn Aufrufe möglich; die
konfigurierte Dashboard-Generierungssitzung begrenzt alle Aufrufe global. Ein
mehrdeutiger Fehler wird als `unknown` gespeichert, hält den Auftrag an und
wird nie blind wiederholt. Pause/Resume und die seitenweise Detailansicht
bleiben auch nach einem Neustart erhalten. Die Kosten sind konservative
Nutzungswerte, keine verbindliche Providerrechnung.

Vorschläge enthalten ausschließlich Titel und Beschreibung. Die geschützte
Dashboard-API stellt eingefrorenes Original und Vorschlag in derselben
Detailantwort nebeneinander bereit. Einzelne oder
mehrere Vorschläge können lokal `approved` oder `rejected` werden. Vor einer
Freigabe wird die aktuelle Originalrevision erneut mit dem Freeze verglichen;
bei Änderungen schlägt die gesamte Freigabe fehl. Eine Freigabe ist **keine**
Plattformautorisierung: dieses Feature enthält keinen Apply-, Publish- oder
sonstigen Plattformaufruf (`platform_writes: 0`).

## Bedienung im Dashboard

Unter **KI-Stapelaufträge → Bestehende Eigenanzeigen mit KI prüfen** kann der
Kunde entweder die auf der aktuellen Katalogseite markierten Anzeigen oder alle
aktuell verifizierten Eigenanzeigen wählen. Danach werden Anweisung,
Gesamtbudget und Reserve je Anfrage eingetragen und der Auftrag zunächst nur
lokal angelegt. Die Auftragsauswahl und die enthaltenen Anzeigen sind
seitenweise bedienbar; Start, Pause und Fortsetzen verändern ebenfalls nur den
lokalen Auftrag.

Jeder Provider-Aufruf braucht die sichtbare Kostenbestätigung. Wahlweise wird
genau der nächste Vorschlag oder ein ausdrücklich bestätigter Stapel von
höchstens zehn nacheinander erzeugt; **Stop** verhindert den nächsten Aufruf,
nicht den bereits laufenden. Vor jedem Aufruf prüft das Backend den
eingefrorenen Originalstand, die chronologisch neueste Beobachtung als
UTC-Instant und die weiterhin verifizierte Eigentumsquelle. Unklare,
unlesbare oder geänderte Quellen stoppen vor Providerkosten. Ein unklarer
Providerausgang stoppt ebenfalls und wird nicht automatisch wiederholt.

In der Tabelle lassen sich Original und Vorschlag für Titel und Beschreibung
aufklappen und vergleichen. Freigabe und Ablehnung werden lokal gespeichert.
Es gibt bewusst keinen Übernehmen-, Apply- oder Publish-Knopf: Die Freigabe ist
keine Plattformautorisierung und erzeugt keinen Kleinanzeigen-Write.

Aufträge und die Anzeigen eines Auftrags können jeweils mit 50 Einträgen pro
Seite vollständig durchlaufen werden. Providerkosten sind konservative lokale
Schätzwerte, keine garantierte
Rechnungsobergrenze. Titel (65 Zeichen) und Beschreibung (4.000 Zeichen) werden
vor Speicherung begrenzt; Modellantworten werden strikt als Objekt mit genau
`title` und `description` akzeptiert. Die native Abnahme auf dem tatsächlichen
Kunden-Windows-PC bleibt zusätzlich zu den automatisierten Browserlogik- und
HTTP-Tests erforderlich.
