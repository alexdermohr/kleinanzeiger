"""Opt-in real Linux/Docker DAC test for the offline backup-intent operator.

Only uniquely named disposable Docker volumes and synthetic SQLite/Witness
images are touched. This is not a live service, historic mmap revocation,
host quiescence or platform-write acceptance test. The caller must select an
already verified local immutable test image; no pulling is permitted.
"""
from __future__ import annotations

import io
import os
import re
import tarfile
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("MARK_INTENT_MULTIUID_TEST_IMAGE", "")
IMAGE_OK = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE))
PYTHON = "/opt/mark-api/venv/bin/python"
if IMAGE and not IMAGE_OK:
    raise ValueError("MARK_INTENT_MULTIUID_TEST_IMAGE must be an immutable sha256 image ID")
if os.environ.get("MARK_REQUIRE_INTENT_MULTIUID") == "1" and not IMAGE_OK:
    raise ValueError("required real-UID test has no immutable Docker image")


def _docker(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/usr/bin/docker", *args], check=False, capture_output=True,
        text=True, timeout=60,
    )
    if result.returncode and not allow_failure:
        raise AssertionError("isolated Docker operation failed: " + result.stderr[-900:])
    return result


_SEAL = """
import os,stat
from pathlib import Path
import mark_api
from mark_api.backup_intent_authority import BackupIntentAuthority
from mark_api.fence_witness import FenceLedger
from mark_api.storage import SnapshotStore
assert os.geteuid()==50043
assert Path(mark_api.__file__).as_posix().startswith('/src/mark_api/')
os.umask(0o022)
source=Path('/source')
sqlite=source/'mark.sqlite'
witness=source/'witness.sqlite'
SnapshotStore(sqlite)
with FenceLedger(witness,initialize=True):
    pass
op=BackupIntentAuthority(Path('/intent'))
generation='1'*32
prepared=op.prepare(generation,sqlite,witness,previous_head_sha256=None)
assert prepared['generation']==generation
result=op.seal_prepared(generation,Path('/output'),Path('/anchor'))
assert result['external_operator_intent_acknowledged'] is True
assert not (Path('/anchor')/'.inflight').exists()
# Parent permissions are intentionally set by the trusted test harness.
# Check every actual source and product-created artifact independently.
for name in ('source','output','anchor','intent'):
    directory=Path('/'+name)
    info=directory.lstat()
    assert info.st_uid==50043 and stat.S_ISDIR(info.st_mode)
    assert stat.S_IMODE(info.st_mode)==0o700,(name,info)
    for artifact in directory.rglob('*'):
        info=artifact.lstat()
        assert info.st_uid==50043,(artifact,info)
        if stat.S_ISDIR(info.st_mode):
            assert stat.S_IMODE(info.st_mode)==0o700,(artifact,info)
        else:
            assert stat.S_ISREG(info.st_mode),(artifact,info)
            assert stat.S_IMODE(info.st_mode)==0o600,(artifact,info)
pair=Path('/output')/generation
files=[
    Path('/intent/.intent.lock'),
    Path('/intent/.intent.bootstrap.json'),
    *(Path('/intent')/(generation+'.'+phase+'.json')
      for phase in ('prepared','committing','acknowledged')),
    Path('/anchor/latest.json'),
    Path('/anchor')/(generation+'.json'),
    *(pair/name for name in
      ('mark.sqlite','witness.sqlite','manifest.json','witness.sqlite.lock')),
]
for path in files:
    info=path.stat()
    assert info.st_uid==50043 and stat.S_IMODE(info.st_mode)==0o600,(path,info)
print('REAL_UID50043_COMPLETE_ARTIFACT_DAC_AND_INTENT_SEALED',flush=True)
"""

