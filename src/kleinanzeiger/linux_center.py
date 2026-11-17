"""KDE control center. kdialog is an external executable, never a Python GUI dependency."""
from __future__ import annotations

import fcntl
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager

from . import linux_launcher


@contextmanager
def instance_lock(directory: Path):
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise OSError("invalid state directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (any(p.is_symlink() for p in (directory, *directory.parents))
            or directory.stat().st_uid != os.getuid()):
        raise OSError("invalid state directory")
    fd = os.open(directory / "center.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise OSError("invalid lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


class Center:
    def __init__(self, kdialog: str, db: Path = linux_launcher._DB):
        self.kdialog = kdialog
        self.db = db
        self.client = None
        self.last_status = "Bereit"

    def dialog(self, *args: str) -> str | None:
        result = subprocess.run(
            [self.kdialog, "--title", "Kleinanzeiger · Kontrollzentrale", *args],
            shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        if result.returncode == 1:  # Cancel / No / window close
            return None
        raise OSError("dialog failed")

    def message(self, text: str):
        self.dialog("--msgbox", text)

    def text(self, text: str):
        # Plain text viewer: imported HTML-like strings must never become KDE rich text.
        with tempfile.TemporaryDirectory(prefix="kleinanzeiger-center-") as tmp:
            path = Path(tmp) / "ansicht.txt"
            path.write_text(text, encoding="utf-8")
            self.dialog("--textbox", str(path), "900", "650")

    def backend(self, module: str, *args: str) -> str:
        env = os.environ.copy()
        env.pop("KLEINANZEIGER_IMPORT_REQUEST", None)
        result = subprocess.run(
            [sys.executable, "-m", module, *args], shell=False,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
            env=env,
        )
        if result.returncode:
            raise ValueError("backend failed")
        return result.stdout

    def existing_db(self):
        if any(p.is_symlink() for p in (self.db, *self.db.parents)) or not self.db.is_file():
            raise ValueError("database unavailable")

    def stop(self):
        if self.client is None:
            return
        # Dedicated session owns launcher and its descendants, including a newly
        # started app browser. An already running Chrome belongs to another group.
        group = self.client.pid
        try:
            try:
                os.killpg(group, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                self.client.wait(timeout=18)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        finally:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.client.wait()
            self.client = None

    def start(self, mode: str):
        if self.client is not None:
            if self.client.poll() is None:
                self.message("Ein Client läuft bereits. Zum Wechsel zuerst „Client beenden“ wählen.")
                return
            self.stop()
        if linux_launcher._other_kleinanzeiger_running():
            self.last_status = "Andere Sitzung läuft"
            self.message("Eine andere Kleinanzeiger-Sitzung ist noch geöffnet. "
                         "Bitte diese zuerst beenden; die Anmeldung bleibt erhalten.")
            return False
        self.client = subprocess.Popen(
            [sys.executable, "-m", "kleinanzeiger.linux_launcher", mode],
            shell=False, start_new_session=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.message("Start angefordert. Das Dashboard öffnet sich im Browser.\n"
                     "Die Kontrollzentrale muss geöffnet bleiben; Beenden stoppt den Client.")

    def articles(self):
        self.existing_db()
        source = self.dialog("--getopenfilename", str(Path.home()), "*.csv *.xlsx | Artikeldateien")
        if not source:
            return
        preview = json.loads(self.backend("mark_api.portfolio_import_cli", "--db", str(self.db),
                                         "--mode", "preview", "--file", source))
        digest = preview.get("sha256")
        self.text(json.dumps(preview, ensure_ascii=False, indent=2))
        if (preview.get("status") != "preview" or preview.get("can_import") is not True
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            self.message("Die Vorschau erlaubt keinen Import. Datei und Konflikte prüfen.")
            return
        if self.dialog("--yesno", "Die angezeigte Vorschau als lokale Artikelentwürfe importieren?\n"
                       "Die Datei wird beim Import erneut gegen den Vorschau-Hash geprüft.") is None:
            return
        result = json.loads(self.backend("mark_api.portfolio_import_cli", "--db", str(self.db),
                           "--mode", "import", "--file", source, "--expected-sha256", digest))
        if result.get("status") != "imported":
            raise ValueError("import rejected")
        self.message("Artikelentwürfe lokal importiert.")

    def backup(self):
        self.existing_db()
        target = self.dialog("--getsavefilename", str(Path.home() / "Kleinanzeiger-Backup.sqlite3"),
                             "*.sqlite3 | SQLite-Backup (neue Datei)")
        if target:
            self.backend("mark_api.backup_cli", "--db", str(self.db), "--backup", target)
            self.message("SQLite-Backup erstellt und durch die Backup-CLI geprüft.")

    def weekly(self):
        self.existing_db()
        target = self.dialog("--getexistingdirectory", str(Path.home()))
        if target:
            self.backend("mark_api.weekly_report", "--db", str(self.db), "--output-dir", target)
            self.message("Wochenbericht im gewählten Ordner gespeichert.\n"
                         "Grundlage sind ausschließlich vorhandene lokale Daten.")

    def radar_monitoring(self, choice):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch()
        try:
            if choice == "new":
                model = self.dialog("--inputbox", "Exakte Modellbezeichnung (keine allgemeine Produktkategorie):")
                if model is None:
                    return
                profile = manager.create(model)
                self.text(f"Profil: {profile}\nEingangsordner: {profile.parent / 'suchmails'}\n\n"
                          "Kleinanzeigen-Suche auf Website als Suchauftrag speichern und eigene EMLs "
                          "atomar in diesen Ordner zustellen.\n"
                          "Keine Mailkonto-Anmeldung oder automatische Marktabfrage. "
                          "Echte Mailtemplates sind noch nicht verifiziert.\n"
                          "Berichte und Historie entstehen bei der ersten Auswertung im Profilordner.")
                if self.dialog("--yesno", "Lokales Monitoring für dieses Profil jetzt aktivieren?\n"
                               "Verarbeitet künftig nur EML-Dateiänderungen im angezeigten Eingang.\n"
                               "Stoppen ist unter Monitoring möglich.") is None:
                    return
                self.message(manager.watch(profile, "start"))
            else:
                selected = self.dialog("--getopenfilename", str(manager.data), "*.json | Eingerichtetes Radar-Profil")
                if not selected:
                    return
                profile = Path(selected).absolute()
                status = manager.watch(profile, "status")
                action = self.dialog("--menu", status, "status", "Status anzeigen", "start", "Monitoring starten",
                                     "stop", "Monitoring stoppen")
                if action in ("start", "stop", "status"):
                    self.message(manager.watch(profile, action))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Plain text includes useful validation/systemd guidance without rich-text interpretation.
            self.text(f"Radar konnte die Aktion nicht abschließen: {exc}")

    def bargains(self):
        choice = self.dialog("--menu", "Schnäppchenjäger: beobachtete Angebotspreise",
                             "file", "Lokale CSV/JSON auswerten", "search", "Manuell im Browser suchen",
                             "radar", "Suchmail-/Datei-Radar aus Profil auswerten",
                             "new", "Neues Suchprofil einrichten",
                             "watch", "Monitoring: Status / Starten / Stoppen")
        if choice in ("new", "watch"):
            self.radar_monitoring(choice)
            return
        if choice not in ("file", "search", "radar"):
            return
        if choice == "radar":
            profile = self.dialog("--getopenfilename", str(Path.home()), "*.json | Radar-Profile")
            if not profile:
                return
            result = json.loads(self.backend("mark_api.bargain_hunter_cli", "--radar", profile))
            counts = (result.get("new"), result.get("price_changes"), result.get("hits"))
            if any(type(n) is not int or n < 0 for n in counts):
                raise ValueError("invalid radar summary")
            self.message(f"Radar geprüft: {counts[0]} neue Inserate, {counts[1]} Preisänderungen, "
                         f"{counts[2]} auffällig günstige Treffer. Bericht und Details: im Radar-Profil. "
                         "Nur lokale Eingaben, keine Marktplatzabfrage.")
            return
        model = self.dialog("--inputbox", "Genaue Modellbezeichnung:")
        if not model:
            return
        if choice == "search":
            from mark_api.bargain_hunter import search_url
            url = search_url(model)
            opener = shutil.which("xdg-open")
            if not opener:
                raise OSError("opener unavailable")
            subprocess.run([opener, url], shell=False, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
        else:
            source = self.dialog("--getopenfilename", str(Path.home()), "*.csv *.json | Angebotsdateien")
            if not source:
                return
            target = self.dialog("--getsavefilename", str(Path.home() / "Schnaeppchenbericht.html"), "*.html")
            if target:
                if Path(target).exists():
                    self.message("Bitte einen neuen Dateinamen wählen.")
                    return
                self.backend("mark_api.bargain_hunter_cli", "--modell", model,
                             "--datei", source, "--bericht", target)
                self.message("Schnäppchenbericht gespeichert. Angebotspreise sind keine bestätigten Verkaufspreise.")

    def diagnostics(self):
        installed = version("kleinanzeiger")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", installed):
            installed = "unbekannt"
        # Only allowlisted values; never paths, environment, child output or exceptions.
        self.text("Kleinanzeiger " + installed + "\nKDE-Dialoge: verfügbar\n"
                  "Client: " + ("läuft" if self.client and self.client.poll() is None else "beendet")
                  + "\nLetzte Aktion: " + self.last_status)

    def run(self):
        actions = {
            "konto": lambda: self.start("konto"), "offline": lambda: self.start("offline"),
            "stop": self.stop, "backup": self.backup, "articles": self.articles,
            "weekly": self.weekly, "bargains": self.bargains, "diagnostics": self.diagnostics,
            "manual": lambda: self.text(MANUAL), "changes": lambda: self.text(CHANGES),
        }
        menu = ("konto", "Konto / Dashboard starten", "offline", "Offline-Dashboard starten",
                "stop", "Client beenden", "backup", "SQLite-Backup erstellen",
                "articles", "Artikel CSV/XLSX: Vorschau und Import", "weekly", "Wochenbericht",
                "bargains", "Schnäppchenjäger", "diagnostics", "Diagnose",
                "manual", "Handbuch", "changes", "Änderungen")
        try:
            while True:
                choice = self.dialog("--menu", "Aktion wählen (Abbrechen beendet den Client):", *menu)
                if choice is None:
                    return 0
                if choice not in actions:
                    continue
                try:
                    if actions[choice]() is not False:
                        self.last_status = "Abgeschlossen"
                except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                    self.last_status = "Fehlgeschlagen"
                    self.message("Aktion fehlgeschlagen. Installation, Eingabedatei und Zugriffsrechte prüfen.\n"
                                 "Rohdaten werden aus Datenschutzgründen nicht angezeigt.")
        finally:
            self.stop()


MANUAL = """Kleinanzeiger · Linux/KDE
Konto startet den bestehenden Linux-Client mit dessen dauerhaftem Chrome-Profil.
Offline arbeitet ohne Kontoabruf. Ein erster Start legt die lokale Datenbank an.
Die Zentrale bleibt geöffnet und stoppt ihren Client beim Schließen. Ein vorher
laufender Browser wird nicht beendet. Ein Moduswechsel erfordert Client beenden.
Artikel: CSV/XLSX wählen, Vorschau vollständig prüfen, dann ausdrücklich bestätigen.
Importiert werden lokale Entwürfe. Änderungen der Datei nach der Vorschau werden abgelehnt.
XLSX benötigt das optionale Paket openpyxl (private-web Extra).
Backup: vorhandene Datenbank, neue Zieldatei in vorhandenem Ordner wählen.
Wochenbericht: Zielordner wählen; vorhandener Bericht desselben UTC-Tages wird ersetzt.
Schnäppchenjäger: lokale CSV/JSON, Suchmail-Radar über ein JSON-Profil oder bewusste Browser-Suche, kein Scraping.
Die bisherige KI-Funktion bleibt unverändert. Wiederherstellung, E-Mail-Import und
Updates erfolgen nicht über diese Oberfläche. Updates über die eigene Paketverwaltung.
"""
CHANGES = """Kleinanzeiger 1.2.0 · KDE-Kontrollzentrale
Neue Linux-Bedienung mit externem kdialog, Konto-/Offline-Start und Client-Verwaltung.
Geprüfte SQLite-Backup-CLI, Artikelvorschau mit bestätigtem hashgebundenem Import,
lokaler Wochenbericht, Schnäppchenjäger, bereinigte Diagnose und dieses Handbuch.
"""


def main() -> int:
    kdialog = shutil.which("kdialog")
    if not kdialog:
        print("Kleinanzeiger: kdialog fehlt. Installation: nix profile install nixpkgs#kdePackages.kdialog",
              file=sys.stderr)
        return 2
    center = Center(kdialog)
    def interrupted(*_):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupted)
                for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        with instance_lock(Path.home() / ".local" / "state" / "kleinanzeiger") as acquired:
            if not acquired:
                center.message("Die Kontrollzentrale ist bereits geöffnet.")
                return 1
            return center.run()
    except KeyboardInterrupt:
        return 130
    except OSError:
        print("Kleinanzeiger: Kontrollzentrale konnte nicht geöffnet werden.", file=sys.stderr)
        return 2
    finally:
        center.stop()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
