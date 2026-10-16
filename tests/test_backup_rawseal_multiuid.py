"""Opt-in real Docker UID/DAC negative: raw sealer cannot bypass protected intent.

All data and volumes are synthetic, uniquely named, and removed afterward.
A compromised executor UID 50044 receives even RW mounts of every protected
directory and the *correct* independent input hashes, but never becomes UID
50043. The installed image is selected by immutable sha256 ID and never pulled.

This is not a host FD/MAP_SHARED revocation, real source quiescence, privileged
operator admission, live restore, or Kleinanzeigen platform-write test.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import unittest
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("MARK_RAW_SEALER_UID_TEST_IMAGE", "")
IMAGE_OK = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE))
PYTHON = "/opt/mark-api/venv/bin/python"
if IMAGE and not IMAGE_OK:
    raise ValueError("MARK_RAW_SEALER_UID_TEST_IMAGE must be an immutable image ID")
if os.environ.get("MARK_REQUIRE_RAW_SEALER_UID") == "1" and not IMAGE_OK:
    raise ValueError("required raw-sealer UID test has no pinned local image")


def _docker(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/usr/bin/docker", *args], capture_output=True, text=True,
        check=False, timeout=90,
    )
    if result.returncode and not allow_failure:
        raise AssertionError("isolated Docker operation failed: " + result.stderr[-900:])
    return result


_TRUSTED_SEAL = r"""
import json,os,stat
from pathlib import Path
import mark_api
from mark_api.storage import SnapshotStore
from mark_api.fence_witness import FenceLedger
from mark_api.backup_intent_authority import BackupIntentAuthority

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
result=op.seal_prepared(generation,Path('/output'),Path('/anchor'))
assert result['external_operator_intent_acknowledged'] is True
assert result['restore_authorized'] is False
assert not (Path('/anchor')/'.inflight').exists()
for path in (sqlite,witness,Path('/anchor/latest.json'),
             Path('/intent/.intent.lock'),
             Path('/intent')/(generation+'.acknowledged.json')):
    st=path.stat()
    assert st.st_uid==50043 and stat.S_IMODE(st.st_mode)==0o600,(path,st)
print('PINNED_TRUSTED_SEAL='+json.dumps({
    'sqlite_sha256':prepared['sqlite_sha256'],
    'witness_sha256':prepared['witness_sha256'],
    'head_sha256':result['head_sha256'],
},sort_keys=True),flush=True)
"""


_RELAX = r"""
import os,stat
from pathlib import Path
assert os.geteuid()==50043
for name in ('source','output','anchor','intent'):
    p=Path('/'+name)
    assert stat.S_IMODE(p.stat().st_mode)==0o700
    p.chmod(0o711)
print('TRUSTED_TEST_ROOTS_TRAVERSABLE_NO_FILE_DAC_CHANGE',flush=True)
"""


_ATTACK = r"""
import errno,os,re
from pathlib import Path
import mark_api
from mark_api.paired_backup_operator import seal_offline_pair,PairedBackupOperatorError
from mark_api.backup_intent_authority import BackupIntentAuthority

assert os.geteuid()==50044
assert Path(mark_api.__file__).as_posix().startswith('/src/mark_api/')
sha_sql=os.environ['MARK_TEST_SQLITE_SHA']
sha_witness=os.environ['MARK_TEST_WITNESS_SHA']
sha_head=os.environ['MARK_TEST_HEAD_SHA']
for value in (sha_sql,sha_witness,sha_head):
    assert re.fullmatch(r'[0-9a-f]{64}',value),value

# Positive control: UID50044 can really mutate a separately owned RW volume.
control=Path('/control/raw-sealer-attacker-probe')
control.write_bytes(b'RW_UID50044_CONFIRMED')
assert control.read_bytes()==b'RW_UID50044_CONFIRMED'

# Protected volumes are ALSO mounted RW, and the trusted harness has enabled
# traversal. Permission denial must come from actual owner-created artifacts.
for name in ('source','output','anchor','intent'):
    assert os.access('/'+name,os.X_OK),name