_ATTACK = """
import errno,os
from pathlib import Path
assert os.geteuid()==50044
# Positive control: RW Docker volume access actually works for this UID.
control=Path('/control/probe')
control.write_bytes(b'UID50044-RW-ALLOWED')
assert control.read_bytes()==b'UID50044-RW-ALLOWED'
# The trusted harness has made its protected roots traversable (0711).
# Before denial, this UID can stat concrete product files through them.
for name in ('source','output','anchor','intent'):
    assert os.access('/'+name,os.X_OK),name
for item in ('/source/mark.sqlite','/anchor/latest.json','/intent/.intent.lock'):
    assert Path(item).stat().st_size>=0,item
assert Path('/output/'+'1'*32).is_dir()
# All protected volumes are ALSO intentionally mounted RW to this UID.
protected=(
    '/intent/.intent.lock',
    '/intent/'+'1'*32+'.acknowledged.json',
    '/source/mark.sqlite',
    '/source/witness.sqlite',
    '/output/'+'1'*32+'/mark.sqlite',
    '/anchor/latest.json',
)
for item in protected:
    for flags in (os.O_RDONLY,os.O_WRONLY):
        try:
            fd=os.open(item,flags|os.O_CLOEXEC|os.O_NOFOLLOW)
        except OSError as exc:
            assert exc.errno in (errno.EACCES,errno.EPERM),(item,flags,exc)
        else:
            os.close(fd)
            raise AssertionError('hostile UID accessed protected path: '+item)
    try:Path(item).read_bytes()
    except PermissionError:pass
    else:raise AssertionError('hostile UID read protected image: '+item)
    try:os.unlink(item)
    except OSError as exc:
        assert exc.errno in (errno.EACCES,errno.EPERM),(item,'unlink',exc)
    else:raise AssertionError('hostile UID unlinked protected artifact: '+item)
for path in ('/intent/forged.json','/anchor/.inflight','/output/attacker'):
    try:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|
                   os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    except OSError as exc:
        assert exc.errno in (errno.EACCES,errno.EPERM),(path,exc)
    else:
        os.close(fd)
        raise AssertionError('hostile UID created protected artifact: '+path)
try:os.rename('/anchor/latest.json','/anchor/rolled-back.json')
except OSError as exc:
    assert exc.errno in (errno.EACCES,errno.EPERM),exc
else:raise AssertionError('hostile UID replaced the anchored HEAD')
print('REAL_UID50044_DAC_DENIED_WITH_RW_VOLUME_POSITIVE_CONTROL',flush=True)
"""


_RELAX_ROOTS = """
import os,stat
from pathlib import Path
assert os.geteuid()==50043
for name in ('source','output','anchor','intent'):
    root=Path('/'+name)
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    root.chmod(0o711)
print('TRUSTED_ROOTS_TRAVERSABLE_ARTIFACT_DAC_UNCHANGED',flush=True)
"""

_RESTORE_ROOTS = """
import os,stat
from pathlib import Path
assert os.geteuid()==50043
for name in ('source','output','anchor','intent'):
    root=Path('/'+name)
    assert stat.S_IMODE(root.stat().st_mode)==0o711
    root.chmod(0o700)
print('TRUSTED_ROOTS_RESTRICTED_AGAIN',flush=True)
"""

