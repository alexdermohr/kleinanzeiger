# Verbindlicher Fortsetzungsauftrag: Kleinanzeiger nach Gesamt-Audit v0.1.11 vollständig fertigstellen

**Übergabe:** 28. Oktober 2026, ca. 16:20 Uhr (Europe/Berlin)  
**Produkt:** Kleinanzeiger, früher Mark · Windows-Kundenprodukt für Mark  
**Repository:** https://github.com/alexdermohr/kleinanzeiger  
**Operator:** Grabowski · https://github.com/heimgewebe/grabowski  
**Verbindliches, nachverfolgbares Master-TODO:** https://github.com/alexdermohr/kleinanzeiger/issues/240  
**Offizielles Ausgangsrelease:** https://github.com/alexdermohr/kleinanzeiger/releases/tag/v0.1.11  
**Am Übergabetag bestätigter main- und Release-Commit:** 3b0fe73806a0d8e96f8f6b98fae979347afe7585 (vor jeder Änderung frisch prüfen).

## 1. Verbindlicher Arbeitsauftrag

Übernimm vollständig und weitgehend autonom die Weiterentwicklung, Fehlerbehebung, Integration, technische Abnahme und Windows-Auslieferung von Kleinanzeiger. Das Ziel ist **ein vollständig bedienbares, zuverlässig funktionierendes, für hunderte bis 10.000 Anzeigen geeignetes KI-Portfolio-Managementsystem für Mark**, nicht ein weiterer Entwicklungsplan.

Arbeite die konkreten P0/P1/P2-Aufgaben aus **Issue #240** ab, implementiere und teste tatsächlich, führe fertige Änderungen nach main zusammen und liefere verifizierte Windows-Releases. Hinterlasse keine bloß vorbereiteten oder unverdrahteten Backendmodule. Stelle nur Fragen, die für einen unersetzbaren externen Zugang oder eine echte menschliche Produktentscheidung zwingend nötig sind; bearbeite unabhängig davon alles technisch Mögliche. Keine Hintergrundarbeitsversprechen.

**Anweisung des Auftraggebers zu Reviews:** Unabhängige Reviews sind ausdrücklich nicht erforderlich. Keine solchen Reviews anfordern und sie nicht als künstlichen Integrationsblocker verwenden. Bestehende Sicherheitsprüfungen, GitHub-Branchschutz, CI, echte Betriebsberechtigungen und Autorisierungen dagegen strikt beachten, nicht fingieren oder umgehen.

## 2. Kundenwünsche und Produktverhalten

Kleinanzeiger soll auf Windows aus einer zentralen Oberfläche möglichst einfach installierbar, aktualisierbar und bedienbar sein. Gewünscht sind Anzeigen erstellen, veröffentlichen, bearbeiten, pausieren, aktivieren, löschen; Bilder, Titel und Beschreibungen aus Prompts erzeugen und nach Freigabe übernehmen; eigener Bestand und Historien inklusive Views, Merker und Anfragen; Grafiken, Top-Listen und Vergleiche nach Bild-/Titel-/Texttyp, Stadt, Verkäufen und Marge; CSV-/Excel-Artikelimport und Fotovorlagen; KI-Verarbeitung ganzer Bestände; Interessenten und Nachrichtenvorschläge; Verkauf, Lager und Reservierung; Preise und Wiedervorlagen; verlässliche Synchronisation, Backups und Updates.

Die Bedienung muss bei 100 bis 10.000 eigenen Anzeigen ruhig, übersichtlich und fehlertolerant bleiben. Plattformwrites und externe KI-Aufrufe erfordern weiterhin tatsächlich vorhandene Berechtigungen und bewusste Freigaben. Bei unklarem Ergebnis niemals blind wiederholen. Hilfen und Fehlerbehandlung müssen für den Kunden verständlich sein; Sicherheitsprotokolle bleiben im Hintergrund, sind aber wirksam.

## 3. Historie und aktueller technischer Nachweis

Die ursprünglich gestapelten Produktänderungen aus PR #219 bis #236 einschließlich #228 wurden integriert. Danach:
- v0.1.9 / PR #237: Chrome-CDP-Bereitschaft vor Windows-Start, sachliche Fehlerdiagnose und Reparatur des Installationsversions-/Wheel-Prüfmusters.
- v0.1.10 / PR #238: mehrere vorhandene Chrome-Tabs sicher auswählen, Managementseite passiv auf Bereitschaft prüfen, gesonderte Transport-Fehlerstufen, sichtbarer Link für Fehlerdetails.
- v0.1.11 / PR #239: kompaktes Ein-Fenster-Startzentrum, seltene Aktionen einklappbar, fünf Hauptnavigationsbereiche, kontextbezogene Auswahlleiste, aktualisiertes Handbuch, vollständiger Windows-Release.

