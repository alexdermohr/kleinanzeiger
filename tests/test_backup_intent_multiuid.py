"""Opt-in real Linux/Docker DAC test for the offline backup-intent operator.

Only uniquely named, disposable Docker volumes and synthetic SQLite/Witness
images are touched. This is not a live service, platform-write, historic mmap,
source-quiescence, or production host acceptance test.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
IMAGE = __import__("os").environ.get("MARK_INTENT_MULTIUID_TEST_IMAGE", "")
IMAGE_OK = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE))
PYTHON = "/opt/mark-api/venv/bin/python"


def _docker(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/usr/bin/docker", *args], check=False, capture_output=True,
        text=True, timeout=60,
    )
    if result.returncode and not allow_failure:
        raise AssertionError("isolated Docker operation failed: " + result.stderr[-900:])
    return result


_SEAL = """
import os
from pathlib import Path
import mark_api
from mark_api.backup_intent_authority import BackupIntentAuthority
from mark_api.fence_witness import FenceLedger
from mark_api.storage import SnapshotStore
assert os.geteuid()==50043
assert Path(mark_api.__file__).as_posix().startswith('/src/mark_api/')
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
assert (Path('/intent')/(generation+'.acknowledged.json')).is_file()
print('REAL_UID50043_OFFLINE_INTENT_SEALED',flush=True)
"""

_ATTACK = """
import errno,os
from pathlib import Path
assert os.geteuid()==50044
# The test deliberately supplies writable volume mounts to the attacker.
# Real DAC, not volume read-only flags, must deny all four protected paths.
for item in (
    '/intent/.intent.lock',
    '/intent/'+'1'*32+'.acknowledged.json',
    '/source/mark.sqlite',
    '/source/witness.sqlite',
    '/output/'+'1'*32+'/mark.sqlite',
    '/anchor/latest.json',
):
    for flags in (os.O_RDONLY,os.O_WRONLY):
        try:
            fd=os.open(item,flags|os.O_CLOEXEC|os.O_NOFOLLOW)
        except OSError as exc:
            assert exc.errno in (errno.EACCES,errno.EPERM),(item,flags,exc)
        else:
            os.close(fd)
            raise AssertionError('hostile UID accessed protected path: '+item)
    try:
        Path(item).read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError('hostile UID read protected image: '+item)
print('REAL_UID50044_CANNOT_READ_OR_MUTATE_OPERATOR_PAIRS',flush=True)
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
head=json.loads(head_bytes)
assert head['generation']==generation
checked=verify_offline_pair(
    root/'mark.sqlite',root/'witness.sqlite',root/'manifest.json',
    expected_manifest_sha256=head['manifest_sha256'],
)
assert checked['generation']==generation
op=BackupIntentAuthority(Path('/intent'))
for action in (
    lambda:op.prepare(generation,Path('/source/mark.sqlite'),
        Path('/source/witness.sqlite'),previous_head_sha256=None),
    lambda:op.acknowledge(generation,Path('/output'),anchor),
):
    try:action()
    except BackupIntentError:pass
    else:raise AssertionError('original acknowledged intent was replayed')
assert not (anchor/'.inflight').exists()
assert (Path('/intent')/(generation+'.acknowledged.json')).is_file()
print('REAL_UID50043_CHAIN_INTACT_AND_INTENT_SINGLE_USE',flush=True)
"""


@unittest.skipUnless(IMAGE_OK, "Set immutable MARK_INTENT_MULTIUID_TEST_IMAGE")
class BackupIntentMultiUidDockerTests(unittest.TestCase):
    def test_hostile_executor_cannot_access_independent_offline_intent(self) -> None:
        suffix = uuid4().hex[:12]
        names = {name: "mark-intent-" + suffix + "-" + name
                 for name in ("source", "output", "anchor", "intent")}
        created: list[str] = []
        with tempfile.TemporaryDirectory(prefix="mark-intent-realuid-") as temp:
            package = Path(temp) / "src" / "mark_api"
            shutil.copytree(ROOT / "src" / "mark_api", package,
                            ignore=shutil.ignore_patterns("__pycache__"))
            for item in (Path(temp), package.parent, *package.parent.rglob("*")):
                if item.is_dir():
                    item.chmod(0o755)
                elif item.is_file():
                    item.chmod(0o644)
            volume_mounts: list[str] = []
            for name, volume in names.items():
                volume_mounts.extend([
                    "--mount", f"type=volume,source={volume},target=/{name}",
                ])
            source_mount = [
                "--mount",
                f"type=bind,source={package.parent},target=/src,readonly",
            ]
            trusted = [
                "run", "--rm", "--pull", "never", "--network", "none",
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--user", "50043:50043", *source_mount,
                "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "--workdir", "/", "--entrypoint", PYTHON,
                *volume_mounts, IMAGE, "-B", "-c",
            ]
            hostile = [
                "run", "--rm", "--pull", "never", "--network", "none",
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--user", "50044:50044", "--workdir", "/",
                "--entrypoint", PYTHON, *volume_mounts, IMAGE, "-B", "-c",
            ]
            try:
                for volume in names.values():
                    _docker("volume", "create", volume)
                    created.append(volume)
                seed = _docker(
                    "run", "--rm", "--pull", "never", "--network", "none",
                    "--read-only", "--user", "0:0", "--cap-drop", "ALL",
                    "--cap-add", "CHOWN", *volume_mounts,
                    "--entrypoint", "/bin/sh", IMAGE, "-ec",
                    "chmod 0700 /source /output /anchor /intent; "
                    "chown 50043:50043 /source /output /anchor /intent",
                )
                self.assertEqual(seed.returncode, 0)
                first = _docker(*trusted, _SEAL)
                self.assertIn("REAL_UID50043_OFFLINE_INTENT_SEALED", first.stdout)
                attack = _docker(*hostile, _ATTACK)
                self.assertIn(
                    "REAL_UID50044_CANNOT_READ_OR_MUTATE_OPERATOR_PAIRS",
                    attack.stdout,
                )
                verify = _docker(*trusted, _RECHECK)
                self.assertIn(
                    "REAL_UID50043_CHAIN_INTACT_AND_INTENT_SINGLE_USE",
                    verify.stdout,
                )
            finally:
                for volume in reversed(created):
                    result = _docker("volume", "rm", volume, allow_failure=True)
                    self.assertEqual(result.returncode, 0, result.stderr[-400:])


if __name__ == "__main__":
    unittest.main()