_RECHECK = """
import os,hashlib,json
from pathlib import Path
import mark_api
from mark_api.backup_intent_authority import BackupIntentAuthority,BackupIntentError
from mark_api.paired_restore_preflight import verify_offline_pair
assert os.geteuid()==50043
assert Path(mark_api.__file__).as_posix().startswith('/src/mark_api/')
generation='1'*32
root=Path('/output')/generation
anchor=Path('/anchor')
head_bytes=(anchor/'latest.json').read_bytes()
head_sha=hashlib.sha256(head_bytes).hexdigest()
head=json.loads(head_bytes)
assert head['generation']==generation
pairs=[root/name for name in ('mark.sqlite','witness.sqlite','manifest.json')]
original={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
          for path in pairs}
checked=verify_offline_pair(
    pairs[0],pairs[1],pairs[2],
    expected_manifest_sha256=head['manifest_sha256'],
)
assert checked['generation']==generation
op=BackupIntentAuthority(Path('/intent'))
checks=(
    (lambda:op.prepare(generation,Path('/source/mark.sqlite'),
        Path('/source/witness.sqlite'),previous_head_sha256=head_sha),
     'operator generation already used or pending'),
    (lambda:op.seal_prepared(generation,Path('/output'),anchor),
     'generation not singly prepared; never retry commit'),
    (lambda:op.acknowledge(generation,Path('/output'),anchor),
     'no unconsumed external committing intent'),
)
for action,message in checks:
    try:action()
    except BackupIntentError as exc:
        # Both variants reject replay of an already consumed generation.
        # An integrated backup authority can fail earlier in preflight.
        assert (
            message in str(exc)
            or (
                message == 'generation not singly prepared; never retry commit'
                and str(exc) == 'operator intent not singly prepared'
            )
        ),(message,str(exc))
    else:raise AssertionError('original acknowledged intent was replayed')
assert (anchor/'latest.json').read_bytes()==head_bytes
for path in pairs:
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original[str(path)]
assert not (anchor/'.inflight').exists()
assert (Path('/intent')/(generation+'.acknowledged.json')).is_file()
print('REAL_UID50043_EXACT_CHAIN_AND_SINGLE_USE_REPLAY_DENIAL',flush=True)
"""


