# Kleinanzeiger P5 – Arbeitsvorlagen und passive Prüfhinweise

**Entwicklungsstand 26. Oktober 2026, noch nicht als Windows-Release ausgeliefert.**
Diese Funktion setzt auf die integrierte P1-/P2-/P3-/P4-
Entwicklungsbasis auf. Es gibt damit **noch keinen unkontrollierten
oder selbstständig kostenpflichtigen Autopiloten**.

## Wiederverwendbare KI-Auftragsvorlagen

Im Dashboard unter **KI-Stapelaufträge → Arbeitsvorlagen & Prüfhinweise**
können bis zu 100 selbst benannte Arbeitsanweisungen mit jeweils
höchstens 1500 Zeichen lokal in SQLite gespeichert werden.

- **Aktuelle Anweisung speichern**: den Inhalt des Auftragsfeldes
  unter einem bis zu 60 Zeichen langen Vorlagennamen hinterlegen.
  Beim Bearbeiten vorhandener Vorlagen wird die gespeicherte
  Revision erneut geprüft und eine bewusste Bestätigung verlangt.
- **Als Auftragstext übernehmen**: die gewählte Vorlage nach
  Rückfrage in das Feld des gewünschten KI-Auftrags kopieren.
  Weder Auftrag noch externe KI werden dadurch gestartet.
- **Vorlage löschen**: nur ausdrücklich mit Bestätigung.
  Ein bestätigter Vorgang wird identisch wiedererkannt.
- Groß-/Kleinschreibung allein erlaubt keine scheinbar
  unterschiedlichen Vorlagen mit gleichem Namen.
  Schreibvorgänge sind idempotent und mit optimistischer
  Revisionsprüfung gesichert. Bei fremden Änderungen wird
  nicht still überschrieben. Vorlagen verbleiben in derselben
  gesicherten SQLite-Datenbank wie Anzeigen und Entwürfe.

## Prüfhinweise ohne Unterbrechungen

Nur wenn im aufgeklappten Vorlagenbereich Daten zu bearbeiten
sind, erscheint eine unaufdringliche Zusammenfassung mit
den Anzahlen:

- KI-Vorschläge, die noch geprüft/freigegeben werden müssen.
- KI-Anfragen mit unklarem Ergebnis. Diese dürfen nicht
  blind wiederholt oder als kostenlos verbucht werden.
- Manuell als verkauft markierte Anzeigen ohne bekannte Kosten.
- Lokale Reservierungsvermerke, die älter als 14 Tage sind.

Die Zahlen sind ausschließlich **Hinweise auf lokal dokumentierte
Vorgänge**. Es werden keine Verkäuferkonten verändert,
keine Browsernavigation ausgelöst, keine Käufer angeschrieben
und keinerlei kostenpflichtige KI-Aufrufe automatisch gestartet.
Der Bereich ist bei Null Hinweisen leer statt unnötige
Warnmeldungen anzuzeigen.

## Sicherheit und Grenzen

HTTP-Leseanforderungen benötigen den lokalen Sitzungstoken,
Änderungen zusätzlich Same-Origin und den lokalen Write-Token.
Die getrennte geschützte A6-Frontend-Architektur erhält
keine ungenehmigte SQLite-Schreibfreigabe. Keine Vorlagen-
oder Regellogik darf die bestehenden KI-Budget-, Freigabe-
oder Plattform-Write-Schutzmechanismen umgehen.

Noch offen für die vollständige P5-Reife: echte periodische
Prüfaufträge, Nutzersteuerung der Ausführungszeit, kontrollierte
und ausdrücklich autorisierte Aktionen, gesicherte
Mehrkonten-Trennung, langfristige Wiederherstellungsabnahme
und alle Release-/Kunden-PC-Gates. Die synthetischen Tests
führen keine reale Anzeige oder bezahlte Anbieteraktion aus.
Der Vaterrechner fotolabor bleibt unangetastet.
