# Kleinanzeiger 1.0.15 unter NixOS/KDE

Die Kontrollzentrale nutzt das externe KDE-Programm `kdialog`. Sie benötigt
keine Python-GUI-Bibliothek. Python ab 3.12 und für den Kontomodus Google Chrome
müssen verfügbar sein. Der Kontostart verwendet unverändert die vorhandene
Linux-Startlogik und deren persistente app-eigene Profilauswahl.

## Installation im Benutzerkonto

Die KDE-Kontrollzentrale benötigt das externe Programm kdialog. Der
NixOS-taugliche Installationsweg mit uv verwendet ein isoliertes Python und
belässt bisherige Chrome-Profile, lokale Datenbank und ältere Versionen
unverändert. Aus diesem geprüften Quell-Checkout:

~~~bash
nix profile add nixpkgs#kdePackages.kdialog
export UV_TOOL_DIR="$HOME/.local/share/kleinanzeiger/releases/v1.0.14-kde-nixos/tools"
export UV_TOOL_BIN_DIR="$HOME/.local/share/kleinanzeiger/releases/v1.0.14-kde-nixos/bin"
nix shell nixpkgs#uv --command uv tool install --python 3.13 --python-preference only-managed '.[private-web]'
~~~

Das Extra installiert unter anderem openpyxl für Excel. Optional lassen
sich KDE-Dialoge später deklarativ via Home Manager oder NixOS bereitstellen.
Die Desktop-Vorlage steht unter docs/kleinanzeiger-center.desktop.
Desktopdateien expandieren weder HOME-Variablen noch Tilde: absolute
Programmaufrufe sind nötig. Beispiel:

~~~bash
mkdir -p "$HOME/.local/share/applications"
desktop="$HOME/.local/share/applications/kleinanzeiger.desktop"
test ! -e "$desktop" || cp -n "$desktop" "$desktop.vor-kontrollzentrale"
sed -e "s|^Exec=.*|Exec=$UV_TOOL_BIN_DIR/kleinanzeiger-center|"     -e "s|^TryExec=.*|TryExec=$UV_TOOL_BIN_DIR/kleinanzeiger-center|"     docs/kleinanzeiger-center.desktop > "$desktop"
desktop-file-validate "$desktop"
~~~

Start im Terminal: "$UV_TOOL_BIN_DIR/kleinanzeiger-center".
Kein Autostart und keine automatische Registrierung kostenpflichtiger
Anbieter. Das lokale Menü ist unabhängig vom Windows-Installer.

## Bedienung und Grenzen

- **Konto / Dashboard** und **Offline** starten denselben bestehenden Linux-Client.
  Die Zentrale bleibt geöffnet. Zum Moduswechsel zuerst **Client beenden** wählen.
  Abbrechen/Schließen der Zentrale beendet ihren Client samt eigener Prozessgruppe;
  ein bereits vorher laufender Chrome wird nicht beendet.
  Offline aktiviert den sitzungsgebundenen lokalen Arbeitsbereich fuer
  Entwuerfe und Verkaufsvermerke, ohne Plattformaktionen. Ein neu gestarteter
  App-Chrome gehört zur Client-Prozessgruppe und wird mit beendet.
- Ein Lock verhindert mehrere Zentralen desselben Benutzers. Zusätzlich verhindert
  die Zentrale einen zweiten eigenen Client. Auch ältere Linux-Launcher
  derselben Benutzerkennung werden vor einem weiteren Konto-/Offline-Start
  erkannt. Eine aktive ältere Sitzung und ihr Chrome-Profil werden nicht
  beendet; erst nach deren normaler Beendigung die neue Sitzung starten.
  So werden parallele Frontends auf derselben SQLite-DB vermieden.
- Die Datenbank liegt wie bisher unter
  `~/.local/share/kleinanzeiger/data/portfolio.sqlite3`. Nur ein ausdrücklicher
  Clientstart kann sie initialisieren. Die anderen Datenbankaktionen verlangen
  eine vorhandene Datenbank und lehnen verknüpfte Pfade ab.
- **Backup** verwendet `mark_api.backup_cli` mit SQLite-Konsistenzprüfung und
  Überschreibschutz. Eine neue Datei in einem vorhandenen Ordner wählen.
  Eine Restore-Schaltfläche gibt es nicht.
- **Artikelimport** zeigt die Backend-Vorschau mit Spaltenzuordnung, Anzahlen,
  Konflikten, Beispieldatensätzen und SHA-256 in einem Klartextfenster. Erst danach
  folgt die ausdrückliche Ja/Nein-Bestätigung. Der Backend-Import prüft denselben
  Hash erneut; geänderte Dateien und Konflikte werden abgewiesen. Es entstehen
  ausschließlich lokale Artikelentwürfe. Vorlagen: [Artikelimport](portfolio-import.md).
- **Wochenbericht** schreibt HTML in den gewählten Ordner. Ein Bericht desselben
  UTC-Tages wird durch die bestehende Berichtsfunktion ersetzt. Es handelt sich
  um lokale Daten, nicht um einen vollständigen Plattform-Posteingang.
- **Schnäppchenjäger** wertet eigene CSV/JSON aus oder öffnet eine bewusst
  angeforderte manuelle Suche im Standardbrowser. Es gibt kein Scraping und keine
  bestätigten Verkaufspreise. Format: [Schnäppchenjäger](schnaeppchenjaeger.md).
- Diagnose zeigt nur Paketversion, Dialogverfügbarkeit, Client-Prozessstatus und
  ein festes Aktionsergebnis. Keine Pfade, Umgebungsvariablen, Tokens, Rohfehler
  oder Backend-Ausgaben werden übernommen. Importvorschauen enthalten dagegen
  bewusst die eigenen ausgewählten Artikeldaten.
- Ein angeforderter Start ist noch kein Bereitschaftsnachweis. Bei Problemen
  Installation/Anmeldung prüfen; der Prozessstatus steht unter **Diagnose**.
- Die Zentrale belässt die bestehende KI-Startlogik unverändert.
  Keine zusätzlichen Plattform-Schreibaktionen, Kostenaktionen, Credential-Verwaltung,
  automatischen Updates oder E-Mail-Importdialoge. Updates über die eigene
  Paketverwaltung. Handbuch und Hinweise zu dieser Änderung sind eingebaut und
  damit auch im installierten Wheel verfügbar.

## Tests ohne Nutzerdaten

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_linux_center.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_linux_launcher.py' -v
```

Dialoge und Browserstarts werden gemockt. Backend-Integrationstests arbeiten
nur mit temporären synthetischen SQLite-Datenbanken. Ein visueller KDE-Test
ist zusätzlich in einer echten grafischen Sitzung erforderlich.