Auf dem letzten bestätigten main-Commit wurden unter Python 3.12 und 3.13 jeweils **2.254 Tests erfolgreich abgeschlossen, 34 Tests je Lauf übersprungen, 0 Fehler**. Dazu kommen bestandene offline installierte Wheel-Tests sowie native Windows-3.12-/3.13-Installations-, PowerShell-5.1-, Backup-, Release- und WinForms-Selbsttests. Das beweist die **technische Testbarkeit und Auslieferung**, nicht die Anmeldung oder volle Funktion auf Marks echtem PC.

**Wichtige reale Fehlerbeobachtung:** Auf dem Rechner namens **fotolabor** öffnete „Kleinanzeiger starten“ zwar Chrome bzw. das Kleinanzeigen-Konto, das Dashboard scheiterte aber trotz bestehender Anmeldung, zuletzt mit „private Web inventory read failed: transport_error“. Der Link „Details“ war in einer früheren Version nicht sichtbar, „Weitere Optionen“ löste ebenfalls einen Fehler aus. Versionsfixes v0.1.9/v0.1.10 sind getestet, aber **die echte Reparatur auf fotolabor nicht abgenommen**. fotolabor ist der Rechner des Vaters, **nicht der Kunden-PC von Mark**. Ohne separate Autorisierung keinerlei Programme, Browserprofile oder Daten darauf ändern, zurücksetzen oder entfernen; bei nicht vorhandener Fernanbindung keine Direktprüfung behaupten.

## 4. Bereits vorhandener Funktionsumfang – keine falschen Fertigmeldungen

- **P0:** Anzeigenkatalog aus SQLite mit Titel-/ID-Suche, Statusfilter, 50 Anzeigen pro Seite, seitenübergreifender lokaler Auswahl, CSV-Ausgabe und bis zu 10.000 synthetischen Anzeigen. Windows-Eigenbestandsleser hat die Grenze 256 Seiten / maximal 12.000 eigene Einträge; reale Langzeitabnahme fehlt.
- **P1:** CSV/XLSX-Artikelimport mit lokalen, unveröffentlichten Entwürfen (bis 5.000), SHA-/Dubletten-Prüfung, lokalem Fotoordner und Formularübernahme. Kein automatisches Publizieren.
- **P2:** Persistente lokale KI-Aufträge für bis zu 5.000 Entwürfe, Freigaben, konservative Kostenreserve, ausdrückliche Anbieteraufrufe, Pause und UNKNOWN-Sperre. Noch kein durchgehend automatischer Managementweg für veröffentlichte Anzeigen.
- **P3/P4/P5:** Manuell gepflegter Verkaufs-/Reservierungs-/Lagerstand, 7-/30-/90-Tage-Controlling, Arbeitsvorlagen und passive Hinweise. Keine automatisch verifizierten Plattformverkäufe oder laufender Autopilot.
- **G1:** Manuell gesetzte Prüftermine; echte Plattform-Ablauf-/Verlängerungsdaten sind unbekannt.
- **G2/G3:** Artikelvorlagen, lokale Duplikate und Fotozuordnung zu Entwürfen.
- **G4:** Die vorhandene Differenzlogik in src/mark_api/portfolio_inventory_diff.py ist **nicht vollständig in die Kunden-HTTP-/Dashboardoberfläche integriert**.
- **G5:** Die vorhandenen Preisregeln in src/mark_api/portfolio_price_rules.py sind **nicht vollständig in die Kundenoberfläche und HTTP-Route integriert**.
- **G6:** Kontrollierte, manuell bestätigte Status-Sammelaktionen und bestätigte Löschung bis zu zehn eigenen Anzeigen; bei Löschung zwei Schritte inklusive „LÖSCHEN N“, kein Blind-Retry.
- **G7:** Lokaler EML-Import, paginierte Gesprächsmetadaten, Revisions-Review und kopierbare lokale/KI-Antwortentwürfe. **Kein echter Plattform-Nachrichtenabruf und kein Versand.**

Ein vorhandenes Python-Modul ist nicht automatisch eine ausgelieferte Kundenfunktion. Für vollständige Umsetzung gehören API/Authentifizierung, UI, Hilfetext, Versions-/Updateweg und installierter Test dazu.

## 5. Verbindliche Reihenfolge der Restaufgaben

### P0: Verlässlicher Kundenstart und sichere Online-/Offline-Nutzung

**P0.1** Den echten Windows-Startfehler und die Browser-/Inventartransportpfade gezielt diagnostizieren. Vorhandene v0.1.11-Start-, CDP- und Fehlercodepfade plus synthetische Positiv-/Negativtests prüfen, bei nachweislich zugelassenem Zugriff reale nur-lesende Diagnose ermöglichen. Eindeutige Fehler wie Chrome-Browsersteuerung, falscher Tab, unauthentifiziertes Konto, gescheiterter JSON-Read und Datenbankfehler trennen. Keine Tokens/Konto-/Cookie-/Profiländerungen.

