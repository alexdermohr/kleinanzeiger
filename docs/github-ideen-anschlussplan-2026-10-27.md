# Kleinanzeiger: Anschlussplan aus GitHub-Projektvergleich

Stand: **27. Oktober 2026**. Zweck: überprüfbare Ideen aus anderen
Kleinanzeigen-Projekten in die bestehende
[KI-Portfolio-Roadmap](roadmap-ki-portfolio-2026-10-26.md)
integrieren, **ohne** parallele Architektur oder redundante Umsetzung.
Dies ist ein **Plan**, keine Behauptung bereits ausgelieferter Features.

## 1. Gesicherter Ausgangspunkt und Abgrenzung

- **`main`**: veröffentlichter P0-Stand `v0.1.6`, Commit
  `8c6c163b320dc3a3f3f66dd83224ec8bd094c809`.
- **Gestapelte, am 27.10.2026 offene Feature-PRs:** [#219](https://github.com/alexdermohr/kleinanzeiger/pull/219)
  (P1 CSV/XLSX-Entwürfe),
  [#220](https://github.com/alexdermohr/kleinanzeiger/pull/220)
  (P2 persistente *lokale* KI-Aufträge),
  [#221](https://github.com/alexdermohr/kleinanzeiger/pull/221)
  (P3 lokaler Verkauf/Lager),
  [#222](https://github.com/alexdermohr/kleinanzeiger/pull/222)
  (P4 Controlling),
  [#223](https://github.com/alexdermohr/kleinanzeiger/pull/223)
  (P5 KI-Arbeitsvorlagen),
  [#224](https://github.com/alexdermohr/kleinanzeiger/pull/224)
  (Windows-Integrationskandidat),
  [#225](https://github.com/alexdermohr/kleinanzeiger/pull/225)
  (manuelle Käuferantwort- und KI-Entwürfe),
  [#226](https://github.com/alexdermohr/kleinanzeiger/pull/226)
  (10.000er-Windows-Eigenbestandsleser) und
  [#227](https://github.com/alexdermohr/kleinanzeiger/pull/227)
  (Anzeige importierter EML-Ereignisse). Der PR-Stand ist eine
  **Momentaufnahme**, kein CI-, Merge- oder Release-Nachweis.
- **Bestehend statt neu bauen:** Python/SQLite, Chrome-CDP,
  Dashboard-Katalog/Mehrfachauswahl, lokale Datenhistorie,
  vorhandene Browser-Writes, P1-Import, P2-KI-Aufträge, P3-Verkauf,
  P4-Controlling und P5-KI-Arbeitsvorlagen wiederverwenden.
  Lokale KI-Aufträge sind **keine** sichere Plattform-Massenwrite-Queue.
- **Zielbild:** ein zentrales Windows-Produkt für 100 bis 10.000
  eigene Anzeigen mit klarer Vorschau, Fortschritt, überprüfbaren
  Änderungen und bedienbaren Fehlerzuständen.
- Der Rechner `fotolabor` ist **nicht** der Kundenrechner und bleibt
  unverändert. Keine stillen Plattformaktionen oder Kosten.

## 2. Konkrete Maßnahmen in Nutzenreihenfolge

Die Kennungen G1–G7 sind **neue Ergänzungen** zu P0–P5, nicht
zusätzliche parallele Phasen. Der jeweils kleinste nachweisbar
funktionierende Ausschnitt ist zuerst zu bauen.

| ID / Priorität | Kundenfunktion und Umsetzung | Abhängigkeit | Nachweisbare Akzeptanz |
| --- | --- | --- | --- |
| **G1 / hoch** | **Ablauf- und Verlängerungsradar:** eigene Anzeigen nach geprüftem Ablaufdatum und Handlungsbedarf gruppieren, Hinweise in vorhandener Katalogansicht; zunächst **nur lesend**, keine automatische Verlängerung. | Vollständiger und vertrauenswürdiger Own-Inventory-Sync; #226 nur nach Integration | Angezeigtes Datum stammt aus zugeordneter Plattformbeobachtung; unbekanntes Datum bleibt **unbekannt**. Zeitzonen/Grenztage, Pausen, Filter und 10.000 Datensätze synthetisch getestet. Keine pauschale 60-Tage-Berechnung als vermeintliche Plattformtatsache. |
| **G2 / hoch** | **Artikelvorlagen und Duplizieren:** häufige Artikelmerkmale, Kategorie, Versand- und Textbausteine als wiederverwendbare lokale Vorlagen; „aus bestehender Anzeige Entwurf erstellen“. **Nicht** mit den KI-*Arbeitsanweisungen* in #223 verwechseln. | P1-Entwürfe #219, bestehende Create-UI | Neuer Entwurf erhält eigene lokale Kennung und keine übernommene Plattform-ID/Schreibberechtigung; Änderungen an Vorlage verändern bestehende Anzeigen nicht; Vorschau/Undo bzw. Verwerfen möglich. |
| **G3 / hoch** | **Fotozuordnung bei CSV/XLSX:** optionale Bildspalten mit vom Anwender gewähltem Bildordner/SKU verbinden, Vorschau, Reihenfolge und Konfliktanzeige; vorhandenen P1-Import erweitern statt zweiten Importer bauen. | P1 #219, bestehender Medienpfad | Test mit 1.000 Artikeln und typischen Mehrfachfotos; keine Pfadflucht/Symlink-Nachverfolgung, Dateityp und -inhalt geprüft, fehlende/mehrdeutige Bilder einzeln sichtbar; Import erzeugt nur lokale Entwürfe, keinen Upload/Publish. |
| **G4 / mittel** | **Bestandsabgleich als Differenzansicht:** zuletzt bestätigter Plattformstand vs. lokale Fassung; „neu/geändert/reserviert/ungeklärt“ getrennt darstellen, keine stillen Auto-Löschungen und keine automatische Zuordnung allein nach gleichem Titel. | #226, vorhandene Snapshot-Historie und Reconciliation | Fehlgeschlagene/unvollständige Synchronisation erzeugt niemals „gelöscht/verwaist“ als gesicherte Tatsache; ID/Quelle/Zeitpunkte nachvollziehbar; 10.000er-Seiten, Duplikate und Pagination-Drift getestet. |
| **G5 / mittel** | **Preisvorschläge und einfache Preisregeln:** pro Anzeige Mindestpreis, optional Prozent- oder Festbetragsschritte und Wartezeit; nachvollziehbare Preisberechnung, Ziel/Verkaufsdaten getrennt von Schätzung. Erst Vorschlag/Sammelvorschau, **keine** automatische Preissenkung. | P3 #221, P4 #222, vorhandene Content-Write-API | Jeder Schritt mit Ausgangspreis, Mindestpreis, Rundung und Grund sichtbar; nie unter Mindestpreis; fehlende Einstandskosten als unbekannt markieren; Grenzwerte/Mehrfachauswahl getestet. Preisänderungen nur mit tatsächlicher Einzel-/Stapelautorität. |
| **G6 / später** | **Kontrollierte Plattform-Massenaktionen:** ausgewählte eigene Anzeigen nacheinander ändern, pausieren, aktivieren oder nach ausdrücklicher Freigabe veröffentlichen/löschen; bestehende Einzel-Write-Semantik benutzen, keine zweite Plattform-API. | P2 #220, vollständige Owner-Sitzung, G4, bestehende CSRF/Idempotenz/Recovery | Trockenlauf und Sammelvorschau mit Anzahl/IDs/Alt-/Neuwerten; stabile ID statt veralteter DOM-Referenz; ein Writer pro Sitzung, Einzelresultate pro ID, Pause/Abbruch; nach Timeout/UNKNOWN **kein Blind-Retry**; Neustart und doppelte Auslösung synthetisch geprüft. |
| **G7 / später** | **Vollständiger autorisierter Posteingang:** Konversationen für eigene Anzeigen abrufen, priorisieren und Antworten als Entwurf mit Einzelfreigabe senden. Bestehende #225-Entwurfsoberfläche und #227-EML-Übersicht weiterverwenden. | Tatsächlich zulässige authentifizierte Nachrichten-Leseschnittstelle und getrennte Schreibberechtigung | Konto-/Anzeigen-/Konversationsbindung nachvollziehbar; keine Käuferidentitäten oder Texte aus fremder Sitzung; EML allein ist kein Owner-Nachweis; keine unbestätigte Nachricht als versendet ausgeben; MFA/CAPTCHA nur manuell. |

**Gestalterischer Querschnitt:** G1–G7 müssen im vorhandenen Windows-Fenster
auffindbar sein, mit kompakten Statusgruppen, eindeutigen Handlungsflächen
und gut sichtbaren Ergebnis-/Fehlerzuständen. Karten-/Tabellenansicht und
Auftragsfortschritt nur ergänzen, soweit sie die tägliche Bedienung wirklich
verbessern; kein Wechsel zu Next.js, Docker oder Telegram als Voraussetzung.

## 3. Ausführungsreihenfolge und Abnahmeregeln

1. **Zuerst bestehende Lieferkette abschließen:** #219 bis #227 in ihrer
   tatsächlichen Abhängigkeitsreihenfolge nach frischer CI/Review auf
   exaktem PR-HEAD integrieren; echten Windows-Installations-/Wheel-Kandidaten
   einschließlich Handbuch prüfen. Der Anschlussplan erzeugt **keine neue
   Sperre** für einen bereits anderweitig abnahmefähigen Release.
2. **Kleine Nutzenpakete:** G1, G2 und G3 jeweils als möglichst kleine PRs,
   nur dort neue Datenspeicherung, wo vorhandene Tabellen nicht genügen.
   Benutzerseitig keine neue Konfigurationsebene verlangen.
3. **Bestandsqualität und Preissteuerung:** G4 und G5 auf validierter
   Datenbasis; offline und ohne Plattform-Writes demonstrierbar.
4. **Plattformwirkungen/Kommunikation:** G6 und G7 erst nach
   Rechteschnittstelle, konkreter Nutzerregel, Wiederanlauf- und
   UNKNOWN-Prüfung. Nicht von vorhandenen rein lokalen Jobtests auf sichere
   reale Bulk-Writes schließen.
5. **Optional erst nach messbarem Bedarf:** natürlichsprachige Änderungen
   an bestehenden Entwürfen, zusätzliche KI-Vision-Provider,
   umfassendere Kategorie-/Attributvorschläge und Wettbewerbs-/
   Marktpreissuche. Diese Punkte liefern **keinen** Vorab-Grund für
   einen neuen Agentenserver, einen Messenger-Bot oder Scraping.

**Für jeden Funktions-PR:** vorhandene Produkt-/Sicherheitsregressionen,
passende synthetische Negativ- und Lasttests, eindeutige
Quelldaten-/Kosten-/Aktionsnachweise, aktualisierte Kundenhilfe,
native Windows-Prüfung auf dem exakt geänderten Lieferstand.
Status ausdrücklich unterscheiden: geplant → implementiert →
getestet → integriert → veröffentlicht → am echten Kunden-PC abgenommen.
Die 10.000 synthetischen Fälle allein bestätigen weder den realen
Kundenbestand noch produktive Plattformberechtigungen.

## 4. Lizenz, Plattformbedingungen und bewusst nicht übernommene Technik

- **Keine Codekopie in diesem Plan.** Die zentralen Projekte
  `Second-Hand-Friends/kleinanzeigen-bot` und
  `bkd3sign/kleinanzeigen-bot-ui` sind mit **AGPL-3.0** versehen.
  Vor jeder direkten Übernahme Lizenzkompatibilität und gegebenenfalls
  Quellenoffenlegungspflichten prüfen; zunächst Konzepte mit eigenem Code
  umsetzen.
- Externe Kleinanzeigen-Oberflächen und Schnittstellen sind veränderlich.
  Automatisierte Zugriffe/Writes müssen vor produktiver Nutzung anhand
  aktueller Plattformbedingungen und tatsächlicher Kontoberechtigung
  bewertet werden. Keine CAPTCHA-/MFA-Umgehung und kein heimliches
  Sammeln fremder Anzeigen als Voraussetzung.
- **Nicht übernehmen:** zweite Chromium-Automation neben unserem
  bestehenden CDP-Pfad, externe Telegram-Pflicht, zweite Queue, neues
  Frontend-Framework oder auto-republish per Löschen/Neuanlegen ohne
  explizite Folgenprüfung. Fremde feste Ablauf- und Kategorienzahlen
  sind Hinweise, keine garantierten Plattformverträge.

## 5. Geprüfte Inspirationsquellen (27.10.2026)

| Repository/Quellcode | Übertragbarer Ansatz |
| --- | --- |
| [Second-Hand-Friends/kleinanzeigen-bot](https://github.com/Second-Hand-Friends/kleinanzeigen-bot) / [price_reduction.py](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/price_reduction.py) | Getrennte Preisentscheidungen mit nachvollziehbaren Schritten |
| [bulk_ad_actions.py](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/bulk_ad_actions.py) / [extend_flow.py](https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/extend_flow.py) | ID-stabiles Stapelverfahren; datumsgesteuerte Verlängerungsprüfung |
| [bkd3sign/kleinanzeigen-bot-ui](https://github.com/bkd3sign/kleinanzeigen-bot-ui) / [status.ts](https://github.com/bkd3sign/kleinanzeigen-bot-ui/blob/main/src/lib/ads/status.ts) | Status-, Ablauf- und Aktionsübersicht im Dashboard |
| [queue.ts](https://github.com/bkd3sign/kleinanzeigen-bot-ui/blob/main/src/lib/bot/queue.ts) / [templates.ts](https://github.com/bkd3sign/kleinanzeigen-bot-ui/blob/main/src/lib/yaml/templates.ts) | Sichtbare Auftragsreihenfolge; wiederverwendbare Anzeigendaten |
| [albert0815/kleinanzeiger](https://github.com/albert0815/kleinanzeiger) | Fotos zu Entwurf, natürlichsprachige Nachbearbeitung, persistente Jobs; kein Telegram-Zwang |
| [trichling/Kleinanzeiger](https://github.com/trichling/Kleinanzeiger) | Bildanalyse und Anbietertrennung als optionale spätere Ideen; Dokumentation und aktueller Code nicht durchgängig identisch |
| [jnslmk/kleinanzeigen-mcp](https://github.com/jnslmk/kleinanzeigen-mcp) | Such-/Vergleichsidee **nur** bei rechtlich und technisch zulässiger Quelle; keine automatische Integration |

Diese Links sind **Recherchebelege**, keine Freigaben zum Kopieren,
kein Beweis eines stabilen API-Vertrags und keine implizite
Lieferverpflichtung für spätere optionale Funktionen.
