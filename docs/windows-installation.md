# Kleinanzeiger v0.1.29 – Windows 11

**Stand v0.1.29:** Sichtbare Startkommandos und Fehler heißen Kleinanzeiger. Das Update unterstützt frühere installierte Versionen bei Backup, Restore, Rollback und Programmstart ohne erneute Einrichtung der Daten. Die bisherigen Kontodaten und Chrome-Profile bleiben beim Update unverändert. **Bereits in v0.1.22:** Der A6-StorageOwner-IPC und die Eigenanzeigen-Lese-CLI wurden synthetisch auf bis zu 12.000 Datensätze erweitert. Der Windows-Katalog unterstützt weiterhin bis zu 10.000 bestätigte eigene Anzeigen; die KI-Prüfung bestehender Eigenanzeigen umfasst seit dieser Version ebenfalls bis zu 10.000 pro Auftrag (lokale Entwürfe bleiben auf 5.000 begrenzt). Der echte Konto-/Chrome-Abgleich auf Marks Kunden-PC ist noch separat zu prüfen. Das Update verändert keine vorhandenen Anzeigen, Backups oder Chrome-Profile.

**Neu in v0.1.29 – Anzeigenabgleich im Kleinanzeiger-Chrome:** Chrome liefert bei vielen Werbe-Iframes mehr als 64 KiB an lokalen DevTools-Zielen. Die Erkennung akzeptiert jetzt begrenzt bis 1 MiB, ohne allgemeine CDP-Befehlsgrenzen zu erweitern. Der ausdrücklich ausgelöste Wiederabgleich funktioniert in Windows-Workerthreads auch ohne POSIX-Signalmaske mit kooperativer Abbruchprüfung. Nach dem Update Kleinanzeiger über das Kontrollfenster beenden und neu starten; der vorhandene Chrome-Profilordner bleibt erhalten. Ein erneut fehlgeschlagener Read überschreibt keine bisherigen Anzeigen; auf Marks Konto steht die Live-Abnahme weiterhin aus.

**Bereits in v0.1.28 – optionale Konto-Nachrichten und getrennte KI-Felder:** Im zentralen Windows-Kontrollfenster unter *Weitere Aktionen → Nachrichten verwalten* kann ein Kunde selbstständig die separat kostenpflichtige Konto-API von Kleinanzeigen Agent (unabhängiger Beta-Anbieter; nicht offiziell von kleinanzeigen.de) einrichten. Der persönliche API-Schlüssel wird in einer eigenen PowerShell-Abfrage verdeckt eingegeben und mit Windows-DPAPI geschützt. Nur auf Klick werden Chatliste, Gespräch (ggf. als gelesen markiert) oder eine vollständig vorab angezeigte und anschließend ausdrücklich bestätigte Nachricht über den Anbieter geladen oder gesendet; das kann Credits kosten. Vor dem Senden den aktuellen eigenen Anzeigen- und Gesprächsstand prüfen. Kleinanzeiger wiederholt unklare Sendungen nicht und speichert keinen Nachrichtentext im Versandjournal. Ohne die eigene Konto-API finden keine externen Nachrichtenaufrufe statt. Außerdem können lokal freigegebene KI-Texte separat über *Titel kopieren* und *Beschreibung kopieren* übernommen werden. Auf Marks PC ist weder die Konto-Verbindung noch der echte Versand abgenommen.

**Bereits in v0.1.27 – freigegebene KI-Texte kopieren:** Im Dashboard unter *Bestehende Eigenanzeigen mit KI prüfen* einen lokalen KI-Auftrag öffnen und den vollständigen Titel-/Beschreibungsvorschlag für eine eigene Anzeige prüfen und freigeben. Nur dann erscheint **Freigegebenen Vorschlag kopieren**. Damit werden allein der neue Titel und die neue Beschreibung in die Browser-Zwischenablage übernommen. Es wird weder eine Anzeige auf Kleinanzeigen geändert noch eine weitere KI-Anfrage gestartet. Ohne Browser-Zwischenablageberechtigung erscheint eine Fehlermeldung; den Vorschlag dann manuell markieren. Eine echte Konto-Übernahme erfordert weiterhin gesonderte Berechtigung, Prüfung und Benutzerentscheidung.

