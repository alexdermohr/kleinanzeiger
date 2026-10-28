# Kleinanzeiger – P3: Verkäufe, Reservierungen und Lager

**Entwicklungsstand 26. Oktober 2026: nicht veröffentlicht.**
Basis: P1 CSV-/XLSX-Entwürfe, P2 KI-Aufträge. Dieses Modul führt
**keine Änderungen an Kleinanzeigen** aus und behauptet keine
offiziell bestätigten Verkäufe oder Nachrichten.

## Enthalten

- Neue zentrale Dashboard-Rubrik **Verkäufe & Lager**.
  Anzeige aus den synchronisierten eigenen Anzeigen auswählen.
- Manuell verwalteter *lokaler* Status Verfügbar, Reserviert oder
  Verkauft. Ein lokaler Status verändert nie den Plattformstatus.
- Bei manuell verbuchtem Verkauf: Erlös in **ganzen Euro** und
  wahlweise bekannte Kosten. Leere Kosten bedeuten *unbekannt*,
  nicht Null. Lagerort und kurze eigene Notiz.
- Übersichten zu Reservierungen, manuellen Verkäufen, Erlösen,
  bekannten Kosten und bekanntem Überschuss. Gewinn nur
  bei Verkäufen mit vollständigen Erlös-/Kostenangaben.
  Importierte Nachrichten werden allein als bekannte Anzahl
  dargestellt, nicht als bestätigte Interessenten.
- 50er-Seiten, unabhängige exakte Einzel-ID-Abfrage mit
  aktuellem Versionszähler, atomare SQLite-Änderung mit
  erwarteter Revision und dauerhaftem Idempotenz-Eventlog.
- Keine lokale Verkaufseintragung nur auf Grundlage von
  CSV-SKUs, E-Mail-only-Anzeigen oder selbst gesetzten
  beliebigen Snapshot-Quellnamen. Zugelassen sind die drei
  bekannten privaten eigenen Bestands-Synchronisationsquellen.
- Nur vertrauenswürdiger lokaler Windows-SQLite-Start mit
  Same-Origin- und Sitzungstoken. Im separaten geschützten
  A6-Frontend gibt es keine freigeschaltete Datenbankschreibroute.

## Kundenbedienung nach späterer Freigabe

1. Kleinanzeiger normal starten und mit dem eigenen Konto
   **Mit Kleinanzeigen abgleichen** verwenden.
2. Unter **Meine Anzeigen** die relevante Katalogseite öffnen;
   danach **Verkäufe & Lager** öffnen.
3. Anzeige wählen. Kleinanzeiger liest den gespeicherten
   lokalen Verkaufsstand dieser einzelnen Anzeige neu.
   Wenn diese Prüfung scheitert, darf nicht gespeichert werden.
4. Den lokalen Status, ggf. Lagerort und Notiz ergänzen.
   Bei Verkauft Erlös und bekannte Kosten eingeben.
   In Notizen keine vertraulichen Käuferdaten ablegen.
5. **Lokalen Verkaufsstand speichern** wählen; bei
   Verkauft die Bedeutung als *manuellen Eintrag* bestätigen.
   Eine Plattformaktion erfolgt dadurch niemals.

Die zusätzliche SQLite-Tabellen für Verkaufsstand und
Änderungsereignisse werden in vorhandene SQLite-Backups
aufgenommen. Eine historische eigene Bestandsbeobachtung
ist ein Datenhinweis, **kein kryptografischer Eigentumsbeweis**.
Der Verkaufsvermerk ist kein steuerlich geprüfter Nachweis.

## Noch offen

Echte erlaubte Nachrichten-Leseschnittstelle vom eigenen
Konto, KI-Antwortentwürfe, einzeln genehmigte Nachrichten-
Sendefunktion, native Windows-Abnahme, Merge und
Kundenrelease. Alle Tests nutzen synthetische Daten; es
wurden keine echten Nachrichten gesendet, Anzeigen geändert
oder kostenpflichtigen KI-Aufrufe ausgeführt.
Der Vaterrechner fotolabor bleibt unberührt.
