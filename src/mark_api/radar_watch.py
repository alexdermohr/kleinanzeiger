"""Local opt-in radar profiles and Linux user path units; no network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from contextlib import closing


def safe(path: Path, *, directory=False):
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsicherer Pfad: absolute Pfade ohne symbolische Links erforderlich.')
    info = path.stat()
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise ValueError('Datei oder Ordner gehört nicht diesem Benutzer.')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Erwartete reguläre Datei bzw. Ordner fehlt.')
    if os.name == 'posix' and info.st_mode & 0o022:
        raise ValueError('Profilpfade dürfen nicht für andere Benutzer schreibbar sein.')


def private_dir(path: Path):
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsicherer Ordnerpfad.')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    safe(path, directory=True)


def path_setting(path: Path) -> str:
    value = str(path)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Monitoring-Pfade dürfen keine Steuerzeichen enthalten.')
    # Path directives take a literal absolute path, unlike ExecStart argv.
    return value.replace('%', '%%')


def quoted(value: str, *, command=False) -> str:
    # systemd.syntax quoted strings, specifier expansion, then ExecStart dollars.
    value = value.replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    return '"' + ''.join('\\x%02x' % ord(c) if ord(c) < 33 or c in '\\"' else c
                         for c in value) + '"'


class RadarWatch:
    def __init__(self, data: Path | None = None, units: Path | None = None):
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise ValueError('Radar bitte als normaler Benutzer ohne sudo/root verwenden.')
        self.data = data or Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'kleinanzeiger/schnaeppchenradar'
        self.units = units or Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user'

    @staticmethod
    def model(value: str) -> str:
        value = value.strip()
        if not 2 <= len(value) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Bitte eine exakte Modellbezeichnung mit 2–200 Zeichen ohne Steuerzeichen eingeben.')
        if not any(c.isalnum() for c in value):
            raise ValueError('Die Modellbezeichnung muss Buchstaben oder Zahlen enthalten.')
        return value

    @staticmethod
    def config(model: str) -> dict:
        return dict(model=model, input_type='eml', input='suchmails', max_age_hours=48,
                    history='history.sqlite', report='bericht.html', json_report='bericht.json',
                    min_peers=4, discount_pct=30, notify=True)

    def create_category(self) -> Path:
        from .silver_sacd import LABEL, CATEGORY, SEARCH_URL
        config = self.config(LABEL)
        config.update(category=CATEGORY, search_url=SEARCH_URL)
        return self._create(LABEL, config, 'category:' + CATEGORY)

    def create_public_category(self) -> Path:
        from .silver_sacd import LABEL, CATEGORY, SEARCH_URL
        config = self.config(LABEL)
        config.update(input_type='public', input='angebote.json', category=CATEGORY,
                      search_url=SEARCH_URL)
        return self._create(LABEL + ' (direkte Suche)', config, 'public:' + CATEGORY)

    def create(self, model: str) -> Path:
        model = self.model(model)
        return self._create(model, self.config(model), model)

    def _create(self, model: str, config: dict, identity: str) -> Path:
        model = self.model(model)
        private_dir(self.data)
        directory = self.data / hashlib.sha256(identity.encode()).hexdigest()
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            profile = directory / 'profil.json'
            self.load(profile)
            if json.loads(profile.read_text(encoding='utf-8')) != config:
                raise ValueError('Vorhandenes Profil weicht ab; es wird nichts überschrieben.')
            return profile
        # Exclusive creation: never replace files, databases or a prior configuration.
        (directory / 'suchmails').mkdir(mode=0o700)
        profile = directory / 'profil.json'
        with profile.open('x', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        profile.chmod(0o600)
        return profile

    def load(self, profile: Path) -> dict:
        safe(self.data, directory=True)
        safe(profile.parent, directory=True)
        safe(profile)
        config = json.loads(profile.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or not isinstance(config.get('model'), str):
            raise ValueError('Kein eingerichtetes Radar-Profil.')
        identity = self.model(config['model'])
        expected = self.config(config['model'])
        if 'category' in config:
            from .silver_sacd import LABEL, CATEGORY, SEARCH_URL
            public_mode = config.get('input_type') == 'public'
            identity = ('public:' if public_mode else 'category:') + CATEGORY
            expected = self.config(LABEL)
            if public_mode:
                expected.update(input_type='public', input='angebote.json')
            expected.update(category=CATEGORY, search_url=SEARCH_URL)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        if profile != self.data / digest / 'profil.json' or config != expected:
            raise ValueError('Nur unveränderte, hier eingerichtete Profile sind für Monitoring zugelassen.')
        safe(profile.parent / 'suchmails', directory=True)
        for name in ('history.sqlite', 'bericht.html', 'bericht.json', 'angebote.json'):
            path = profile.parent / name
            if path.exists() or path.is_symlink():
                safe(path)
        return config

    def profiles(self) -> list[tuple[Path, str]]:
        if not self.data.exists():
            return []
        safe(self.data, directory=True)
        found = []
        for directory in self.data.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            profile = directory / 'profil.json'
            try:
                config = self.load(profile)
            except (ValueError, OSError):
                continue
            found.append((profile, config['model'] + (' (direkte Suche)' if config.get('input_type') == 'public' else '')))
            if len(found) > 100:
                raise ValueError('Mehr als 100 Suchprofile: Profilordner prüfen.')
        return sorted(found, key=lambda entry: (entry[1].casefold(), str(entry[0])))

    def deliver(self, profile: Path, source: Path) -> Path:
        self.load(profile)
        # Export anywhere locally; never follow a symlink or read devices/FIFOs.
        if source.is_symlink():
            raise ValueError('EML-Quelle darf kein symbolischer Link sein.')
        fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('EML-Quelle muss eine reguläre Datei sein.')
            from .search_mail import MAX_BYTES
            content = stream.read(MAX_BYTES + 1)
        if not content or len(content) > MAX_BYTES:
            raise ValueError('EML-Quelle ist leer oder größer als 1 MB.')
        target = profile.parent / 'suchmails' / (hashlib.sha256(content).hexdigest() + '.eml')
        if target.exists() or target.is_symlink():
            safe(target)
            if target.read_bytes() != content:
                raise ValueError('Zustellkonflikt; vorhandene Datei bleibt unverändert.')
            return target
        # Stage outside inbox so a partially written .eml is never observed.
        fd, temporary = tempfile.mkstemp(prefix='.delivery-', dir=profile.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                safe(target)
                if target.read_bytes() != content:
                    raise ValueError('Zustellkonflikt.')
        finally:
            os.unlink(temporary)
        return target

    def local_status(self, profile: Path) -> str:
        config = self.load(profile)
        inbox = profile.parent / 'suchmails'
        count = 0
        for item in inbox.iterdir():
            if item.suffix.lower() == '.eml' and not item.is_symlink() and item.is_file():
                count += 1
                if count > 1000:
                    break
        if config.get('input_type') == 'public':
            snapshot = profile.parent / 'angebote.json'
            lines = ['Quelle: öffentliche Kleinanzeigen-Suchseite (alle 30 Minuten).',
                     'Letzte heruntergeladene Ergebnisliste vorhanden.' if snapshot.is_file() else
                     'Noch keine erfolgreich heruntergeladene Ergebnisliste.',
                     'Bei HTTP-/Parserfehlern bleibt der letzte Bericht erhalten.']
        else:
            lines = [f'EML-Eingang: {inbox}',
                     f'EML-Dateien: {count if count <= 1000 else "über 1000"}',
                     'Eingangsquelle nicht belegt; kein neuer Marktplatzfeed.' if not count else
                     'Lokale EML-Dateien vorhanden; automatische Herkunft nicht nachgewiesen.']
        if config.get('category'):
            lines.append(f'SACD-Kandidatenbericht: {profile.parent / "bericht.html"}')
        history = profile.parent / 'history.sqlite'
        if not history.exists():
            missing = ('Noch kein erfolgreicher Seitenabruf.' if config.get('input_type') == 'public'
                       else 'Noch keine lokale Auswertung; Eingangsquelle nicht eingerichtet/nicht belegt.')
            return '\n'.join(lines + [missing])
        safe(history)
        try:
            with closing(sqlite3.connect(f'{history.as_uri()}?mode=ro', uri=True, timeout=1)) as db:
                if db.execute('PRAGMA application_id').fetchone()[0] != 0x42524452:
                    raise ValueError('Ungültige Radar-Historie.')
                row = db.execute('SELECT payload FROM radar_runs ORDER BY id DESC LIMIT 1').fetchone()
            if not row or len(row[0]) > 2_000_000:
                raise ValueError('Keine lesbare Auswertung.')
            result = json.loads(row[0])
            stamp = result.get('generated_at', '')
            if not isinstance(stamp, str) or not re.fullmatch(r'[0-9T:+.Z-]{10,40}', stamp):
                raise ValueError('Ungültiger Auswertungszeitpunkt.')
            counts = [result.get(k) for k in ('imported', 'new', 'price_changes', 'hits')]
            if any(type(n) is not int or not 0 <= n <= 1000000 for n in counts):
                raise ValueError('Ungültige Auswertungszähler.')
            lines += [f'Letzte lokale Auswertung: {stamp}',
                      f'Importiert: {counts[0]}; neu: {counts[1]}; Preisänderungen: {counts[2]}; Treffer: {counts[3]}']
            if config.get('category'):
                observed_count = result.get('known_candidate_count')
                events_count = result.get('candidate_events')
                if (type(observed_count) is int and 0 <= observed_count <= 1000000 and
                        type(events_count) is int and 0 <= events_count <= 1000000):
                    lines.append(f'SACD: {events_count} Kandidatenereignisse dieser Auswertung; '
                                 f'{observed_count} historisch beobachtete Kandidaten.')
                else:
                    lines.append('SACD-Kandidatenbestand im letzten Lauf nicht verfügbar.')
            # Only fixed parser messages are displayed: exceptions can contain paths or secrets.
            allowed = {
                'Keine EML-Dateien; keine Angebote', 'Eindeutiger Absenderheader fehlt',
                'Absenderheader passt nicht zu Kleinanzeigen (kein Echtheitsbeweis)',
                'Eindeutiger Datumsheader fehlt', 'Ungültiger Datumsheader',
                'Zeitangabe braucht Zeitzone', 'Mail ist alt, undatiert oder liegt in der Zukunft',
                'Textlayout benötigt genau Titel, EUR-Preis, Direktlink',
                'HTML-Layout mehrdeutig oder nicht unterstützt', 'Kein eindeutiger EUR-Festpreis (VB/Free ausgeschlossen)',
                'Kein direkter sicherer Inseratlink; Tracking wird nicht aufgelöst',
                'Defekt, Suchanzeige oder Zubehör statt Player', 'ND8006 unterstützt keine SACD-Disc-Wiedergabe',
                'Explizite Silberangabe im Mailtitel fehlt', 'Keine eindeutige passende Produktgruppe/Modellzuordnung',
                'Multipart-Zuordnung widersprüchlich oder leer', 'Anhänge/andere MIME-Inhalte nicht unterstützt',
                'Kein positiver Festpreis', 'Nur reguläre EML-Dateien erlaubt', 'EML größer als 1 MB',
                'Fehlerhafte oder zu komplexe MIME-Mail', 'MIME-Typ nicht unterstützt',
                'Tonträger oder widersprüchliche SACD-Disc-Angabe',
                'Unklare Angabe oder Tonträger statt Player',
                'Defekt, Tonträger oder Zubehör statt eindeutigem Player',
                'Widersprüchliche Farbe oder SACD-Angabe', 'Keine eindeutige SACD-Disc-Wiedergabe',
                'SACD-Qualität belegt keine SACD-Disc-Wiedergabe',
                'SACD-Disc-fähiger Player im Titel nicht plausibel benannt',
            }
            diagnostics = result.get('diagnostics', [])
            if not isinstance(diagnostics, list):
                raise ValueError('Ungültige Importdiagnostik.')
            lines.append('Importdiagnostik:')
            for entry in diagnostics[:100]:
                reason = entry.get('reason') if isinstance(entry, dict) else None
                if not isinstance(reason, str):
                    reason = ''
                if reason not in allowed and not re.fullmatch(r'[0-9]{1,6} alte EML-Dateien außerhalb des Zeitfensters ignoriert', reason):
                    reason = 'Weitere Diagnose aus Datenschutzgründen nicht angezeigt.'
                filename = entry.get('file', '') if isinstance(entry, dict) else ''
                label = filename + ': ' if isinstance(filename, str) and re.fullmatch(r'[a-f0-9]{64}\.eml', filename) else ''
                lines.append('- ' + label + reason)
            if not diagnostics:
                lines.append('- Keine Importdiagnosen.')
            if len(diagnostics) > 100:
                lines.append('- Weitere Diagnosen abgeschnitten.')
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            lines.append('Letzte lokale Auswertung oder Importdiagnostik nicht lesbar.')
        return '\n'.join(lines)

    def unit_content(self, profile: Path) -> dict[str, str]:
        self.load(profile)
        name = 'kleinanzeiger-radar-' + profile.parent.name
        # ExecStart's executable is not subject to dollar expansion; arguments are.
        command = quoted(sys.executable) + ' ' + ' '.join(quoted(s, command=True) for s in
                           ('-I', '-m', 'mark_api.bargain_hunter_cli', '--radar', str(profile)))
        if self.load(profile).get('input_type') == 'public':
            command = quoted(sys.executable) + ' ' + ' '.join(quoted(s, command=True) for s in
                                   ('-I', '-m', 'mark_api.public_sacd', '--profile', str(profile)))
            return {name + '.timer': '[Unit]\nDescription=Kleinanzeiger öffentliche SACD-Suche\n[Timer]\n'
                    'OnBootSec=3min\nOnUnitActiveSec=30min\nRandomizedDelaySec=2min\n'
                    f'Unit={name}.service\n[Install]\nWantedBy=timers.target\n',
                    name + '.service': '[Unit]\nDescription=Kleinanzeiger öffentliche SACD-Suche\n'
                    '[Service]\nType=oneshot\nUMask=0077\nStandardOutput=journal\nStandardError=journal\n'
                    'Environment="PATH=%h/.nix-profile/bin:/run/current-system/sw/bin:/usr/bin:/bin"\n'
                    f'ExecStart={command}\n'}
        return {name + '.path': '[Unit]\nDescription=Kleinanzeiger local radar inbox\n[Path]\n'
                f'PathChanged={path_setting(profile.parent / "suchmails")}\n'
                f'PathModified={path_setting(profile.parent / "suchmails")}\nUnit={name}.service\n'
                '[Install]\nWantedBy=default.target\n',
                name + '.service': '[Unit]\nDescription=Kleinanzeiger local radar\nStartLimitIntervalSec=0\n[Service]\nType=oneshot\n'
                'UMask=0077\nStandardOutput=null\nStandardError=journal\nEnvironment="PATH=%h/.nix-profile/bin:/run/current-system/sw/bin:/usr/bin:/bin"\n'
                f'ExecStart={command}\n'}

    @staticmethod
    def systemctl(*args: str, check=True):
        result = subprocess.run(['systemctl', '--user', *args], shell=False, capture_output=True,
                                text=True, timeout=45, stdin=subprocess.DEVNULL)
        if check and result.returncode:
            raise ValueError('systemd-Benutzerdienst fehlgeschlagen. User-Session und Installation prüfen.')
        return result

    def watch(self, profile: Path, action: str) -> str:
        if sys.platform != 'linux':
            raise ValueError('Automatisches Monitoring benötigt Linux mit systemd --user.')
        if action not in ('start', 'status', 'stop'):
            raise ValueError('Unbekannte Monitoring-Aktion.')
        contents = self.unit_content(profile)
        private_dir(self.units)
        # Serialize our unit operations, also against another GUI/CLI invocation.
        import fcntl
        fd = os.open(self.units / '.kleinanzeiger-radar.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'r+') as lock:
                info = os.fstat(lock.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ValueError('Unsichere Monitoring-Sperrdatei.')
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._watch(contents, action)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('systemd antwortet nicht rechtzeitig; Monitoring-Status prüfen.') from exc

    def _watch(self, contents: dict[str, str], action: str) -> str:
        path_unit, service = contents
        for name, content in contents.items():
            path = self.units / name
            if path.exists() or path.is_symlink():
                safe(path)
                if path.read_text(encoding='utf-8') != content:
                    raise ValueError('Vorhandene fremde/geänderte Unit bleibt unverändert.')
        if action in ('status', 'stop') and not any((self.units / name).exists() for name in contents):
            states = [self.systemctl('is-active', name, check=False).returncode for name in contents]
            if any(code not in (3, 4) for code in states):
                raise ValueError('Unit-Dateien fehlen, aber systemd meldet keinen sicheren Stillstand. User-Session prüfen.')
            return 'Monitoring gestoppt (noch nicht eingerichtet).'
        if action == 'status':
            result = self.systemctl('is-active', path_unit, check=False)
            if result.returncode not in (0, 3, 4):
                raise ValueError('Monitoring-Status konnte nicht ermittelt werden.')
            failed = self.systemctl('is-failed', service, check=False).returncode == 0
            status = 'Monitoring aktiv' if result.returncode == 0 else 'Monitoring gestoppt'
            return status + ('; letzte Auswertung fehlgeschlagen (journalctl --user prüfen).' if failed else '')
        if action == 'stop':
            self.systemctl('disable', '--now', path_unit)
            self.systemctl('stop', service)
            return 'Monitoring gestoppt; Eingänge und Ergebnisse bleiben erhalten.'
        # -I deliberately excludes the checkout/PYTHONPATH: only the installed interpreter works.
        probe_code = (
            'from importlib.metadata import distribution; from pathlib import Path; '
            'import mark_api.bargain_hunter_cli as cli; '
            'installed = distribution("kleinanzeiger").locate_file("mark_api/bargain_hunter_cli.py"); '
            'assert installed.is_file() and Path(cli.__file__).resolve() == installed.resolve()'
        )
        probe = subprocess.run([sys.executable, '-I', '-c', probe_code],
                               shell=False, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        if probe.returncode:
            raise ValueError('Bitte Kleinanzeiger in dieser Python-Umgebung installieren; Quellcheckout reicht nicht.')
        created = []
        was_active = self.systemctl('is-active', path_unit, check=False).returncode == 0
        try:
            for name, content in contents.items():
                path = self.units / name
                if not path.exists():
                    with path.open('x', encoding='utf-8') as stream:
                        stream.write(content)
                    created.append(path)
            self.systemctl('daemon-reload')
            self.systemctl('enable', '--now', path_unit)
            if self.systemctl('is-active', path_unit, check=False).returncode != 0:
                raise ValueError('Monitoring wurde nicht aktiv; Einrichtung zurückgerollt.')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            rollback_failed = False
            cleanup = [('disable', '--now', path_unit), ('stop', service)] if not was_active else []
            for args in cleanup:
                try:
                    rollback_failed |= self.systemctl(*args, check=False).returncode != 0
                except (OSError, subprocess.SubprocessError):
                    rollback_failed = True
            for path in created:
                path.unlink()
            try:
                rollback_failed |= self.systemctl('daemon-reload', check=False).returncode != 0
            except (OSError, subprocess.SubprocessError):
                rollback_failed = True
            if rollback_failed:
                raise ValueError('Start fehlgeschlagen; systemd-Rollback unvollständig. Monitoring-Status und User-Session prüfen.') from exc
            raise
        return ('Monitoring aktiv; öffentliche Suchseite wird automatisch alle 30 Minuten geprüft.'
                if path_unit.endswith('.timer') else
                'Monitoring aktiv; wartet auf atomar zugestellte EML-Dateien.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('create').add_argument('model')
    commands.add_parser('create-silver-sacd')
    commands.add_parser('create-public-sacd')
    commands.add_parser('run-public').add_argument('profile', type=Path)
    delivery = commands.add_parser('deliver')
    delivery.add_argument('profile', type=Path)
    delivery.add_argument('source', type=Path)
    for name in ('start', 'status', 'stop'):
        commands.add_parser(name).add_argument('profile', type=Path)
    args = parser.parse_args(argv)
    try:
        manager = RadarWatch()
        if args.action == 'create-silver-sacd':
            print(manager.create_category())
        elif args.action == 'create-public-sacd':
            print(manager.create_public_category())
        elif args.action == 'run-public':
            from .public_sacd import run_public
            result = run_public(args.profile)
            print(json.dumps({key: result[key] for key in ('candidate_events', 'known_candidate_count', 'price_changes')}, ensure_ascii=False))
        elif args.action == 'create':
            print(manager.create(args.model))
        elif args.action == 'deliver':
            print(manager.deliver(args.profile.absolute(), args.source))
        else:
            print(manager.watch(args.profile.absolute(), args.action))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Radar: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