**Bereits in v0.1.26 – Katalogansicht nach Neustart:** Nach erfolgreich geladener Suche bleiben Suchtext und Statusfilter im selben Browserprofil gespeichert. Fehlgeschlagene Suchen bewahren die letzte erfolgreiche Ansicht; abgeschalteter Browserspeicher verhindert keine Suche. Kein Speichern von Auswahlen, Konto-Sessions, Freigaben oder Plattformaktionen. Die echte Abnahme auf Marks Windows-PC bleibt separat.

**Bereits in v0.1.25 – Programm-Rückwechsel:** Nach *Weitere Aktionen → Vorherige Programmversion verwenden* startet das aktuelle Windows-Kontrollfenster die wiederhergestellte ältere Programmversion auch bei fehlender oder fremder Chrome-Steuerung über einen mitinstallierten Kompatibilitätshelfer im lokalen Arbeitsmodus. Der Hilfsprozess reagiert auf den privaten STOP-/EOF-Kanal und beendet sich geordnet. Anzeigen, Sicherungen und Chrome-Anmeldungen bleiben unverändert. Falls die alte Version die aktuelle Datenbank nicht sicher lesen kann, verweigert Kleinanzeiger weiterhin den Rückwechsel.

**Verbessert in v0.1.24:** Kleinanzeiger verifiziert vor dem Kontozugriff, dass Chrome-Port 9222 wirklich zu seinem eigenen dauerhaften Browserprofil gehört. Falls ein anderer Prozess den Port belegt oder Chrome nicht zuverlässig erkannt wird, startet ausschließlich die lokale Bestandsansicht ohne Plattformzugriffe. Kleinanzeigen- und Google-Anmeldungen im eigenen Profil bleiben gespeichert; das normale persönliche Chrome wird nicht verändert. Nach Klärung des Browserfensters Kleinanzeiger neu starten. Im Dashboard lädt der Bestandsvergleich nach schnellen Filterwechseln automatisch die zuletzt gewählte Ansicht.

## Einmal entpacken, dann nur noch Kleinanzeiger öffnen

1. Das vollständige Windows-ZIP in einen eigenen Ordner entpacken.
2. **Kleinanzeiger.cmd** doppelklicken. Das Kleinanzeiger-Fenster öffnet sich.
3. **Einrichten und starten** anklicken. Falls Python 3.12/3.13 fehlt, wird
   es aus einer offiziellen, SHA-256- und signaturgeprüften Python-Datei
   automatisch bereitgestellt. Kleinanzeiger und seine Abhängigkeiten werden
   isoliert installiert. Fehlendes Chrome wird signiert von Google geladen.
4. Im geöffneten separaten Chrome-Profil **bei Kleinanzeigen anmelden**.
   Danach gegebenenfalls im Kleinanzeiger-Fenster erneut **Starten** klicken.
   Das Dashboard öffnet sich automatisch.
5. Danach den Desktop-Eintrag **Kleinanzeiger** benutzen. Der ursprünglich
   entpackte Installationsordner wird für den normalen Start nicht benötigt.

Keine Konsoleingaben, manuelles Python, Docker, WSL, Ubuntu oder Kopieren
technischer Dashboard-URLs erforderlich.

**Anmeldung bleibt gespeichert:** Kleinanzeiger verwendet ein dauerhaftes eigenes Chrome-Profil unter dem Kleinanzeiger-Appordner. Es wird weder beim Programmstart noch beim Beenden gelöscht. Wer sich in diesem Chrome bei Kleinanzeigen und gegebenenfalls beim Google-Konto anmeldet, muss dies nicht bei jedem Kleinanzeiger-Start wiederholen, solange die jeweilige Sitzung gültig bleibt. Das normale persönliche Google-Chrome-Profil wird nicht geändert; dessen Google-Login wird nicht automatisch in das separate Produktprofil übernommen.


