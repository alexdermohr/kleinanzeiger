# Kleinanzeiger

**Version 1.7.1 · Windows 11 (64 Bit)**

Kleinanzeiger hilft dir, eigene Anzeigen zu verwalten, Ergebnisse auszuwerten und mit KI neue Entwürfe vorzubereiten. Was veröffentlicht oder geändert wird, entscheidest du.

**Schnäppchenjäger:** Über **Weitere Aktionen → Schnäppchenjäger** kann
man selbst bereitgestellte Angebote (CSV/JSON) lokal bewerten und manuelle
Kleinanzeigen-Suchen öffnen. Unter Linux/KDE ist zusätzlich eine bewusst
aktivierte öffentliche SACD-Suche möglich; Windows nutzt weiterhin die
lokale CSV-/JSON-Auswertung.

## In drei Schritten starten

1. [Kleinanzeiger für Windows herunterladen](https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.7.1) und das ZIP **vollständig entpacken**.
2. Im entpackten Ordner **Kleinanzeiger.cmd** doppelklicken, **Installieren / aktualisieren** wählen und danach **Kleinanzeiger starten** anklicken. Für die Installation werden benötigte Komponenten gegebenenfalls über das Internet geladen.
3. **Kleinanzeigen-Konto öffnen**, im eigenen Kleinanzeiger-Chrome anmelden und gegebenenfalls bestätigen. Dann Kleinanzeiger starten und im Dashboard **Mit Kleinanzeigen abgleichen** wählen.

Im Dashboard siehst du den **letzten bestätigten Kontoabgleich mit Datum und Uhrzeit**. Wenn die Anmeldung oder Verbindung fehlt, zeigt Kleinanzeiger direkt, was zu prüfen ist.

Die Kontoanmeldung erfolgt nur im Browser. Kleinanzeiger fragt nicht nach deinem Kleinanzeigen-Passwort. Bei einem Verbindungsproblem bleiben vorhandene Daten im **lokalen Arbeitsmodus** lesbar; Plattformänderungen sind dort gesperrt.

## Das kannst du damit machen

- **Anzeigen:** Eigene Anzeigen laden, suchen, neu erstellen, Bilder hinzufügen, bearbeiten, pausieren, aktivieren und bewusst löschen.
- **Artikel & KI:** CSV-/Excel-Artikel importieren, Entwürfe und Bilder vorbereiten und nach eigener Freigabe KI-Texte und -Bilder erzeugen.
- **Auswertungen:** Aufrufe, Merker und Antworten verfolgen, Anzeigen vergleichen, lokale E-Mail-Benachrichtigungen und Wochenberichte auswerten.
- **Organisation:** Wiedervorlagen, lokale Preispläne, Verkäufe, Lagervermerke, Sicherung und Wiederherstellung.

KI benötigt einen eigenen Anbieterzugang und eine ausdrückliche Kostenfreigabe. Das optionale Live-Nachrichtenfenster benötigt einen **separaten, gegebenenfalls kostenpflichtigen Drittanbieterzugang**. Lokale E-Mail-Auswertungen sind kein Live-Postfach. Unbeaufsichtigte Massenveröffentlichungen und automatischer Nachrichtenversand sind nicht enthalten.

**Neue Versionen:** Das Kontrollfenster prüft im Hintergrund auf offizielle Updates und zeigt an, was sich geändert hat. Über **Alle Änderungen ansehen** bleiben die Versionshinweise auch nach der Installation erreichbar. Updates erfolgen nur auf deinen Klick; ohne Internet läuft Kleinanzeiger wie gewohnt.

## Hilfe

- [Benutzerhandbuch](docs/Kleinanzeiger-Handbuch.html) – Einstieg und typische Aufgaben
- [Alle Funktionen](docs/Kleinanzeiger-Funktionen.html) – vollständiger Funktionsumfang
- [Windows-Installation und Problemlösung](docs/windows-installation.md)

Bei Problemen im Kleinanzeiger-Fenster **Fehlerdetails anzeigen** oder **Diagnose kopieren** wählen. Profile, Datenbank und Backups nicht zur Fehlerbehebung löschen.

### Linux / KDE

**Linux/KDE ab 1.7.1:** Drei öffentliche SACD-Suchen wechseln alle 30 Minuten. Silberne Geräte mit Preis/Link; fehlender Farbbeleg erscheint getrennt zur Fotoprüfung, ohne Alarm. Modellmedian nur mit vier frischen Peers. Alternativ eigene EUR-Preislimits unter **Schnäppchenjäger → Monitoring** setzen: persönliche Ziele, keine Marktwerte. Dort auch Bericht und Benachrichtigungstest.
[Einrichtung der Schnäppchensuche](docs/schnaeppchenjaeger.md).

`kleinanzeiger-center` öffnet die deutsche KDE-Kontrollzentrale mit Konto- und
Offline-Start, geprüften SQLite-Backups, bestätigtem CSV/XLSX-Artikelimport,
Wochenbericht und Schnäppchenjäger. Externes `kdialog` erforderlich:
[NixOS-Installation und Desktop-Eintrag](docs/linux-kde-center.md).

Ab 1.3.0: **Schnäppchenjäger → Neues Suchprofil einrichten** legt nach Eingabe
des exakten Modells einen eigenen Suchmail-Eingang an. Optional verarbeitet ein
systemd-User-Path-Watch neue lokale EMLs automatisch; **Monitoring** bietet Status,
Start und Stop. Dieses separate **EML-Profil** fragt den Marktplatz nicht ab:
Suchauftrag und atomare EML-Zustellung richtet der Nutzer selbst ein. Echte
Mailtemplates sind noch nicht verifiziert. [Einrichtung und Grenzen](docs/schnaeppchenjaeger.md#linuxkde-automatische-lokale-verarbeitung-ab-130).

Ab **1.4.0:** **Silberne SACD-Player beobachten** per EML; nur explizit
silberne SACD-Disc-Player in Deutschland; Modellpreisbewertung nur mit vier frischen Peers.
`kleinanzeiger-radar create-silver-sacd` erstellt ein inaktives Profil.
[Filter und Einrichtung](docs/schnaeppchenjaeger.md#silberne-sacd-player-ab-140).