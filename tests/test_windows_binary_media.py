"""Ensure Windows CRT text mode never alters binary customer media files."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from mark_api.private_web_media import (
    PrivateWebMediaHandleStore,
    _prepare_local_media,
)
from mark_api.private_web_media_verify import _read_local_source


@unittest.skipUnless(os.name == "nt", "native Windows media only")
class WindowsMediaBinaryRoundtripTests(unittest.TestCase):
    def test_png_byte_exact_across_staging_restart_browser_copy_and_readback(self):
        from PIL import Image

        picture = io.BytesIO()
        with Image.new("RGB", (5, 4), (31, 151, 227)) as image:
            image.save(picture, "PNG")
        source_bytes = picture.getvalue()
        self.assertTrue(source_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"\r\n", source_bytes)

        protected: set[str] = set()
        profile = os.environ["LOCALAPPDATA"]
        with tempfile.TemporaryDirectory(prefix="kleinanzeiger-media-", dir=profile) as root:
            store_path = Path(root) / "handles"
            store = PrivateWebMediaHandleStore(
                directory=store_path,
                protected_refs=lambda: frozenset(protected),
            )
            ref = store.stage_media("customer.png", source_bytes)
            protected.add(ref)
            original_path = Path(store.resolve((ref,))[0].path)
            self.assertEqual(original_path.read_bytes(), source_bytes)
            store.close()

            restarted = PrivateWebMediaHandleStore(
                directory=store_path,
                protected_refs=lambda: frozenset(protected),
            )
            verified_source, = restarted.resolve((ref,))
            self.assertEqual(Path(verified_source.path).read_bytes(), source_bytes)
            self.assertEqual(_read_local_source(verified_source), source_bytes)

            # Windows os.lstat(path) and os.fstat(descriptor) can expose
            # different metadata even for the same underlying file.
            before = os.lstat(verified_source.path)
            descriptor = os.open(
                verified_source.path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
            try:
                opened = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            identity_fields = (
                "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"
            )
            differences = [
                name for name in identity_fields
                if getattr(before, name) != getattr(opened, name)
            ]
            print("WINDOWS_MEDIA_STAT_FSTAT_DIFFERENT_FIELDS", differences, flush=True)
            prepared = _prepare_local_media((verified_source,))
            try:
                self.assertEqual(len(prepared.paths), 1)
                self.assertEqual(Path(prepared.paths[0]).read_bytes(), source_bytes)
                self.assertEqual(prepared.files[0].size_bytes, len(source_bytes))
                self.assertEqual(
                    _read_local_source(type(verified_source)(prepared.paths[0])),
                    source_bytes,
                )
            finally:
                prepared.close()
            protected.clear()
            restarted.discard((ref,))
            restarted.close()


if __name__ == "__main__":
    unittest.main()