## Alles an einem Ort

Das Kontrollfenster startet in einer **kompakten Ansicht**: Starten,
Installieren/Aktualisieren, Konto, Artikelimport, Sicherung und KI-Zugang
sind direkt sichtbar. Über **Weitere Aktionen** lassen sich E-Mails
importieren, Vergleichsmerkmale bearbeiten, Entwürfe exportieren und
eine Sicherung in eine *noch nicht vorhandene* Datenbank wiederherstellen.
**Weniger anzeigen** schließt diesen Bereich. Es sind keine weiteren
Werkzeugfenster oder Konsoleingaben erforderlich.

**Fehlerdetails anzeigen** erscheint nur bei einer fehlgeschlagenen
Aktion unter der Statusmeldung. Kein Zugriff auf Login-Passwörter,
Chrome-Profil oder bestehende Datenbank ist dafür erforderlich.


Das Kleinanzeiger-Fenster bietet:

- **Kleinanzeiger starten / beenden**: öffnet das Konto und danach das
  Dashboard automatisch. Beenden nutzt den privaten stdin-Kanal und den
  vorhandenen kontrollierten Shutdown. Laufende Änderungen zuvor abschließen.
- **Installieren / aktualisieren**: prüft das offizielle GitHub-Release,
  ZIP-/Wheel-SHA-256 und Manifest. Vor einem Update wird automatisch ein
  SQLite-Backup mit Manifest erstellt.
- **Artikel importieren**: CSV-/Excel-XLSX-Datei mit Artikelnummer, Titel, Beschreibung, Kategorie und vollem Europreis wählen. Die Vorprüfung zeigt Zuordnung, neue/identische Artikel und Konflikte. Nur nach Bestätigung werden die Datensätze mit automatischem Backup als lokale unveröffentlichte Entwürfe übernommen; niemals automatisch veröffentlicht.
- **Entwürfe exportieren**: alle lokalen Artikelentwürfe als CSV zum Bearbeiten und erneuten Einlesen ausgeben. Export überschreibt keine bestehenden Dateien.
- **E-Mails importieren**: importiert ausdrücklich ausgewählte lokale Kleinanzeigen-EML-Dateien (vorher die Anwendung beenden) und erstellt davor eine verifizierte Sicherung.
- **Anzeigen vergleichen**: wählt bestehende oder durch E-Mail-Import belegte Anzeigen aus und pflegt Bildtyp, Stadt, Texttyp und Titeltyp für Gruppenrankings (ebenfalls bei beendetem Produkt).
- **Handbuch / Funktionen**: öffnet die vollständigen HTML-Kundenunterlagen aus dem installierten Paket.
- **Sicherung erstellen**: sichert vorhandene Daten online, ohne eine
  Datenbankdatei manuell zu kopieren.
- **KI einrichten**: eigenen OpenAI-API-Schlüssel verdeckt eingeben oder
  entfernen. Er wird per Windows-DPAPI an das lokale Benutzerkonto gebunden.
  Jeder externe kostenpflichtige Text-/Bildaufruf benötigt weiterhin eine
  eigene Bestätigung im Dashboard.
- **Kleinanzeigen-Konto öffnen**: für die erste Anmeldung und MFA im
  getrennten Chrome-Profil.
- **Weitere Aktionen / Wiederherstellen**: nur bei noch nicht vorhandener
  Datenbank samt WAL/SHM/Journal, mit ausdrücklicher Zustimmung und
  Manifestprüfung.

Im Dashboard führen fünf Hauptverknüpfungen zu **Übersicht**,
**Meine Anzeigen**, **Artikel & KI**, **Interessenten** und
**Auswertungen**. **Weitere Bereiche** enthält die seltener benötigten
Werkzeuge: Neue Anzeige, KI-Stapelaufträge, KI-Entwürfe,
Verkäufe & Lager und Controlling. Es wird keine Funktion entfernt. Unter Controlling kann für lokale EML-Berichte die Tageskennzahl zwischen eingegangenen Nachrichten und unterschiedlichen Gesprächen je Tag umgeschaltet und entsprechend als CSV exportiert werden; eindeutige Käufer sind aus EML allein unbekannt.
Die Anzeigenverwaltung bleibt links, die Bearbeitung auf breiten
Bildschirmen daneben; auf schmalen Fenstern stehen sie untereinander.