@unittest.skipUnless(IMAGE_OK, "Set immutable MARK_INTENT_MULTIUID_TEST_IMAGE")
class BackupIntentMultiUidDockerTests(unittest.TestCase):
    def test_hostile_executor_cannot_access_independent_offline_intent(self) -> None:
        suffix = uuid4().hex[:12]
        names = {name: "mark-intent-" + suffix + "-" + name
                 for name in ("source", "output", "anchor", "intent", "control")}
        created: list[str] = []
        launched: list[str] = []
        with tempfile.TemporaryDirectory(prefix="mark-intent-realuid-") as temp:
            # A green Docker run must use the exact committed product modules,
            # not silently import a locally modified or untracked source tree.
            head = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            self.assertRegex(head, r"^[0-9a-f]{40}$")
            source_status = subprocess.run(
                ["git", "-C", str(ROOT), "status", "--porcelain",
                 "--untracked-files=all", "--", "src/mark_api"],
                check=True, capture_output=True, text=True, timeout=10,
            )
            self.assertFalse(source_status.stdout.strip(),
                             "Docker source differs from committed HEAD")
            package = Path(temp) / "src" / "mark_api"
            # Extract exactly HEAD's archived tracked modules: an ignored
            # local .so, legacy .pyc or other build artifact cannot enter /src.
            product_archive = subprocess.run(
                ["git", "-C", str(ROOT), "archive", "--format=tar",
                 head, "src/mark_api"],
                check=True, capture_output=True, timeout=30,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(product_archive), mode="r:") as archive:
                archive.extractall(temp, filter="data")
            self.assertTrue((package / "__init__.py").is_file())
            for item in (Path(temp), package.parent, *package.parent.rglob("*")):
                if item.is_dir():
                    item.chmod(0o755)
                elif item.is_file():
                    item.chmod(0o644)
            volume_mounts: list[str] = []
            for name, volume in names.items():
                volume_mounts.extend([
                    "--mount", f"type=volume,source={volume},target=/{name},volume-nocopy",
                ])
            source_mount = [
                "--mount",
                f"type=bind,source={package.parent},target=/src,readonly",
            ]

            def run_python(role: str, uid: int, script: str,
                           *, source_visible: bool) -> subprocess.CompletedProcess[str]:
                name = f"mark-intent-{suffix}-{role}"
                launched.append(name)
                opts = [*source_mount, "-e", "PYTHONPATH=/src"] if source_visible else []
                return _docker(
                    "run", "--rm", "--pull", "never", "--name", name,
                    "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--user", f"{uid}:{uid}", "--workdir", "/",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
                    *opts, "--entrypoint", PYTHON, *volume_mounts,
                    IMAGE, "-B", "-c", script,
                )

            try:
                for volume in names.values():
                    _docker("volume", "create", volume)
                    created.append(volume)
                seed_name = f"mark-intent-{suffix}-seed"
                launched.append(seed_name)
                _docker(
                    "run", "--rm", "--pull", "never", "--name", seed_name,
                    "--network", "none", "--read-only", "--user", "0:0",
                    "--cap-drop", "ALL", "--cap-add", "CHOWN",
                    *volume_mounts, "--entrypoint", "/bin/sh", IMAGE, "-ec",
                    "for d in /source /output /anchor /intent /control; do "
                    "out=$(ls -A \"$d\") || exit 1; "
                    "[ -z \"$out\" ] || exit 1; done; "
                    "chmod 0700 /source /output /anchor /intent /control; "
                    "chown 50043:50043 /source /output /anchor /intent; "
                    "chown 50044:50044 /control",
                )
                first = run_python("seal", 50043, _SEAL, source_visible=True)
                self.assertIn("REAL_UID50043_COMPLETE_ARTIFACT_DAC_AND_INTENT_SEALED",
                              first.stdout)
                # 0711 permits a hostile UID to traverse all harness roots.
                # Access denial must now come from the product-created 0600
                # files and 0700 generation directory, not the root harness.
                relaxed = run_python("relax", 50043, _RELAX_ROOTS, source_visible=False)
                self.assertIn("TRUSTED_ROOTS_TRAVERSABLE_ARTIFACT_DAC_UNCHANGED",
                              relaxed.stdout)
                attack = run_python("attack", 50044, _ATTACK, source_visible=False)
                self.assertIn(
                    "REAL_UID50044_DAC_DENIED_WITH_RW_VOLUME_POSITIVE_CONTROL",
                    attack.stdout,
                )
                restored = run_python("restore", 50043, _RESTORE_ROOTS,
                                      source_visible=False)
                self.assertIn("TRUSTED_ROOTS_RESTRICTED_AGAIN", restored.stdout)
                verify = run_python("verify", 50043, _RECHECK, source_visible=True)
                self.assertIn(
                    "REAL_UID50043_EXACT_CHAIN_AND_SINGLE_USE_REPLAY_DENIAL",
                    verify.stdout,
                )
                self.assertEqual(
                    subprocess.run(
                        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
                        check=True, capture_output=True, text=True, timeout=10,
                    ).stdout.strip(), head,
                    "committed product HEAD changed during the Docker test",
                )
                print("MARK_UID_TEST_EXACT_COMMITTED_HEAD=" + head, flush=True)
            finally:
                cleanup_errors: list[str] = []
                # Even a Docker CLI timeout cannot orphan an anonymous
                # container: every launch has an independently known name.
                for name in reversed(launched):
                    try:
                        result = _docker("rm", "-f", name, allow_failure=True)
                        if result.returncode and "No such container" not in (
                            result.stderr + result.stdout
                        ):
                            cleanup_errors.append("container " + name + ": " + result.stderr[-200:])
                    except Exception as exc:
                        cleanup_errors.append("container " + name + ": " + repr(exc))
                for volume in reversed(created):
                    try:
                        result = _docker("volume", "rm", volume, allow_failure=True)
                        if result.returncode:
                            cleanup_errors.append("volume " + volume + ": " + result.stderr[-200:])
                    except Exception as exc:
                        cleanup_errors.append("volume " + volume + ": " + repr(exc))
                if cleanup_errors:
                    message = "synthetic Docker cleanup incomplete: " + "; ".join(cleanup_errors)
                    if sys.exc_info()[0] is None:
                        self.fail(message)
                    else:
                        print(message, file=sys.stderr)


if __name__ == "__main__":
    unittest.main()