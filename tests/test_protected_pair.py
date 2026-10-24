"""Fail-closed unit checks for the native protected cold paired operations.

The positive full root/UID/namespace/installed-wheel proof runs in
protected_product_uid_acceptance, never with a same-UID test bypass.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mark_api import protected_pair
from mark_api.protected_pair import ProtectedPairError


class ProtectedPairSafetyTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, "nonroot admission required")
    def test_nonroot_backup_and_restore_cannot_create_any_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nonexistent = root / "source"
            backup = root / "backup"
            restored = root / "new-store"
            with self.assertRaises(Exception):
                protected_pair.backup(nonexistent, backup)
            with self.assertRaises(Exception):
                protected_pair.restore(
                    nonexistent, restored,
                    expected_head_sha256="a" * 64,
                )
            self.assertFalse(backup.exists())
            self.assertFalse(restored.exists())

    def test_disjoint_roots_reject_self_parent_and_child(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for source, output in (
                (base, base),
                (base, base / "child"),
                (base / "child", base),
            ):
                with self.subTest(source=source, output=output):
                    with self.assertRaises(ProtectedPairError):
                        protected_pair._disjoint(source, output)

    def test_private_source_copy_validates_mode_alias_and_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uid = os.getuid()
            source = root / "source"
            source.write_bytes(b"source:" + bytes(range(100)))
            source.chmod(0o600)
            output = root / "output"
            digest = protected_pair._copy_exact(
                source, output, source_uid=uid, destination_uid=uid,
            )
            self.assertEqual(
                digest, hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(source.read_bytes(), output.read_bytes())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                protected_pair._copy_exact(
                    source, output, source_uid=uid, destination_uid=uid,
                )
            source.chmod(0o644)
            with self.assertRaises(ProtectedPairError):
                protected_pair._copy_exact(
                    source, root / "unsafe", source_uid=uid,
                    destination_uid=uid,
                )
            self.assertFalse((root / "unsafe").exists())

    def test_unresolved_media_drafts_must_never_be_dropped_from_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "media"
            media.mkdir(mode=0o700)
            marker = media / ".mark-private-media-ready-v1"
            marker.write_bytes(b"")
            marker.chmod(0o600)
            with patch.object(
                protected_pair.product, "_paths",
                return_value={"media": media},
            ), patch.object(
                protected_pair.product, "ACTOR", os.getuid(),
            ):
                protected_pair._no_actor_media(root)
                staged = media / "opaque_image.png"
                staged.write_bytes(b"synthetic-image")
                staged.chmod(0o600)
                with self.assertRaises(ProtectedPairError):
                    protected_pair._no_actor_media(root)
                staged.unlink()
                marker.write_bytes(b"not-an-empty-marker")
                with self.assertRaises(ProtectedPairError):
                    protected_pair._no_actor_media(root)

    def test_cli_refuses_missing_root_without_creating_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "missing-state"
            backup = root / "backup"
            with patch.object(
                protected_pair.product, "_root_admission",
                side_effect=ProtectedPairError("missing trusted root"),
            ):
                self.assertEqual(
                    protected_pair.main([
                        "backup", "--state-root", str(source),
                        "--backup-root", str(backup),
                    ]),
                    2,
                )
            self.assertFalse(source.exists())
            self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