Für größere Bestände zeigt **Meine Anzeigen** nur 50 Anzeigen pro Seite.
Über **Suche nach Titel oder Anzeigen-ID** und **Status** kann der
lokale Bestand gefiltert werden. **Weiter / Zurück** blättert seitenweise.
Die Auswahlkästchen lassen sich über Seiten hinweg sammeln. Im
**Ab v0.1.17** wählen **Sichtbare auswählen (max. 50)**
und **Sichtbare abwählen** ausschließlich die bereits geladenen Anzeigen
dieser Katalogseite; eine Auswahl von anderen Seiten bleibt erhalten.
Die beiden Schaltflächen lösen weder eine Plattformaktion noch einen
KI-Aufruf aus. Erst wenn mindestens eine Anzeige markiert ist, erscheint
die **Aktionsleiste für ausgewählte Anzeigen**. **Auswahl als CSV**
speichert ausschließlich die ausgewählten Anzeigen-IDs; **Auswahl leeren** setzt sie zurück. Bei mehr
als zehn ausgewählten Anzeigen bleiben Plattform-Sammelaktionen gesperrt,
die reine CSV-Auswahl ist weiterhin möglich. CSV-Export und Markieren allein ändern **keine** Plattformanzeige.
Mit gültiger Schreibsitzung lassen sich **1 bis 10 eigene Anzeigen** nach
vollständiger Bestätigung **nacheinander pausieren oder aktivieren**.
Eine endgültige Sammellöschung erfordert zusätzlich die zweite,
anzahlgebundene Bestätigung **LÖSCHEN N**.
Vor jedem Einzelauftrag wird der lokale Stand erneut geprüft. Bei
unbekanntem Ergebnis, geändertem Status, Recovery oder auf Wunsch wird
der Stapel gestoppt, ohne automatischen Plattform-Retry.
**Unbeaufsichtigtes Massenlöschen, Massenpreisänderungen und
Sammelveröffentlichung** sind nicht enthalten.
Einzelne Anzeigen werden weiterhin über **Bearbeiten** verwaltet.

Über **Mit Kleinanzeigen abgleichen** werden eigene Anzeigen direkt aus der
angemeldeten Browser-Sitzung neu gelesen. **Ansicht neu laden** aktualisiert
hingegen ausschließlich die lokal gespeicherte Darstellung. Seit v0.1.19 nennt
ein fehlgeschlagener manueller Abgleich einen zutreffenden sicheren Grund und
eine konkrete Lösung für Browser/Chrome, Anmeldung, Netzwerk, einen bereits
laufenden Abgleich, Recovery oder lokalen Speicher. Das behauptet keinen
gelösten Chrome-Transport und löst keine automatische Wiederholung aus. Ab v0.1.20
bleibt nach einem initial fehlgeschlagenen Browserread das Offline-Dashboard bestehen;
der ausdrücklich manuelle Wiederverbindungsversuch zeigt dieselbe tatsächliche sichere
Klassifikation statt einer pauschalen Fehlermeldung. Es gibt keine Blind-Retries,
keine Datenüberschreibung und keine wiedererlangten Plattformwrites ohne neue Sitzung.

## Bei Verbindungsproblemen lokal weiterarbeiten

Ab v0.1.12 öffnet der normale Start bei nicht erreichbarer Browsersteuerung oder
fehlgeschlagenem ersten Inventarabruf den **lokalen Arbeitsmodus**, sofern die
bestehende Datenbank gesund ist und kein ungeklärter Synchronisationsvorgang
vorliegt. Der zuletzt gespeicherte Bestand bleibt erhalten. Ein fehlgeschlagener
Abruf bedeutet **nicht**, dass Anzeigen gelöscht wurden oder das Konto leer ist.
Die Oberfläche kennzeichnet den veralteten Stand ausdrücklich.

