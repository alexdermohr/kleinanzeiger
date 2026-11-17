"""Local opt-in radar profiles and Linux user path units; no network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


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
            identity = 'category:' + CATEGORY
            expected = self.config(LABEL)
            expected.update(category=CATEGORY, search_url=SEARCH_URL)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        if profile != self.data / digest / 'profil.json' or config != expected:
            raise ValueError('Nur unveränderte, hier eingerichtete Profile sind für Monitoring zugelassen.')
        safe(profile.parent / 'suchmails', directory=True)
        for name in ('history.sqlite', 'bericht.html', 'bericht.json'):
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
            found.append((profile, config['model']))
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

    def unit_content(self, profile: Path) -> dict[str, str]:
        self.load(profile)
        name = 'kleinanzeiger-radar-' + profile.parent.name
        # ExecStart's executable is not subject to dollar expansion; arguments are.
        command = quoted(sys.executable) + ' ' + ' '.join(quoted(s, command=True) for s in
                           ('-I', '-m', 'mark_api.bargain_hunter_cli', '--radar', str(profile)))
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
        return 'Monitoring aktiv; wartet auf atomar zugestellte EML-Dateien.'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('create').add_argument('model')
    commands.add_parser('create-silver-sacd')
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