**P0.2** Sicheres Offline-Dashboard implementieren: Der normale Launcher scheitert derzeit am initialen Live-Inventarabruf und lässt vorhandene lokale Anzeigen nicht als Arbeitsoberfläche öffnen. Bei fehlgeschlagenem Live-Read soll eine nachweislich gesunde bestehende DB als **deutlich veraltet markierter lokaler Bestand** nutzbar sein. Plattformwrites und unfrische Owner-Autorität bleiben gesperrt bis zu erneutem, eindeutig erfolgreichem Sync. Keine fehlerbedingt erfundenen ABSENT- oder leeren Bestände, keine neue Datenbank bei versehentlichem falschem Pfad, keine Retry-Automatik für möglicherweise ausgeführte Writes. Tests mit Offline, Authfehler, beschädigter DB, Abbruch, Neustart und späterem erfolgreichen Sync.

**P0.3** Externe Plattformberechtigung je konkretem Konto, privatem/gewerblichem Profil, ProSellers und Zugriffskanal nach aktuellen Bedingungen prüfen. Das ist **kein pauschaler Stopp der technischen Entwicklung**; synthetische Tests sind möglich. Keine tatsächlichen nicht autorisierten Plattform-Reads, -Writes, -Nachrichten oder MFA/CAPTCHA-Umgehung.

**P0.4** Die vollständige Ziel-PC-Endabnahme auf **Marks Windows-PC** getrennt nachweisen: Version/SHA, Neuinstallation/Update, Anmeldung, Erst-/Wieder-Sync, Anzeigeauswahl, Create/Media/Content/Status/Delete (nur falls konkret autorisiert), KI-Entwürfe mit gesonderter Kostenfreigabe, EML, Backup/Restore und Fehlerszenarien. Ohne Zugang oder Zustimmung ehrlich „nicht abgenommen“ stehen lassen.

### P1: Kundenfunktionen aus dem bereits vorhandenen Code tatsächlich schließen

**P1.1 G4** Bestandsdifferenzen in Dashboard/HTTP verdrahten: neu, verändert, unklar, absent nur bei gesichertem vollständigem Eigenbestands-Read, Quellen- und Beobachtungszeiten, sichere Cursor/Filter, 10.000 synthetische Anzeigen.

**P1.2 G5** Preisregeln und -vorschläge mit Mindestpreis, Warten, Schritt, Rundung, Ausgangs-/Vorschlagspreis und Vorschau bedienbar machen. Kein unbeaufsichtigter Preiswrite; explizit berechtigte Änderungen nur durch die bestehende geschützte Plattform-Write-Kette.

**P1.3 G7** Autorisierten vollständigen Inbox-Read und nach ausdrücklicher Freigabe bestätigten Nachrichtensendepfad erschließen, soweit technisch und tatsächlich zulässig. Konto-/Anzeigen-/Konversationsbindung, Käufer-PII-Minimierung, pro Nachricht Send-Bestätigung/UNKNOWN und kein doppelter Send. Lokale EML-Metadaten bleiben getrennt von echter Plattform-Mailbox.

**P1.4 KI-Portfolio-Arbeit** für 100–10.000 bereits veröffentlichte eigene Anzeigen schließen: faktengebundene, budgetierte und bestätigte KI-Titel/Text/Bild-Vorschläge → Vorher/Nachher-Vorschau → ausdrückliche Freigabe → sicherer Einzel-Write → bestätigter Status oder Recovery. Bestehende lokale 5.000er-Jobkette nutzen, keine zweite Queue oder stillen Kosten.

**P1.5** Belastbare Realitäts-/Langzeittests: 100/1.000/5.000/10.000 Datensätze, 200+ Chrome-/Managementseiten, veränderte Pagination, p95-Latenz, Speicherverbrauch, Reconnect, Crash/Restart, unbekannte Ergebnissituation. Linux-A6-StorageOwner mit separater 2.048er Generation nicht umgehen.

**P1.6** Update-Rollback und externe sichere Datensicherung mit prüfbarer Wiederherstellung; bestehende lokale Backup-/WAL-/Manifest-/Recovery-Sperren erhalten. Keine komplette Profil- oder DB-Löschung.

**P1.7** Tatsächlich messbaren Verkaufserfolg ermöglichen, aber kein „beste Anzeige“ behaupten, bevor die zwei offenen fachlichen Metrikfragen aus Issue #1 ausdrücklich entschieden sind. Reaktions-Rohmetriken, belegte Verkäufe, Kosten, Marge und KI-Kostenschätzungen sauber trennen.

### P2: UX, Wartbarkeit, Diagnose und Abnahmedokumentation