Lokale Artikel, Preisregeln, Aufträge, Verkaufsnotizen und Auswertungen bleiben
bedienbar. **Sämtliche Plattformänderungen sind gesperrt.** Mit **Mit Kleinanzeigen
abgleichen** wird einmal ausdrücklich erneut gelesen. Nach einem erfolgreichen
Abgleich bleibt diese Sitzung lokal: Für Plattformänderungen Kleinanzeiger
beenden und neu starten. Der Neustart muss den Eigenbestand erneut bestätigen.
Es werden keine Browserprofile, Datenbanken oder Recovery-Zustände zurückgesetzt.
Bei beschädigter Datenbank oder ungeklärten Vorgängen gibt es keinen scheinbar
erfolgreichen Fallback. Ein noch nie bestätigter Bestand wird nicht als bestätigtes
leeres Konto ausgegeben. KI bleibt optional und benötigt weiterhin ihre Kostenfreigabe.

## Bestandsänderungen und lokale Preisregeln

**Weitere Bereiche → Bestandsänderungen → Bestand vergleichen** zeigt neue,
geänderte, unveränderte und nicht mehr sichtbare Anzeigen aus den letzten zwei
bestätigten Kontoabgleichen, mit Quelle und Zeitpunkt. Ab v0.1.15 kann
unter **Änderungstyp** ein globaler Filter für neue, geänderte, pausierte,
unklare, unveränderte, ohne Vergleichsbasis oder unbestätigte Anzeigen
gewählt werden. Die Filterung durchsucht den gesamten bestätigten
Eigenbestand (synthetisch bis 10.000 Anzeigen geprüft), nicht nur die
aktuelle 50er-Seite. **Nächste 50** blättert durch passende Treffer;
ein Filterwechsel setzt den Cursor und den gebundenen Vergleichszeitstand
zurück. Die Anzahl passender Treffer im gesamten Bestand wird nicht
vorgetäuscht. Bei einem fehlgeschlagenen
Abgleich bleibt die Aussage unbestätigt. Nicht mehr sichtbar ist kein Beleg für
Verkauf oder Löschung. Bei Änderungen während des Blätterns den Vergleich neu laden.

Unter **Artikelentwürfe** einen importierten Artikel suchen und **Preisregel**
wählen; alternativ **Weitere Bereiche → Preisregeln** öffnen und die lokale
Artikelnummer laden. Ausgangspreis, Mindestpreis, Änderungsschritt in ganzen Euro
und Wartezeit in Tagen prüfen. Die Vorschau zeigt genau einen möglichen Schritt,
begrenzt durch den Mindestpreis. **Regel lokal speichern** speichert ausschließlich
die Regel. Die Wartezeit beginnt mit dieser Speicherung, nicht mit einem vermuteten
Plattformereignis. Weder der lokale Artikelpreis noch eine Plattformanzeige wird
geändert. Gespeicherte Regeln lassen sich laden, ändern und entfernen. Nach einer
unklaren Speicherantwort zuerst neu laden, nicht blind wiederholen.

## Optional: echte Kleinanzeigen-Chats über externe Konto-API

Die ausführliche Einrichtung und alle Funktionsgrenzen stehen in **Konto-Nachrichten.md**, die der Windows-Ausgabe beiliegt.

Nach eigenständiger Einrichtung eines kostenpflichtigen Kontozugangs bei
**Kleinanzeigen Agent** (unabhängiger Beta-Dienst, nicht offiziell von
kleinanzeigen.de) im Windows-Kontrollfenster unter **Weitere Aktionen →
Nachrichten verwalten** einen persönlichen Konto-API-Schlüssel geschützt mit
Windows-DPAPI hinterlegen. Der optionale Zugang kostet laut Anbieter
9,90 EUR netto pro Monat zuzüglich Credits. Kontoanmeldung und Buchung geschehen
nicht durch Kleinanzeiger; die Nutzung kann Kontoeinschränkungen mit sich bringen.

