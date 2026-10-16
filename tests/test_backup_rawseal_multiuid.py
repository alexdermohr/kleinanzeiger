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
from unittest.mock import patch


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


def _docker_reports_missing(
    result: subprocess.CompletedProcess[str], kind: str, name: str,
) -> bool:
    """Only exact Docker not-found replies establish resource absence."""
    if result.returncode == 0 or result.stdout.strip() not in ("", "[]"):
        return False
    if kind == "volume":
        accepted = (
            f"Error response from daemon: get {name}: no such volume",
            f"Error: No such volume: {name}",
        )
    elif kind == "container":
        accepted = (
            f"Error response from daemon: No such container: {name}",
            f"Error: No such container: {name}",
            f"Error: No such object: {name}",
        )
    else:
        raise ValueError("unknown Docker resource kind")
    return result.stderr.strip() in accepted


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


_DAC_PROBE = r"""
import errno,os
from pathlib import Path
assert os.geteuid()==50044

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

print('ROOTS_0711_FILE_DAC_DENIED_WITH_RW_POSITIVE_CONTROL',flush=True)
"""


_ATTACK = r"""
import errno,os,re,stat
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

# The raw API must be rejected when the actual trusted private roots
# have their original owner/mode (50043/0700), not merely because the trusted
# harness temporarily relaxed them to 0711 for direct file-DAC probing.
for name in ('source','output','anchor','intent'):
    info=Path('/'+name).stat()
    assert info.st_uid==50043,(name,info)
    assert stat.S_IMODE(info.st_mode)==0o700,(name,info)
assert Path('/control/raw-sealer-attacker-probe').read_bytes()==b'RW_UID50044_CONFIRMED'

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

            label_key = "mark.a6.rawseal.run"
            label_value = label_key + "=" + suffix
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
                prior = _docker("container", "inspect", name, allow_failure=True)
                self.assertTrue(
                    _docker_reports_missing(prior, 'container', name),
                    'synthetic container name not proven absent or Docker unavailable',
                )
                named_containers.append(name)  # before a potentially uncertain Docker run
                args = [
                    "run", "--rm", "--pull", "never", "--name", name,
                    "--label", label_value,
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
                    prior = _docker("volume", "inspect", volume,
                                    allow_failure=True)
                    self.assertTrue(
                        _docker_reports_missing(prior, 'volume', volume),
                        'synthetic volume name not proven absent or Docker unavailable',
                    )
                    # Register the unique name BEFORE Docker starts. A daemon
                    # may have created it even when the CLI later times out.
                    created.append(volume)
                    _docker("volume", "create", "--label", label_value, volume)
                    driver = _docker(
                        "volume", "inspect", "--format",
                        '{{.Driver}} {{json .Options}} '
                        '{{index .Labels "mark.a6.rawseal.run"}}', volume,
                    ).stdout.strip()
                    self.assertIn(driver,
                                  ("local null " + suffix, "local {} " + suffix))
                # Root touches *only* the disposable volume directory ownership.
                # No host UID, global Docker options or services are changed.
                seed_name = "mark-rawseal-" + suffix + "-seed"
                prior = _docker("container", "inspect", seed_name,
                                allow_failure=True)
                self.assertTrue(
                    _docker_reports_missing(prior, 'container', seed_name),
                    'synthetic seed container not proven absent or Docker unavailable',
                )
                named_containers.append(seed_name)  # before uncertain run
                _docker(
                    "run", "--rm", "--pull", "never", "--name", seed_name,
                    "--label", label_value,
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
                    direct = run_python("attack-dac", 50044, _DAC_PROBE)
                    self.assertIn(
                        "ROOTS_0711_FILE_DAC_DENIED_WITH_RW_POSITIVE_CONTROL",
                        direct.stdout,
                    )
                finally:
                    restored = run_python("restore", 50043, _RESTORE)
                    self.assertIn("TRUSTED_TEST_ROOTS_RESET_0700", restored.stdout)
                attacked = run_python("attack-raw", 50044, _ATTACK, evidence=env)
                self.assertIn(
                    "HOSTILE_UID50044_RAW_SEALER_AND_INTENT_WRITE_DENIED",
                    attacked.stdout,
                )
                self.assertIn(
                    "ACTUAL_PRODUCT_RAW_SEALER_IMPORTED_WITH_CORRECT_SOURCE_DIGESTS",
                    attacked.stdout,
                )
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

                def was_created_by_this_test(kind: str, name: str) -> bool:
                    # Never remove a pre-existing/foreign resource after a
                    # create/launch timeout, collision or daemon error.
                    expr = ('{{index .Config.Labels "mark.a6.rawseal.run"}}'
                            if kind == "container"
                            else '{{index .Labels "mark.a6.rawseal.run"}}')
                    observed = _docker(kind, "inspect", "--format", expr,
                                       name, allow_failure=True)
                    if observed.returncode:
                        if _docker_reports_missing(observed, kind, name):
                            return False  # no creation or --rm already ran
                        cleanup_errors.append(
                            name + ": ownership inspect failed, not 404: " +
                            (observed.stdout + observed.stderr)[-180:]
                        )
                        return False
                    if observed.stdout.strip() != suffix:
                        cleanup_errors.append(
                            name + ": ownership label mismatch; refuse cleanup"
                        )
                        return False
                    return True

                def confirm_removed(kind: str, name: str) -> None:
                    # A --rm container can disappear between inspect and rm.
                    # Re-inspection accepts only an exact daemon not-found.
                    final = _docker(kind, "inspect", name, allow_failure=True)
                    if not _docker_reports_missing(final, kind, name):
                        cleanup_errors.append(
                            name + ": not proven removed: " +
                            (final.stdout + final.stderr)[-180:]
                        )

                for name in reversed(named_containers):
                    try:
                        if not was_created_by_this_test("container", name):
                            continue
                        _docker("rm", "-f", name, allow_failure=True)
                        confirm_removed("container", name)
                    except Exception as exc:
                        cleanup_errors.append(name + ": " + repr(exc))
                for volume in reversed(created):
                    try:
                        if not was_created_by_this_test("volume", volume):
                            continue
                        _docker("volume", "rm", volume, allow_failure=True)
                        confirm_removed("volume", volume)
                    except Exception as exc:
                        cleanup_errors.append(volume + ": " + repr(exc))
                self.assertFalse(cleanup_errors,
                                 "unconfirmed cleanup of synthetic Docker resources: " +
                                 repr(cleanup_errors))


    def test_created_volume_is_cleaned_when_docker_client_times_out(self) -> None:
        # A CLI TimeoutExpired does not mean the daemon failed to create the
        # requested volume. Reproduce the effect *after* the first create.
        original = _docker
        created: str | None = None
        injected = False

        def lose_create_ack(*args: str, **kwargs: object
                            ) -> subprocess.CompletedProcess[str]:
            nonlocal created, injected
            if args[:2] == ("volume", "create") and not injected:
                response = original(*args, **kwargs)
                self.assertEqual(response.returncode, 0)
                created = args[-1]
                injected = True
                raise subprocess.TimeoutExpired(
                    cmd=["/usr/bin/docker", *args], timeout=90,
                )
            return original(*args, **kwargs)

        try:
            with patch(__name__ + "._docker", side_effect=lose_create_ack):
                nested = type(self)(
                    "test_hostile_uid_with_all_rw_mounts_cannot_invoke_raw_sealer",
                )
                result = unittest.TestResult()
                nested.run(result)
            self.assertTrue(injected)
            self.assertIsNotNone(created)
            self.assertEqual(len(result.errors), 1, result.errors)
            self.assertFalse(result.failures, result.failures)
            self.assertIn("TimeoutExpired", result.errors[0][1])
            absent = original("volume", "inspect", created,
                              allow_failure=True)
            self.assertTrue(
                _docker_reports_missing(absent, "volume", created),
                "uncertain created volume not proven absent or Docker unreachable",
            )
            print("DOCKER_VOLUME_CREATE_TIMEOUT_CLEANED", flush=True)
        finally:
            # A future broken cleanup must not leak even this negative test.
            if created is not None:
                owned = original(
                    "volume", "inspect", "--format",
                    '{{index .Labels "mark.a6.rawseal.run"}}',
                    created, allow_failure=True,
                )
                if owned.returncode == 0:
                    expected_suffix = created.split("-")[2]
                    self.assertEqual(
                        owned.stdout.strip(), expected_suffix,
                        "refuse to remove a non-test Docker volume",
                    )
                    original("volume", "rm", created)
                    self.fail("orphaned test volume required emergency cleanup")


    def test_docker_missing_evidence_rejects_daemon_failures(self) -> None:
        name = "mark-rawseal-fake-test-resource"
        cases = (
            ("volume", f"Error response from daemon: get {name}: no such volume", True),
            ("container", f"Error response from daemon: No such container: {name}", True),
            ("volume", f"Error response from daemon: 500: no such volume {name}", False),
            ("container", "permission denied while reaching Docker daemon", False),
            ("container", f"Error response from daemon: No such container: {name}-other", False),
            ("volume", "unexpected daemon timeout", False),
        )
        for kind, message, absent in cases:
            with self.subTest(kind=kind, message=message):
                result = subprocess.CompletedProcess(
                    args=["docker", kind, "inspect", name],
                    returncode=1, stdout="[]", stderr=message,
                )
                self.assertEqual(_docker_reports_missing(result, kind, name), absent)
                success = subprocess.CompletedProcess(
                    args=result.args, returncode=0, stdout="", stderr=message,
                )
                self.assertFalse(_docker_reports_missing(success, kind, name))

    def test_auto_rm_container_after_inspect_does_not_fail_cleanup(self) -> None:
        # Synchronously run a disposable --rm seed; its Docker container is
        # already gone at the end. Inject an outdated positive inspect result
        # once during cleanup to emulate auto-removal before docker rm -f.
        original = _docker
        seed: str | None = None
        observed_positive = False
        attempted_remove = False

        def just_exited_after_inspection(*args: str, **kwargs: object
                                          ) -> subprocess.CompletedProcess[str]:
            nonlocal seed, observed_positive, attempted_remove
            if args and args[0] == "run" and "--name" in args:
                candidate = args[args.index("--name") + 1]
                result = original(*args, **kwargs)
                if candidate.endswith("-seed"):
                    seed = candidate
                return result
            if (seed is not None and args[:2] == ("container", "inspect")
                    and args[-1] == seed and "--format" in args
                    and not observed_positive):
                observed_positive = True
                suffix = seed.split("-")[2]
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=suffix, stderr="",
                )
            if seed is not None and args[:2] == ("rm", "-f") and args[-1] == seed:
                attempted_remove = True
                # Force the documented losing race even if this local Docker
                # version returns 0 for an already auto-removed --rm seed:
                # real post-removal inspect is absent, rm reports a synthetic
                # nonzero "No such container", and only final strict 404 wins.
                original(*args, **kwargs)
                absent = original("container", "inspect", seed,
                                  allow_failure=True)
                self.assertTrue(_docker_reports_missing(absent, "container", seed))
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="",
                    stderr="Error response from daemon: No such container: " + seed,
                )
            return original(*args, **kwargs)

        with patch(__name__ + "._docker", side_effect=just_exited_after_inspection):
            nested = type(self)(
                "test_hostile_uid_with_all_rw_mounts_cannot_invoke_raw_sealer",
            )
            result = unittest.TestResult()
            nested.run(result)
        self.assertTrue(observed_positive)
        self.assertTrue(attempted_remove)
        self.assertFalse(result.errors, result.errors)
        self.assertFalse(result.failures, result.failures)


    def test_create_timeout_rejects_inspection_500_not_false_404(self) -> None:
        # A daemon 500 after a successfully cleaned create is not evidence of
        # absence. This tests the full post-timeout verification contract, not
        # just the stand-alone parser.
        original = _docker
        created: str | None = None
        injected = False
        volume_removed = False
        cleanup_inspect_succeeded = False

        def wrong_daemon_reply(*args: str, **kwargs: object
                                ) -> subprocess.CompletedProcess[str]:
            nonlocal created, injected, volume_removed, cleanup_inspect_succeeded
            if args[:2] == ("volume", "create") and created is None:
                outcome = original(*args, **kwargs)
                created = args[-1]
                return outcome
            if (created is not None and args[:2] == ("volume", "rm")
                    and args[-1] == created):
                result = original(*args, **kwargs)
                volume_removed = True
                return result
            if (created is not None and args == ("volume", "inspect", created)
                    and volume_removed):
                if not cleanup_inspect_succeeded:
                    # The nested normal cleanup must prove a real 404 first.
                    real = original(*args, **kwargs)
                    self.assertTrue(
                        _docker_reports_missing(real, "volume", created),
                    )
                    cleanup_inspect_succeeded = True
                    return real
                if not injected:
                    # Only the later *test verdict* inspect receives 500.
                    injected = True
                    return subprocess.CompletedProcess(
                        args=args, returncode=1, stdout="[]",
                        stderr="Error response from daemon: 500 Internal Server Error",
                    )
            return original(*args, **kwargs)

        with patch(__name__ + "._docker", side_effect=wrong_daemon_reply):
            nested = type(self)(
                "test_created_volume_is_cleaned_when_docker_client_times_out",
            )
            result = unittest.TestResult()
            nested.run(result)
        self.assertTrue(volume_removed)
        self.assertTrue(cleanup_inspect_succeeded)
        self.assertTrue(injected)
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.failures), 1, result.failures)
        self.assertIn("not proven absent", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