**P2.1** Fünf Hauptbereiche aus v0.1.11 zu zuverlässigen Arbeitsansichten weiterentwickeln: gespeicherte Filter, sortierbare Tabellen, anpassbare Spalten, erreichbare Auswahlliste, Tastaturbedienung, Fehlertoleranz bei 10.000 Einträgen.

**P2.2** Verständliche Fehler- und Offlinezustände, gesonderte und geheimbereinigte Diagnosedetails, sicherer ausdrücklich ausgelöster Diagnoseexport, Windows-DPI-/Auflösungs-/Screenreadertests.

**P2.3** Großmodule (dashboard.py rund 7.800 Zeilen; private_web_cdp.py rund 4.450) ohne Framework-Neubau behutsam modularisieren, alle bisherigen Sicherheits-/API- und Recovery-Regeln exakt bewahren.

**P2.4** README, Roadmaps und Handbuch gegen den wirklich gelieferten Stand aktualisieren; historische Issues #57, #76, #111, #192, #214–#218 und weitere Storage/CDP-Findings auf dem aktuellen HEAD neu bewerten. Alte Issues nicht als aktuelle Reproduktion annehmen, aber auch nicht ohne Gegenbeleg schließen.

## 6. Vorgehensweise, technische Grenzen und Integrationsregeln

1. **Live-Wahrheit zuerst:** main/PRs/Issue #240, Release-Manifest, Windows-CI, Leases und Checkouts frisch lesen. Die SHA oben ist nur der geprüfte Ausgangszustand.
2. **Grabowski** als primären Operator verwenden. Isolierte eigene Worktrees, CAS-gebundene Git-Operationen, Fremdänderungen/Dirty-Worktrees/Fremdleases nicht überschreiben. Der separate Großer-Adler-Observer ist keine zwingende Review-Voraussetzung.
3. **Unabhängige Reviews sind durch den Auftraggeber explizit abgewählt.** Keine künstliche Warteschleife hierfür. Branchschutz, echte Merge-Autorität, sichere Regressionen und GitHub-CI hingegen nicht umgehen oder erfinden.
4. Bestehende Schutzmechanismen erhalten: eigene Konto-/Anzeigen-ID, Loopback-CDP, Origin/CSRF, browser-/operationengebundene Tokens, unveränderliche Idempotenzschlüssel, Pending/UNKNOWN-Fences, niemals Blind-Retry, zweimalige Löschfreigabe, kein ungefragter Provideraufruf.
5. Die vorhandene Python/SQLite/WinForms/Chrome-CDP-/Dashboard-Komposition ausbauen, nicht als zweites Produkt neu schreiben. Keine neuen Agenten-/Docker-/Node-/Messenger-Pflichten und keine ungenehmigte AGPL-Codeübernahme.
6. Jeder PR ist **vollständig umzusetzen**, mit installierter Wheel-/GUI-E2E, synthetischen Positiv-/Negativ- und Regressionstests, nativer Windows-CI unter Python 3.12/3.13, ggf. 10.000er-Lastbelegen, aktualisierter Kundenhilfe. Nur exakt grüne und freigegebene HEADs sicher mergen.
7. Jeder neue stabile Release wird unveränderlich publiziert mit ZIP, SHA-256, Manifest, Wheel, HTML-Handbuch, Funktionsübersicht und einfacher Windows-Updateanleitung. Bereits ausgelieferte Tags niemals überschreiben.
8. Fertigmeldung unterscheidet **implementiert → getestet → gemergt → veröffentlicht → auf echtem Kunden-PC abgenommen**. Ohne den letzten Nachweis nicht „optimal“ oder „alle Kundenfunktionen fertig“ behaupten.

## 7. Definition of Done und unmittelbarer Einstieg

Als fertig gilt nur die vollständige durchgehend benutzbare Kundenkette mit funktionsfähigem Start oder klar gesperrtem Offlinezustand, nachweisbar korrekten Eigentümerdaten, real nutzbaren G4/G5-Funktionen, zulässig autorisiertem G7-Nachrichtenweg, budgetiert bestätigten KI-Anzeigenänderungen, sicherem Langzeitbetrieb und belastbarem Backup/Update/Restore. Abschluss erfordert ein tatsächlich abgenommenes Kundenrelease **oder** eine offen ausgewiesene noch fehlende reale Kundenauthorisierung; eine synthetische Freigabe ersetzt diese nicht.

**Beginne sofort:** Frischen GitHub-/Operatorstand lesen, P0.1/P0.2 aus Issue #240 technisch bearbeiten; parallel unabhängig mögliche G4/G5-Verdrahtung vorbereiten. Überprüfte Änderungen in Kunden-PRs und Windows-Releases integrieren. Nicht bloß diese Übergabe noch einmal zusammenfassen.