**Verbindung prüfen** benötigt keine Credits. Chats laden, einen Chat öffnen
(kann ihn als gelesen markieren) und eine Antwort senden werden erst nach
bewusstem Klick ausgeführt. Antworten kosten beim Anbieter gegebenenfalls
Credits und erfordern eine ausdrückliche Einzelfreigabe. Ein unbestätigter
Versand wird dauerhaft als unbekannt behandelt und nicht automatisch erneut
gesendet. Nachrichten und Käuferdaten werden nicht im separaten Versandjournal
gespeichert. Ein lokaler Anzeigenbestand ist kein Beweis aktueller Eigentümerschaft;
vor der Antwort das aktuelle Konto und den Gesprächspartner selbst prüfen.

Die tatsächliche Funktion auf Marks angemeldetem Windows-Kunden-PC bleibt
bis zum verbundenen Konto-API-Zugang separat abzunehmen. Das bestehende
**Interessenten-&-Antworten-Formular** ist weiterhin ein lokaler EML- und
KI-Entwurfsarbeitsbereich ohne eigenen Plattform-Versand. Eine unbeaufsichtigte
KI-Nachrichtenautomatik wird nicht aktiviert.

## Interessenten und Antwortentwürfe

Unter **Meine Anzeigen** eine eigene Anzeige suchen und **Antwort vorbereiten**
anklicken. Im Bereich **Interessenten & Antworten** einen bereits erhaltenen
Nachrichtenausschnitt ohne personenbezogene Daten eingeben und nur tatsächlich
bekannte Fakten hinzufügen. **Neutrale Antwort beginnen** ist kostenlos und
arbeitet rein lokal. **KI-Antwortentwurf erzeugen** braucht den eigenen
KI-Schlüssel, die aktive KI-Sitzung und für jeden Aufruf eine neue ausdrückliche
Zustimmung zur Promptübertragung und zu möglichen Kosten. Den Text prüfen,
bearbeiten und bei Bedarf **Antworttext kopieren**.

**Kleinanzeiger liest keine vollständigen Plattform-Chats aus und versendet
keine Nachrichten.** Die Nachricht und der Antwortentwurf werden in dieser
Ansicht nicht dauerhaft gespeichert. Der Nutzer versendet den kopierten Text
selbst im originalen Kleinanzeigen-Dialog.

## Kostenloser Autopilot-Tagesplan (ab v0.1.22)

Im Dashboard unter **Bestandsänderungen → Priorisierter Tagesplan (lokal und kostenlos)** auf **Aufgaben prüfen** klicken. Die Anwendung zeigt zuerst unklare/laufende KI-Kostenansprüche, anschließend neue lokal importierte EML-Hinweise, überfällige selbst gesetzte Wiedervorlagen, ungeprüfte lokale KI-Vorschläge sowie manuelle Termine der nächsten sieben Tage. Bis zu 50 Aufgaben je Seite, auch bei 10.000 gespeicherten Einträgen. Eine Anzeige kann mehrere Prüfpunkte haben.

**Nur lokale Hinweise:** Keine kostenpflichtigen KI-Aufrufe, keine Plattform-Liveinbox, kein automatischer Versand, keine Preis- oder Statusänderung. Die ursprünglichen KI-UNKNOWN-Kosten müssen weiterhin im Anbieter-Konto unabhängig geprüft und im bestehenden Arbeitsbereich ausdrücklich geklärt werden; die Tagesliste startet sie nie erneut. Eine Plattformberechtigung oder ein bestätigter aktueller Eigentumsstatus wird daraus nicht abgeleitet.

## Bestehende Eigenanzeigen mit KI prüfen

Im Dashboard **Bestehende Eigenanzeigen mit KI prüfen** öffnen. Bestätigte eigene veröffentlichte Anzeigen entweder aus der Katalogauswahl oder über **Alle passenden** wählen; ein Auftrag umfasst seit v0.1.21 höchstens 10.000 Anzeigen. Die erfassten Originaldaten, Quellen und Anzeigen-IDs bleiben an den Auftrag gebunden.

