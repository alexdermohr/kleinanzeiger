# Kleinanzeiger

**Version 1.5.0 · Windows 11 (64 Bit)**

Kleinanzeiger hilft dir, eigene Anzeigen zu verwalten, Ergebnisse auszuwerten und mit KI neue Entwürfe vorzubereiten. Was veröffentlicht oder geändert wird, entscheidest du.

**Schnäppchenjäger:** Über **Weitere Aktionen → Schnäppchenjäger** kann
man selbst bereitgestellte Angebote (CSV/JSON) lokal bewerten und manuelle
Kleinanzeigen-Suchen öffnen. Unter Linux/KDE ist zusätzlich eine bewusst
aktivierte öffentliche SACD-Suche möglich; Windows nutzt weiterhin die
lokale CSV-/JSON-Auswertung.

## In drei Schritten starten

1. [Kleinanzeiger für Windows herunterladen](https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.5.0) und das ZIP **vollständig entpacken**.
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

**Neu ab 1.5.0:** Im Schnäppchenjäger die Aktion
**Silberne SACD-Player direkt suchen (automatisch)** auswählen.
Ein separates Profil kann nach Bestätigung die öffentliche Suche höchstens
alle 30 Minuten prüfen. Kein E-Mail-Schalter, kein Kleinanzeigen-Login,
kein fremder API-Schlüssel erforderlich. Erster Abruf = Bestandsaufnahme;
später nur neue Inserate und Preisänderungen melden. Ergebnisse und
Beobachtungsunsicherheiten stehen im lokalen Bericht. Bei HTTP-Sperre oder
geändertem HTML bleiben alte Daten erhalten. Die Nutzungsbedingungen
verlangen für automatisierte Abrufe schriftliche Zustimmung: Der Betreiber
muss die Berechtigung selbst sicherstellen. Die EML-Überwachung bleibt getrennt.
Siehe Dokumentation zum Schnäppchenjäger.

`kleinanzeiger-center` öffnet die deutsche KDE-Kontrollzentrale mit Konto- und
Offline-Start, geprüften SQLite-Backups, bestätigtem CSV/XLSX-Artikelimport,
Wochenbericht und Schnäppchenjäger. Externes `kdialog` erforderlich:
[NixOS-Installation und Desktop-Eintrag](docs/linux-kde-center.md).

Ab 1.3.0: **Schnäppchenjäger → Neues Suchprofil einrichten** legt nach Eingabe
des exakten Modells einen eigenen Suchmail-Eingang an. Optional verarbeitet ein
systemd-User-Path-Watch neue lokale EMLs automatisch; **Monitoring** bietet Status,
Start und Stop. Keine automatische Marktabfrage oder Mailkonto-Anmeldung:
Suchauftrag und atomare EML-Zustellung richtet der Nutzer selbst ein. Echte
Mailtemplates sind noch nicht verifiziert. [Einrichtung und Grenzen](docs/schnaeppchenjaeger.md#linuxkde-automatische-lokale-verarbeitung-ab-130).

Ab **1.4.0** ergänzt **Silberne SACD-Player beobachten** genau eine Kategorie:
echte SACD-Disc-Player in Silber, Deutschland, ohne Preislimit und ohne Markenwahl.
Nur explizite SACD-Player-/Silberangaben im Mailtitel werden berücksichtigt;
ND8006, Defektangebote, Zubehör und unklare Farbe werden ignoriert. Keine
Modellpreisvergleiche: neue Geräte und tatsächliche Preisänderungen erscheinen
einmalig als **nicht bewertbar**, `hits=0`. Foto und Funktion selbst prüfen.
CLI: `kleinanzeiger-radar create-silver-sacd`. Erstellung ist idempotent und
aktiviert keinen Dienst. [Suchadresse, Einrichtung und Grenzen](docs/schnaeppchenjaeger.md#silberne-sacd-player-ab-140).