for item in ('/source/mark.sqlite','/source/witness.sqlite',
             '/anchor/latest.json','/intent/.intent.lock'):
    assert Path(item).stat().st_size>=0,item
    for flags in (os.O_RDONLY,os.O_WRONLY):
        try:
            fd=os.open(item,flags|os.O_NOFOLLOW|os.O_CLOEXEC)
        except OSError as exc:
            assert exc.errno in (errno.EACCES,errno.EPERM),(item,exc)
        else:
            os.close(fd)
            raise AssertionError('foreign UID opened protected file '+item)

print('ACTUAL_PRODUCT_RAW_SEALER_IMPORTED_WITH_CORRECT_SOURCE_DIGESTS',flush=True)
# Crucial: the test is not just checking a bad digest or missing code path.
# Run the exact installed Python implementation, with all valid digests and
# writable mounts. An untrusted UID must have no authority to publish HEAD.
try:
    seal_offline_pair(
        Path('/source/mark.sqlite'),Path('/source/witness.sqlite'),
        Path('/output'),Path('/anchor'),
        expected_sqlite_sha256=sha_sql,
        expected_witness_sha256=sha_witness,
        expected_previous_head_sha256=sha_head,
        generation='2'*32,
    )
except PairedBackupOperatorError as exc:
    assert 'operator directory is not privately owned' in str(exc),str(exc)
else:
    raise AssertionError('hostile UID published raw second generation')
print('RAW_SEALER_REFUSED_FOREIGN_UID_WITH_REAL_DIGESTS',flush=True)

# A forged external intent is equally unavailable to the compromised UID.
try:
    BackupIntentAuthority(Path('/intent'))
except PairedBackupOperatorError as exc:
    assert 'operator directory is not privately owned' in str(exc),str(exc)
else:
    raise AssertionError('hostile UID acquired protected operator authority')
print('OPERATOR_INTENT_AUTHORITY_DENIED_TO_EXECUTOR_UID',flush=True)

for path in ('/intent/22222222222222222222222222222222.prepared.json',
             '/anchor/.inflight','/output/22222222222222222222222222222222'):
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except OSError as exc:
        assert exc.errno in (errno.EACCES,errno.EPERM),(path,exc)
    else:
        os.close(fd)
        raise AssertionError('hostile UID created recovery authority '+path)
print('HOSTILE_UID50044_RAW_SEALER_AND_INTENT_WRITE_DENIED',flush=True)
"""


_RESTORE = r"""
import os,stat
from pathlib import Path
assert os.geteuid()==50043
for name in ('source','output','anchor','intent'):
    p=Path('/'+name)
    assert stat.S_IMODE(p.stat().st_mode)==0o711
    p.chmod(0o700)
print('TRUSTED_TEST_ROOTS_RESET_0700',flush=True)
"""


_RECHECK = r"""
import hashlib,json,os
from pathlib import Path
from mark_api.paired_restore_preflight import verify_offline_pair
from mark_api.backup_intent_authority import BackupIntentAuthority,BackupIntentError
assert os.geteuid()==50043
expected=os.environ['MARK_TEST_HEAD_SHA']
head=Path('/anchor/latest.json')
assert hashlib.sha256(head.read_bytes()).hexdigest()==expected
record=json.loads(head.read_bytes())
generation='1'*32
assert record['generation']==generation
assert not (Path('/output')/('2'*32)).exists()
assert not (Path('/anchor')/(('2'*32)+'.json')).exists()
assert not (Path('/anchor')/'.inflight').exists()
assert not (Path('/intent')/(('2'*32)+'.prepared.json')).exists()
root=Path('/output')/generation
pair=verify_offline_pair(
    root/'mark.sqlite',root/'witness.sqlite',root/'manifest.json',
    expected_manifest_sha256=record['manifest_sha256'],
)
assert pair['generation']==generation
op=BackupIntentAuthority(Path('/intent'))
try:
    op.acknowledge(generation,Path('/output'),Path('/anchor'))