Vor dem ersten kostenpflichtigen Vorschlag das USD-Gesamtbudget und die externe Datenübertragung ausdrücklich bestätigen. **Nächster Vorschlag** verarbeitet eine Anzeige, die seit v0.1.22 kostenbestätigte Eigenanzeigen-Stapelverarbeitung bis zu 100 Anfragen nacheinander (vorbehaltlich Budget und Sitzungslimit). Der Stapel lässt sich stoppen und später fortsetzen. Bei **UNKNOWN**, Kommunikationsfehlern oder unklarem Providerergebnis nicht blind wiederholen; mögliche Kosten zuerst klären. Jeden Original-/Vorschlagsvergleich anzeigenweise prüfen und den Vorschlag lokal **freigeben** oder **ablehnen**. Es erfolgt keine automatische Plattform-Veröffentlichung und keine sonstige Plattformänderung. Der lokale Kostenbericht in **Controlling** berücksichtigt Entwurfs- und Bestandsanzeigen-KI-Aufträge getrennt, zeigt nachgeprüfte UNKNOWN-Kosten separat von Token-Schätzungen und offenen Reservierungen und ist keine bestätigte Anbieterrechnung.

Die bisherigen KI-Stapelaufträge für lokale CSV-/XLSX-Entwürfe bleiben separat verfügbar: bis zu 5.000 lokale Entwürfe sammeln, Vorschläge prüfen, einzeln freigeben und ausschließlich lokal übernehmen.

## Frühere Programmversion wiederherstellen

Ab **v0.1.14** erscheint unter **Weitere Aktionen** die Schaltfläche **Vorherige Programmversion verwenden …**, sobald eine gültige frühere Programmumgebung vorhanden ist. Anwendung vorher beenden, die gewünschte Version auswählen, die Bestätigung lesen und den Rückwechsel ausdrücklich freigeben. Kleinanzeiger prüft die ältere Umgebung und deren lesende Kompatibilität mit der vorhandenen Datenbank vor und nach der Aktivierung; bei einem Fehlversuch wird die zuletzt aktive Programmumgebung wiederhergestellt. Der Wechsel berührt weder die Anzeigendatenbank noch Chrome-Profile oder bestehende Sicherungen. Die bisher aktive Programmversion bleibt ihrerseits als Rückfallmöglichkeit erhalten. **Dies ist kein Zurücksetzen von Daten oder unbekannten Plattformvorgängen.** Nach dem Rückwechsel startet **Kleinanzeiger starten** die gewählte Version; nur **Installieren / aktualisieren** führt erneut ein Update aus.

## Bestehende Daten und Updates

**Ab v0.1.13** wird eine neue Python-Programmumgebung vollständig getrennt aufgebaut und vor dem Wechsel geprüft. Die bisherige Version bleibt bis zum erfolgreichen Aktivieren erhalten; eine vor oder während der Aktivierung fehlschlagende Aktualisierung wird auf die bisherige Umgebung zurückgestellt. Die vorherige Programmumgebung bleibt danach als eindeutig benannter Ordner `.venv-rollback-*` im lokalen Kleinanzeiger-Verzeichnis verfügbar. Diese Ordner enthalten Programmdateien, nicht die Anzeigendatenbank. Datenbank, Sicherungen und Chrome-Profil werden weder beim Pakettest noch beim Versionswechsel überschrieben. Ab v0.1.14 ist die Auswahl einer geprüften früheren Programmversion direkt unter **Weitere Aktionen** verfügbar.

Die stabil installierten Programmdateien liegen unter
%LOCALAPPDATA%\Kleinanzeiger\App, die Datenbank unter
%LOCALAPPDATA%\Kleinanzeiger\anzeigen.sqlite. Backupdateien,
WAL/SHM, Recovery-Anfragen, Medien und das separate ChromeProfile bleiben
beim Update erhalten. Laufende Dashboard-/Write-Prozesse blockieren ein
Update, bis die bisherige Anwendung kontrolliert beendet ist.

