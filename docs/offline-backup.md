# Lokales SQLite-Backup von Mark (ohne Plattformzugriff)

Dieser opt-in-Befehl ist ausschließlich eine **lokale Sicherung** einer bereits initialisierten Mark-SQLite-Datei. Er verwendet weder Browser/CDP noch Netzwerk- oder Plattformzugriffe. Er veröffentlicht, verändert oder quittiert keine Anzeige. Er ist **keine Freigabe der A6-Host-Recovery und kein Beleg für Kleinanzeigen-Automatisierungsrechte**.

## Verwendung

1. Mark und andere Programme, die dieselbe SQLite-Datei verwenden, regulär beenden. Prüfen, dass keine externe Schreib- oder Recovery-Operation mehr läuft. Bei unklarem Plattform-Write nicht als erledigt behandeln.
2. Ein vorhandenes, privat kontrolliertes Sicherungsverzeichnis wählen. Das Ziel darf **noch nicht existieren**.
3. Im installierten Python-3.12+-Paket einmalig ausführen:

```sh
mark-api-backup --db "$HOME/mark.sqlite" --backup "$HOME/mark-sicherung.sqlite"
```

Bei Erfolg liefert die CLI eine JSON-Quittung mit SHA-256 des Sicherungsinhalts und Zählungen von Anzeigen-, Nachrichten-, Sync- und offenen Write-Journalen. Sie enthält keine Nachrichtentexte oder Zugangsdaten. Die Sicherungsdatei wird schreibgeschützt erzeugt (Modus `0400`); sie enthält dennoch personenbezogene Daten und muss vertraulich aufbewahrt werden.

Die vorhandene Implementierung aus [PR #75](https://github.com/alexdermohr/mark-api/pull/75) führt eine SQLite-Online-Backup-Operation mit WAL-Unterstützung aus, prüft die Datenbank, Recovery-Tabellen und Dateibindungen und veröffentlicht die Sicherung nur an einem noch nicht bestehenden Ziel. Quelle und Ziel dürfen keine Symlink-Umleitung aufweisen. Ein fehlerhafter oder mehrdeutiger Abschluss berechtigt nicht zum automatischen Wiederholen.

## Fehler und Wiederherstellung

- Bei einem Fehler prüfen, ob der Zielpfad inzwischen existiert. Eine unklare Publikation niemals durch blindes Überschreiben oder denselben Aufruf mit Nebenwirkungen „reparieren“.
- Eine Sicherung **nie über die aktuell verwendete Datenbank kopieren**. Zur manuellen Prüfung nur eine neue private Datei anlegen, deren Inhalt mit dem ausgegebenen SHA-256 vergleichen und diese separate Kopie auf SQLite-Integrität prüfen. Schreibt man in eine Kopie, muss sie entsprechend beschreibbar gemacht werden.
- Bereits vorhandene `in_progress`- oder unklare Write-/Sync-Journale bleiben Teil der Sicherung. Der Backupbefehl liefert keine externe Wiederholungs-, Quittierungs-, Lösch- oder Freigabeautorität.

## Sicherheitsgrenze

Die Backup-CLI kann einen **kompromittierten, gleichzeitig schreibenden Prozess unter derselben UID** oder schon manipulierte `MAP_SHARED`-Speicherseiten nicht unabhängig ausschließen. Eine zuvor verfälschte, intern konsistente Quelle kann auch mit korrekten Datei-/WAL-Prüfungen eine inhaltlich falsche Sicherung ergeben. Deshalb ist die eigenständige Host-/Witness-/FD-/Mount-/Backup-Paar-Recovery aus [Issue #76](https://github.com/alexdermohr/mark-api/issues/76) weiter **NO-GO**. Keine produktive Plattform-Restore-Aktion daraus ableiten.

Weitere Funktionen des lokalen Produkts: [Offline-Kurzanleitung](offline-quickstart.md).
