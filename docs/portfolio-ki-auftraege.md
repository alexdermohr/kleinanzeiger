# Kleinanzeiger – KI-Stapelaufträge (P2)

**Stand: 29. Oktober 2026.** Lokale Entwurfsaufträge und die getrennten
KI-Aufträge für bestätigte veröffentlichte Eigenanzeigen sind im Windows-Paket
v0.1.18 enthalten. Erweiterungen dieses Arbeitszweigs erfordern nach dem Merge
eine weitere versionierte Ausgabe und sind noch nicht in v0.1.18.
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

## Anleitung – KI für lokale Artikelentwürfe

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

## Verbleibende Abnahme und Grenzen

- Für neue Erweiterungen erneut Quell-, Wheel-, native Windows-3.12/3.13-
  und Installerprüfungen abschließen und ein neues unveränderliches Windows-
  Release veröffentlichen. Bereits veröffentlichte Versionen bleiben erhalten.
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

Aufgenommen werden höchstens 10.000 Anzeigen, deren jeweils neueste Beobachtung
aus einer der vier verifizierten Eigenbestandsquellen (Management, Product
Launcher, Owner Inventory oder Periodic Readonly) stammt, den
Status `active` oder `paused` hat und vollständigen Originaltext enthält.
Fremde, lokale Import-, fehlende und `UNKNOWN`-Quellen werden abgewiesen. Beim
Erstellen friert der Auftrag Anzeigen-ID, Beobachtungs-ID und -zeit, Quelle,
Status, Titel und Beschreibung samt SHA-256 ein. Derselbe `request_key` ist nur
für exakt dieselbe Nutzlast idempotent.

Ein bestätigter Provider-Aufruf reserviert sein Budget vor dem Aufruf. Pro
ausdrücklich bestätigtem UI-Stapel sind seit dem KI-100er-Ausbau höchstens
100 sequenzielle Aufrufe möglich (Vorauswahl bleibt zehn). Vor jedem Stapel
liest das Dashboard den persistenten Budgetstand und zeigt Anzahl sowie
reservierten Höchstbetrag an; tatsächliche Providerkosten können abweichen.
Die konfigurierte Dashboard-Generierungssitzung begrenzt alle Aufrufe global
auf maximal 100 inklusive anderer Generationen. Doppelklicks erzeugen keine
parallelen Stapel; Stop wirkt nach der laufenden Anfrage. Ein
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
aktuell verifizierten Eigenanzeigen (bis 10.000) wählen. Die Auswahl „alle“ wird
ohne riesige ID-Liste als leeres ad_ids-Feld übertragen; eine nicht leere
Liste im Modus all wird abgewiesen. Eine ausdrücklich markierte Auswahl
bleibt auf 10.000 IDs und 2 MiB JSON-Nutzlast begrenzt. Danach werden Anweisung,
Gesamtbudget und Reserve je Anfrage eingetragen und der Auftrag zunächst nur
lokal angelegt. Die Auftragsauswahl und die enthaltenen Anzeigen sind
seitenweise bedienbar; Start, Pause und Fortsetzen verändern ebenfalls nur den
lokalen Auftrag.

Jeder Provider-Aufruf braucht die sichtbare Kostenbestätigung. Wahlweise wird
genau der nächste Vorschlag oder ein ausdrücklich bestätigter Stapel von
höchstens 100 nacheinander erzeugt (Vorauswahl: zehn); **Stop** verhindert den nächsten Aufruf,
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

## Unterbrochene bestehende Anzeigen-KI-Aufträge nach Neustart klären

**Zusätzliche Funktion dieses Entwicklungszweigs (noch nicht Bestandteil des
veröffentlichten v0.1.18-Installationspakets):** Im Dashboard unter
**KI-Stapelaufträge → Bestehende Eigenanzeigen mit KI prüfen** erscheint bei
inflight oder unknown ein einklappbarer Bereich
**Unterbrochene oder unklare KI-Anfrage klären**.

- Erst nach beendetem KI-Aufruf bzw. Programmneustart
  **Unterbrochene Anfrage als unklar sichern** wählen. Der reservierte
  Kostenbetrag und eingefrorene Quellnachweis bleiben bestehen.
  Es findet **kein** zweiter Provider-Aufruf statt.
- Das tatsächliche Ergebnis und eine mögliche Berechnung unabhängig im
  KI-Anbieterkonto prüfen; die lokale Anwendung kann eine fremde Rechnung nicht
  verifizieren. Betroffene Anzeige der aktuellen Auftragsseite auswählen,
  unabhängig geprüfte US-Cent eintragen und die Prüfung bestätigen.
  0 Cent nur eingeben, wenn kostenfrei unabhängig nachgewiesen.
- **Unklaren Vorgang endgültig klären** verwirft die unsichere Antwort
  ausschließlich lokal, ersetzt die Reservierung durch den manuell geprüften
  Betrag und vermerkt manually_settled. Der alte möglicherweise berechnete
  KI-Aufruf wird **nie** erneut ausgeführt. Jeder Claim ist höchstens
  einmal klärbar.
- Sind alle unklaren oder laufenden Claims geklärt, wechselt ein angehaltener
  Auftrag in paused und kann mit **Fortsetzen** erneut freigegeben werden.
  Jeder neue kostenpflichtige Aufruf braucht eine erneute ausdrückliche
  Zustimmung. Plattformwrites bleiben unverändert null.
- Eine noch tatsächlich laufende Provider-Anfrage sperrt die lokale
  Abrechnungsklärung (HTTP 409). Auch ein Neustart ist keine Garantie,
  dass dem Anbieter keine Kosten entstanden sind.