Die technische Prüfung nutzt synthetische Plattformgegenstellen: keine
echten Anzeigenänderungen, kein unbestätigter kostenpflichtiger KI-Aufruf.
Die separate Linux-A6-Altbestandsmigration (#76) ist kein Windows-Blocker.

Das vollständig integrierte Handbuch heißt `Kleinanzeiger-Handbuch.html`, die
Funktionsübersicht `Kleinanzeiger-Funktionen.html`. Beide Dateien liegen im
Windows-Paket und sind über das zentrale Kontrollfenster erreichbar.

## Große Produktbestände als Entwürfe vorbereiten

CSV (UTF-8) oder Excel XLSX mit den Pflichtspalten `sku,title,description,category,price_eur` und optional `images`. Deutsche Spaltennamen `Artikelnummer,Titel,Beschreibung,Kategorie,Preis,Bilder` werden automatisch zugeordnet. Kategorie als `Haus & Garten > Dekoration`, Preis als ganze Euro, bis 5000 Artikel je Datei, bis zehn Bilddateinamen (Semikolon-getrennt). Die Bilddateinamen sind nur Hinweise und noch keine Plattformuploads. Ein bestehender Artikel mit derselben SKU und abweichendem Inhalt wird ohne Änderung abgewiesen. Im Dashboard den Bereich **Artikelentwürfe** öffnen, suchen, seitenweise durchsehen und einen Entwurf mit ausdrücklichem Klick in das separate Neuanzeigenformular übernehmen. Dieser Schritt **veröffentlicht keine Anzeige** und erteilt keine Eigentümerfreigabe. Für Details siehe [Artikelimport](portfolio-import.md).


## Wenn Chrome angemeldet ist, das Dashboard aber nicht startet

**Bereits ab Version 0.1.10:** Kleinanzeiger erkennt mehrere offene Tabs im eigenen
Chrome-Profil und sucht den richtigen Tab **Meine Anzeigen** statt pauschal
nur ein einziges Browserfenster vorauszusetzen. Bei mehreren solchen Tabs
verwendet es eine fest ausgewählte Tab-Identität; private Tabs und offene
Anzeigen-Editoren werden nicht geschlossen oder ungefragt umgeleitet.
Der Browser- und Lesezugriff bleibt lokal und lesend. Die Oberfläche zeigt
**Fehlerdetails anzeigen** unter der Statuszeile.



Kleinanzeiger verwendet ein eigenes Chrome-Profil unter
%LOCALAPPDATA%\Kleinanzeiger\ChromeProfile. Die Anmeldung in einem anderen
Chrome-Fenster genügt nicht. Ist **Meine Anzeigen** bereits in dem von
Kleinanzeiger geöffneten Chrome-Fenster sichtbar, **ist die Meldung kein
Beweis für eine fehlende Anmeldung**.

Die Version 0.1.12 prüft nach dem Öffnen von Chrome höchstens 12 Sekunden, ob
dessen lokale Browsersteuerung auf 127.0.0.1:9222 antwortet. Auch bei einem
Fehlschlag wird der Produktstarter für den sicheren lokalen Arbeitsmodus
aufgerufen. Datenbank- oder Recovery-Probleme werden dadurch nicht übergangen.
Bei einem echten Startfehler **Fehlerdetails anzeigen** (Details) öffnen: dort
stehen die technische Fehlermeldung und gegebenenfalls der Exitcode.

Wenn die Browsersteuerung nicht erreichbar ist, zunächst **nur die von
Kleinanzeiger gestarteten Chrome-Fenster** vollständig schließen und über
**Kleinanzeigen-Konto öffnen** neu aufrufen. Das private Standard-Chrome,
das Chrome-Profil, die Datenbank und die Backups **nicht löschen**.
Ein neu aufgerufener Browser-Tab beweist noch nicht, dass das
Chrome-Debugging verbunden ist. Der Test auf einem anderen PC ersetzt keine
Abnahme auf dem Kundenrechner.