except BackupIntentError as exc:
    assert 'no unconsumed' in str(exc),str(exc)
else:
    raise AssertionError('acknowledged external intent replayed')
print('TRUSTED_ANCHOR_PAIR_AND_ONE_SHOT_INTENT_UNCHANGED',flush=True)
"""


@unittest.skipUnless(IMAGE_OK, "Explicit immutable MARK_RAW_SEALER_UID_TEST_IMAGE required")
class RawSealerHostileUidDockerTests(unittest.TestCase):
    def test_hostile_uid_with_all_rw_mounts_cannot_invoke_raw_sealer(self) -> None:
        suffix = uuid4().hex[:12]
        volumes = {
            key: "mark-rawseal-" + suffix + "-" + key
            for key in ("source", "output", "anchor", "intent", "control")
        }
        created: list[str] = []
        named_containers: list[str] = []
        with tempfile.TemporaryDirectory(prefix="mark-rawseal-real-uid-") as tmp:
            root = Path(tmp)
            head = subprocess.run(
                ["git", "-C", str(REPO), "rev-parse", "--verify", "HEAD"],
                check=True, text=True, capture_output=True, timeout=10,
            ).stdout.strip()
            self.assertRegex(head, r"^[0-9a-f]{40}$")
            source_clean = subprocess.run(
                ["git", "-C", str(REPO), "status", "--porcelain", "--",
                 "src/mark_api"],
                check=True, text=True, capture_output=True, timeout=10,
            )
            self.assertFalse(source_clean.stdout.strip(),
                             "product source differs from committed HEAD")
            # Never install mutable, ignored or untracked product code into
            # the test container: exact tracked modules only.
            archive = subprocess.run(
                ["git", "-C", str(REPO), "archive", "--format=tar",
                 head, "src/mark_api"],
                check=True, capture_output=True, timeout=30,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as pack:
                pack.extractall(root, filter="data")
            product = root / "src" / "mark_api"
            self.assertTrue((product / "__init__.py").is_file())
            for path in (root, product.parent, *product.parent.rglob("*")):
                if path.is_dir():
                    path.chmod(0o755)
                elif path.is_file():
                    path.chmod(0o644)

            mounts: list[str] = []
            for key, volume in volumes.items():
                mounts += [
                    "--mount", f"type=volume,source={volume},target=/{key},volume-nocopy",
                ]
            module_bind = [
                "--mount", f"type=bind,source={product.parent},target=/src,readonly",
                "-e", "PYTHONPATH=/src",
            ]

            def run_python(label: str, uid: int, script: str,
                           *, evidence: dict[str, str] | None = None
                           ) -> subprocess.CompletedProcess[str]:
                name = "mark-rawseal-" + suffix + "-" + label
                named_containers.append(name)
                args = [
                    "run", "--rm", "--pull", "never", "--name", name,
                    "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--user", f"{uid}:{uid}", "--workdir", "/",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
                    *module_bind,
                ]
                if evidence:
                    args.extend(item for k, v in evidence.items()
                                for item in ("-e", k + "=" + v))
                return _docker(
                    *args, "--entrypoint", PYTHON, *mounts,
                    IMAGE, "-B", "-c", script,
                )

            try:
                self.assertEqual(
                    _docker("image", "inspect", "--format", "{{.Id}}",
                            IMAGE).stdout.strip(), IMAGE,
                )
                security = _docker(
                    "info", "--format", "{{json .SecurityOptions}}",
                ).stdout.lower()
                self.assertNotIn("name=userns", security)
                self.assertNotIn("name=rootless", security)
                for volume in volumes.values():
                    _docker("volume", "create", volume)
                    created.append(volume)
                    driver = _docker(
                        "volume", "inspect", "--format",
                        "{{.Driver}} {{json .Options}}", volume,
                    ).stdout.strip()
                    self.assertIn(driver, ("local null", "local {}"))
                # Root touches *only* the disposable volume directory ownership.
                # No host UID, global Docker options or services are changed.
                seed_name = "mark-rawseal-" + suffix + "-seed"
                named_containers.append(seed_name)
                _docker(
                    "run", "--rm", "--pull", "never", "--name", seed_name,
                    "--network", "none", "--read-only", "--user", "0:0",
                    "--cap-drop", "ALL", "--cap-add", "CHOWN",
                    *mounts, "--entrypoint", "/bin/sh", IMAGE, "-ec",
                    "for dir in /source /output /anchor /intent /control; do "
                    "test -z \"$(ls -A \"$dir\")\" || exit 1; "
                    "done; "
                    "chmod 0700 /source /output /anchor /intent /control;"
                    "chown 50043:50043 /source /output /anchor /intent; "
                    "chown 50044:50044 /control; "
                )
                trusted = run_python("seal", 50043, _TRUSTED_SEAL)
                matched = re.search(
                    r"^PINNED_TRUSTED_SEAL=(\{[^\n]+\})$",
                    trusted.stdout, flags=re.MULTILINE,
                )
                self.assertIsNotNone(matched, trusted.stdout)
                prepared = json.loads(matched.group(1))
                self.assertEqual(set(prepared),
                                 {"sqlite_sha256", "witness_sha256", "head_sha256"})
                self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", v)
                                    for v in prepared.values()))
                relaxed = run_python("relax", 50043, _RELAX)
                self.assertIn("TRUSTED_TEST_ROOTS_TRAVERSABLE", relaxed.stdout)
                env = {
                    "MARK_TEST_SQLITE_SHA": prepared["sqlite_sha256"],
                    "MARK_TEST_WITNESS_SHA": prepared["witness_sha256"],
                    "MARK_TEST_HEAD_SHA": prepared["head_sha256"],
                }
                try:
                    attacked = run_python("attack", 50044, _ATTACK, evidence=env)
                    self.assertIn(
                        "HOSTILE_UID50044_RAW_SEALER_AND_INTENT_WRITE_DENIED",
                        attacked.stdout,
                    )
                    self.assertIn(
                        "ACTUAL_PRODUCT_RAW_SEALER_IMPORTED_WITH_CORRECT_SOURCE_DIGESTS",
                        attacked.stdout,
                    )
                finally:
                    restored = run_python("restore", 50043, _RESTORE)
                    self.assertIn("TRUSTED_TEST_ROOTS_RESET_0700", restored.stdout)
                checked = run_python("recheck", 50043, _RECHECK, evidence=env)
                self.assertIn(
                    "TRUSTED_ANCHOR_PAIR_AND_ONE_SHOT_INTENT_UNCHANGED",
                    checked.stdout,
                )
                final_head = subprocess.run(
                    ["git", "-C", str(REPO), "rev-parse", "--verify", "HEAD"],
                    check=True, text=True, capture_output=True, timeout=10,
                ).stdout.strip()
                self.assertEqual(final_head, head)
                print("RAW_SEALER_DAC_EXACT_COMMITTED_HEAD=" + head, flush=True)
            finally:
                cleanup_errors: list[str] = []
                for name in reversed(named_containers):
                    try:
                        out = _docker("rm", "-f", name, allow_failure=True)
                        if out.returncode and "No such container" not in (
                            out.stdout + out.stderr
                        ):
                            cleanup_errors.append(name + ": " + out.stderr[-160:])
                    except Exception as exc:
                        cleanup_errors.append(name + ": " + repr(exc))
                for volume in reversed(created):
                    try:
                        out = _docker("volume", "rm", volume, allow_failure=True)
                        if out.returncode:
                            cleanup_errors.append(volume + ": " + out.stderr[-160:])
                    except Exception as exc:
                        cleanup_errors.append(volume + ": " + repr(exc))
                self.assertFalse(cleanup_errors,
                                 "synthetic Docker resources left behind: " +
                                 repr(cleanup_errors))


if __name__ == "__main__":
    unittest.main()
