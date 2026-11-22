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
import threading
from contextlib import contextmanager

from . import linux_launcher, linux_release_check


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
        self._release_checked = False
        self._release_latest = None
        self._release_thread = None

    def begin_release_check(self) -> None:
        """Start one read-only check without slowing the first KDE dialog."""
        if self._release_thread is not None:
            return
        self._release_thread = threading.Thread(
            target=self._release_check_worker, name="kleinanzeiger-release-check", daemon=True
        )
        self._release_thread.start()

    def _release_check_worker(self) -> None:
        try:
            self._release_latest = linux_release_check.latest_release()
        except Exception:
            self._release_latest = None
        finally:
            self._release_checked = True

    def _update_caption(self) -> str:
        latest = self._release_latest
        if self._release_checked and linux_release_check.newer(version("kleinanzeiger"), latest):
            return f"Neue Version {latest} – Versionshinweise"
        return "Updates / Version prüfen"

    def show_updates(self) -> None:
        installed = version("kleinanzeiger")
        if not self._release_checked:
            self.message("Die automatische Suche nach neuen Versionen läuft im Hintergrund.\n"
                         "Updates später erneut öffnen.")
            return
        latest = self._release_latest
        if latest is None:
            self.message("Aktuelle Version: " + installed + "\n"
                         "Offizielle Release-Abfrage derzeit nicht möglich.\n"
                         "Der normale Betrieb bleibt unverändert.")
        elif linux_release_check.newer(installed, latest):
            if self.dialog("--yesno", f"Version {latest} ist verfügbar (installiert: {installed}).\n"
                           "Offizielle Versionshinweise und Downloads im Browser öffnen?\n"
                           "Installation erfolgt nicht automatisch.") is not None:
                opener = shutil.which("xdg-open")
                if not opener:
                    raise OSError("browser opener unavailable")
                subprocess.run(
                    [opener, linux_release_check.release_url(latest)],
                    shell=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=30, check=True,
                )
        else:
            self.message(f"Kleinanzeiger {installed} ist aktuell.\n"
                         f"Neueste offizielle Version: {latest}")

    def dialog(self, *args: str) -> str | None:
        result = subprocess.run(
            [self.kdialog, "--title", "Kleinanzeiger · Kontrollzentrale", *args],
            shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        # Real kdialog returns 1 for menu Escape/No and 2 for Escape in
        # yes/no and message dialogs. A failed invocation can also return 1,
        # but reports the error on stderr; never silently treat that as No.
        if result.returncode in (1, 2) and not (result.stderr or "").strip():
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
            if choice in ("new", "silver-sacd", "public-sacd"):
                model = "SACD-Player silber" if choice in ("silver-sacd", "public-sacd") else self.dialog("--inputbox", "Exakte Modellbezeichnung (keine allgemeine Produktkategorie):")
                if model is None:
                    return
                profile = (manager.create_public_category() if choice == "public-sacd" else
                           manager.create_category() if choice == "silver-sacd" else manager.create(model))
                if choice == "public-sacd":
                    self.text('Öffentliche SACD-Suche in Deutschland, ohne E-Mail oder Anmeldung.\n'
                              'Nur silberne und plausible SACD-Player; keine modellübergreifende Schnäppchenwertung.\n'
                              'Das Profil rotiert drei öffentliche Suchanfragen, darunter eine breitere SACD-Player-Suche, bei nur einem Abruf alle 30 Minuten.\n'
                              'Bei Sperre oder Layoutänderung bleibt die letzte Auswertung erhalten.')
                if choice == "silver-sacd":
                    from mark_api.silver_sacd import SEARCH_URL
                    self.text("Silberne Player für echte SACD-Discs, Deutschland, ohne Preislimit.\n"
                              "Modellpreisbewertung nur mit vier frischen Vergleichsanzeigen; Foto und Funktion selbst prüfen.\n"
                              "Kleinanzeigen-Suche öffnen → Suchauftrag speichern (eigenes Konto erforderlich).\n"
                              f"Offizielle Suchadresse: {SEARCH_URL}\n"
                              "Ohne eigene echte EML-Zustellung keine laufende Marktbeobachtung.")
                if choice == 'public-sacd':
                    self.text(f"Profil: {profile}\nBericht: {profile.parent / 'bericht.html'}\n\n"
                              "Abruf ausschließlich öffentlicher Suchseiten; kein Konto und keine E-Mails. "
                              "Bei erster Abfrage nur Bestandsaufnahme, danach neue IDs/Preise prüfen.\n"
                              "Der Kandidatenbericht zeigt die beobachteten Preise aufsteigend.")
                else:
                    self.text(f"Profil: {profile}\nEingangsordner: {profile.parent / 'suchmails'}\n\n"
                              "Kleinanzeigen-Suche auf Website als Suchauftrag speichern und eigene EMLs "
                              "atomar in diesen Ordner zustellen.\n"
                              "Keine Mailkonto-Anmeldung oder automatische Marktabfrage. "
                              "Echte Mailtemplates sind noch nicht verifiziert.\n"
                              "Berichte und Historie entstehen bei der ersten Auswertung im Profilordner.")
                monitor_desc = ("Ruft die öffentliche Suchseite höchstens alle 30 Minuten ab.\n" if choice == 'public-sacd' else
                                "Verarbeitet künftig nur EML-Dateiänderungen im angezeigten Eingang.\n")
                if self.dialog("--yesno", "Lokales Monitoring für dieses Profil jetzt aktivieren?\n"
                               + monitor_desc + "Stoppen ist unter Monitoring möglich.") is None:
                    return
                self.message(manager.watch(profile, "start"))
            else:
                profiles = manager.profiles()
                if not profiles:
                    self.message("Noch keine Suchprofile vorhanden. Zuerst Neues Suchprofil einrichten wählen.")
                    return
                entries = []
                for index, (_profile, model) in enumerate(profiles):
                    entries.extend((str(index), model))
                selected = self.dialog("--menu", "Suchprofil nach Modell auswählen:", *entries)
                if selected is None:
                    return
                if not selected.isdecimal() or not 0 <= int(selected) < len(profiles):
                    raise ValueError("Ungültiges Suchprofil.")
                profile = profiles[int(selected)][0]
                if choice == "import":
                    if manager.load(profile).get('input_type') == 'public':
                        self.message('Direkte Suchprofile benötigen keinen E-Mail-Import.')
                        return
                    source = self.dialog("--getopenfilename", str(Path.home()), "*.eml | Gespeicherte E-Mail")
                    if not source:
                        return
                    manager.deliver(profile, Path(source))
                    self.message("EML-Datei atomar übernommen. Bei aktivem Monitoring wird sie automatisch "
                                 "geprüft; sonst Suchmail-Radar aus Profil auswerten. "
                                 "Unbekannte Mailformate erscheinen als Importdiagnose.")
                    return
                status = manager.watch(profile, "status")
                direct = manager.load(profile).get('input_type') == 'public'
                actions = ("status", "Status anzeigen", "report", "Kandidatenbericht öffnen",
                           "start", "Monitoring starten", "stop", "Monitoring stoppen")
                if direct:
                    actions += ("fetch", "Öffentliche Suche jetzt ausführen",
                                "notify-test", "Benachrichtigung testen",
                                "price-target", "Persönliches Modellpreisziel setzen / löschen")
                action = self.dialog("--menu", status, *actions)
                if action == "status":
                    self.text(manager.watch(profile, "status") + "\n\n" + manager.local_status(profile))
                elif action == "report":
                    from mark_api.radar_watch import safe
                    report = profile.parent / 'bericht.html'
                    if not report.exists() and not report.is_symlink():
                        self.message("Noch kein Radarbericht vorhanden. Zuerst eine Auswertung starten.")
                        return
                    safe(report)
                    opener = shutil.which('xdg-open')
                    if not opener:
                        self.message("Kein Browser-Öffner verfügbar. Bitte den Bericht über die Dateiverwaltung öffnen.")
                        return
                    subprocess.run([opener, str(report)], shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=30, check=True)
                elif action == "price-target" and direct:
                    options = manager.price_target_models(profile)
                    if not options:
                        self.message("Noch keine erkannten Modellnummern im Radarbericht. "
                                     "Zuerst die automatische Suche und den Bericht prüfen.")
                        return
                    targets = manager.price_targets(profile)
                    entries = []
                    for key, label in options.items():
                        existing = targets.get(key)
                        entries.extend((key, label + (
                            " · Ziel " + existing + " EUR" if existing is not None else "")))
                    selected = self.dialog("--menu",
                        "Persönliches Preislimit für ein bereits erkanntes Modell auswählen:", *entries)
                    if selected is None:
                        return
                    if selected not in options:
                        raise ValueError("Unbekannter Modellschlüssel.")
                    value = self.dialog("--inputbox",
                        "Maximaler Angebotspreis in EUR. Leer lassen entfernt das Preisziel. "
                        "Dies ist dein eigener Grenzwert, kein Marktwert.",
                        targets.get(selected, ""))
                    if value is None:
                        return
                    result = manager.set_price_target(profile, selected, value.strip() or None)
                    if selected in result:
                        self.message(f"Persönliches Preislimit für {selected}: {result[selected]} EUR. "
                                     "Wirksam beim nächsten regulären Suchlauf; kein Marktpreisvergleich.")
                    else:
                        self.message(f"Persönliches Preislimit für {selected} entfernt. "
                                     "Andere Modellziele bleiben erhalten.")
                elif action == "notify-test" and direct:
                    notifier = shutil.which("notify-send")
                    if not notifier:
                        raise OSError("Desktop-Benachrichtigungsdienst nicht verfügbar.")
                    subprocess.run([notifier, "--app-name=Kleinanzeiger", "--",
                                    "Kleinanzeiger · Testmeldung",
                                    "SACD-Radar: Dies ist ein Funktionstest, kein neues Angebot."],
                                   shell=False, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=10, check=True)
                    self.message("Testmeldung an den Desktop-Benachrichtigungsdienst übergeben. "
                                 "Es wurde keine Marktänderung erzeugt.")
                elif action == "fetch" and direct:
                    from mark_api.public_sacd import run_public
                    result = run_public(profile)
                    self.message(f"Öffentliche Suche abgeschlossen: "
                                 f"{result['known_candidate_count']} historisch beobachtete Kandidaten, "
                                 f"{result['candidate_events']} neue Ereignisse. "
                                 "Bericht über Monitoring öffnen.")
                elif action in ("start", "stop"):
                    self.message(manager.watch(profile, action))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Plain text includes useful validation/systemd guidance without rich-text interpretation.
            self.text(f"Radar konnte die Aktion nicht abschließen: {exc}")

    def bargains(self):
        choice = self.dialog("--menu", "Schnäppchenjäger: beobachtete Angebotspreise",
                             "file", "Lokale CSV/JSON auswerten", "search", "Manuell im Browser suchen",
                             "radar", "Suchmail-/Datei-Radar aus Profil auswerten",
                             "new", "Neues Suchprofil einrichten",
                             "silver-sacd", "Silberne SACD-Player beobachten",
                             "public-sacd", "Silberne SACD-Player direkt suchen (automatisch)",
                             "watch", "Monitoring: Status / Starten / Stoppen",
                             "import", "Gespeicherte E-Mail (.eml) in Suchprofil importieren")
        if choice in ("new", "silver-sacd", "public-sacd", "watch", "import"):
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
            if result.get("reference_type") in ("category_unpriced", "category_asking_prices"):
                self.message(f"SACD-Kategorie geprüft: {result['candidate_events']} neue oder neu bewertbare Geräteereignisse, "
                             f"{result['hits']} modellbezogen auffällig günstige Angebote. "
                             "Modellpreiswertung nur mit mindestens vier frischen Peers. Details im Radar-Bericht.")
                return
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
        status = (
            "Prüfung läuft" if not self._release_checked else
            "nicht verfügbar" if self._release_latest is None else
            f"neuer als installiert: {self._release_latest}" if
            linux_release_check.newer(installed, self._release_latest) else
            f"aktuell: {self._release_latest}"
        )
        self.text("Kleinanzeiger " + installed + "\nUpdateprüfung: " + status
                  + "\nKDE-Dialoge: verfügbar\n"
                  "Client: " + ("läuft" if self.client and self.client.poll() is None else "beendet")
                  + "\nLetzte Aktion: " + self.last_status)

    def run(self):
        actions = {
            "konto": lambda: self.start("konto"), "offline": lambda: self.start("offline"),
            "stop": self.stop, "backup": self.backup, "articles": self.articles,
            "weekly": self.weekly, "bargains": self.bargains, "diagnostics": self.diagnostics,
            "updates": self.show_updates,
            "manual": lambda: self.text(MANUAL), "changes": lambda: self.text(CHANGES),
        }
        try:
            while True:
                menu = ("konto", "Konto / Dashboard starten", "offline", "Offline-Dashboard starten",
                        "stop", "Client beenden", "backup", "SQLite-Backup erstellen",
                        "articles", "Artikel CSV/XLSX: Vorschau und Import", "weekly", "Wochenbericht",
                        "bargains", "Schnäppchenjäger", "updates", self._update_caption(),
                        "diagnostics", "Diagnose", "manual", "Handbuch", "changes", "Änderungen")
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
Schnäppchenjäger: lokale CSV/JSON, Suchmail-Radar und automatische öffentliche SACD-Suche.
Der SACD-Timer rotiert drei Suchbegriffe (auch SACD Player) bei höchstens einem Abruf alle 30 Minuten.
Monitoring erlaubt persönliche Modellpreislimits, Status, Kandidatenbericht und Testbenachrichtigung.
Kandidaten erscheinen aufsteigend nach beobachtetem Angebotspreis. Erst vier
weitere frische Inserate desselben Modells ermöglichen einen Vergleichsmedian.
Persönliche Preislimits sind keine Marktwerte. Angebote ohne Silberangabe erscheinen nur zur Fotoprüfung, ohne Alarm.
Tatsächliche Verkaufspreise und Funktionsfähigkeit sind nicht belegt.
Die bisherige KI-Funktion bleibt unverändert. Wiederherstellung und
E-Mail-Import erfolgen nicht über diese Oberfläche.
Bei Start prüft die Kontrollzentrale im Hintergrund auf offizielle neue Versionen.
Unter Updates lassen sich verfügbare Versionshinweise im Browser öffnen.
Die Installation erfolgt nicht automatisch. Bei Netzfehlern bleibt der Betrieb erhalten.
"""
CHANGES = """Kleinanzeiger 1.8.2 · KDE-Kontrollzentrale
Behoben: Escape schließt auch Ja/Nein- und Hinweisdialoge normal.
Echte KDialog-Aufruffehler bleiben als Fehler erkennbar.
Keine Änderungen an Daten oder Plattformfunktionen.

Behoben: Ein zu früher Timerlauf nach einem Neustart wird wegen der
fortgeltenden 30-Minuten-Abrufsperre als übersprungen gemeldet,
nicht mehr als fehlgeschlagener Dienst. Keine neuen HTTP-Abfragen,
kein Zurücksetzen des Suchzählers, kein Überschreiben der Berichte.
Echte HTTP-, Daten- und Sperrfehler bleiben ausdrücklich Fehler.

Neu: Beim Start prüft die KDE-Zentrale im Hintergrund nach offiziellen
stabilen Updates, zeigt eine neuere Version direkt im Menü und öffnet
nur nach Bestätigung die offizielle GitHub-Release-Seite im Browser.
Keine automatische Installation, keine Konto- oder Marktplatzmutation.
Behoben: Die manuelle Farbfotoprüfliste bleibt auch bei den folgenden
abwechselnden SACD-Suchen sichtbar, soweit die letzte Sichtung höchstens
72 Stunden zurückliegt. Zeitstempel und unbestätigte Farbe bleiben kenntlich.

Neu: Breitere dritte SACD-Abfrage bei unverändert einem Abruf je 30 Minuten.
Farbzweifelhafte Titel erscheinen getrennt zur manuellen Fotoprüfung, ohne Alarm.
Persönliche EUR-Preislimits je Modell sind unter Monitoring editierbar und
lösen einmalige Zieltreffermeldungen aus, unabhängig vom Marktpreis-Median.
Keine persönliche Preisgrenze wird als belegter Marktwert bezeichnet.
Neu: Drei öffentliche SACD-Suchbegriffe rotieren bei nur einem Abruf je 30 Minuten.
Preisprüfung: Vier weitere, frische Angebote desselben Herstellermodells
liefern einen Angebotsmedian und erlauben eine konservative Schnäppchenwertung.
Ohne Vergleichsangebote erscheinen Kandidaten ausdrücklich unbewertet.
Verbessert: SACD-Kandidaten erscheinen nach beobachtetem Preis sortiert.
Monitoring: Benachrichtigungen per Testmeldung direkt überprüfen.
Keine Testmeldung erzeugt eine neue Anzeige oder Preisänderung.
Behoben: Öffentlicher SACD-Abruf kann unter NixOS jetzt das System-
Zertifikatsbündel für die verifizierte HTTPS-Verbindung nutzen.
Neu: Silberne SACD-Player direkt suchen (automatisch).
Ein separater systemd-Benutzertimer prüft höchstens alle 30 Minuten die
öffentliche Kleinanzeigen-Suchseite ohne E-Mail, API-Schlüssel oder Anmeldung.
Neue Inserate und echte Preisänderungen werden nur einmal gemeldet.
Der erste Lauf bildet einen Bestand, ohne Altangebote als neu zu melden.
Historische Kandidaten erscheinen mit Preis und Original-Link im Bericht.
Bei fehlgeschlagenen Abrufen bleiben Berichte und SQLite-Historie erhalten.
Das bisherige EML-Radar bleibt unabhängig davon nutzbar.
Fotos, SACD-Funktion und tatsächliche Verfügbarkeit selbst prüfen.
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
            center.begin_release_check()
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