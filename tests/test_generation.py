from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mark_api.generation import (
    AdvertisementGenerator,
    GenerationError,
    _client,
    save_generated_image,
)
from mark_api.generation_cli import main


def _chunk(tag: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(tag + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", checksum)
    )


def valid_png() -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1024, 1024, 8, 6, 0, 0, 0)
    pixels = (b"\x00" + b"\x00" * (1024 * 4)) * 1024
    return (
        header + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(pixels))
        + _chunk(b"IEND", b"")
    )


class FakeResponses:
    def __init__(self, output_text: object):
        self.output_text = output_text
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=self.output_text)


class FakeImages:
    def __init__(self, encoded: object):
        self.encoded = encoded
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(b64_json=self.encoded)])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.responses = FakeResponses(json.dumps({
            "title": "Gut erhaltenes Fahrrad",
            "description": "Gut erhaltenes Fahrrad mit Angaben des Verkäufers.",
        }))
        self.images = FakeImages(base64.b64encode(valid_png()).decode("ascii"))
        self.fake = SimpleNamespace(responses=self.responses, images=self.images)
        self.generator = AdvertisementGenerator(self.fake)

    def test_text_draft_requires_user_request_and_retains_data(self):
        draft = self.generator.generate_text(
            "Gebrauchtes Fahrrad, frisch gewartet.", model="gpt-5-mini"
        )
        self.assertEqual(draft.title, "Gut erhaltenes Fahrrad")
        self.assertIn("Angaben", draft.description)
        self.assertEqual(draft.as_dict()["status"], "draft")
        self.assertIs(draft.as_dict()["published"], False)
        self.assertEqual(len(self.responses.calls), 1)
        self.assertEqual(self.responses.calls[0]["input"],
                         "Gebrauchtes Fahrrad, frisch gewartet.")
        self.assertIn("keine Tatsachen", self.responses.calls[0]["instructions"])
        fmt = self.responses.calls[0]["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertEqual(fmt["schema"]["required"], ["title", "description"])
        self.assertFalse(fmt["schema"]["additionalProperties"])

    def test_invalid_text_prompt_does_not_contact_provider(self):
        for prompt in ("", "   ", "x" * 4001, "ok\x00bad", "lone\ud800"):
            with self.subTest(prompt=prompt[:12]):
                with self.assertRaises(GenerationError):
                    self.generator.generate_text(prompt)
        self.assertEqual(self.responses.calls, [])

    def test_bad_provider_responses_fail_closed(self):
        cases = [
            'not a JSON object',
            '{"title": "Only title"}',
            '{"title": "", "description": "okay"}',
            '{"title": 9, "description": "not text"}',
            '{"title": "Test", "description": "x", "published": true}',
            '{"title": "bad\\nvalue", "description": "body"}',
        ]
        for output in cases:
            with self.subTest(output=output):
                self.responses.output_text = output
                with self.assertRaises(GenerationError):
                    self.generator.generate_text("Ein Artikel")
        self.assertEqual(len(self.responses.calls), len(cases))

    def test_lone_surrogate_provider_output_fails_as_generation_error(self):
        for field in ("title", "description"):
            with self.subTest(field=field):
                payload = {
                    "title": "Fahrrad",
                    "description": "Beschreibung",
                }
                payload[field] += "\ud800"
                self.responses.output_text = json.dumps(payload)
                with self.assertRaises(GenerationError):
                    self.generator.generate_text("Ein Fahrrad")

    def test_provider_failure_is_sanitized_and_not_retried(self):
        self.responses.error = RuntimeError("secret prompt and token")
        with self.assertRaisesRegex(GenerationError, "text generation request failed") as error:
            self.generator.generate_text("Ein Artikel")
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(len(self.responses.calls), 1)

    def test_image_bytes_are_png_and_not_published(self):
        png = self.generator.generate_image("Ein Fahrrad auf neutralem Hintergrund")
        self.assertEqual(png, valid_png())
        self.assertEqual(len(self.images.calls), 1)
        self.assertEqual(self.images.calls[0]["size"], "1024x1024")
        self.assertEqual(self.images.calls[0]["n"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "generated.png"
            save_generated_image(png, target)
            self.assertEqual(target.read_bytes(), png)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaisesRegex(GenerationError, "already exists"):
                save_generated_image(png, target)
            self.assertEqual(target.read_bytes(), png)

    def test_image_data_validation_is_strict(self):
        original = self.images.encoded
        invalid = ["?garbage", "A" * 26_000_001, None,
                   base64.b64encode(b"plain text").decode("ascii")]
        for encoded in invalid:
            with self.subTest(case=str(encoded)[:20]):
                self.images.encoded = encoded
                with self.assertRaises(GenerationError):
                    self.generator.generate_image("Fahrrad")
        self.images.encoded = original
        other_size = bytearray(valid_png())
        other_size[16:20] = struct.pack(">I", 2000)
        other_size[29:33] = struct.pack(">I", zlib.crc32(other_size[12:29]) & 0xFFFFFFFF)
        self.images.encoded = base64.b64encode(other_size).decode("ascii")
        with self.assertRaisesRegex(GenerationError, "dimensions"):
            self.generator.generate_image("Fahrrad")

    def test_missing_key_fails_before_optional_import(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(GenerationError, "OPENAI_API_KEY"):
                _client()

    def test_invalid_provider_model_rejected_without_call(self):
        with self.assertRaises(GenerationError):
            self.generator.generate_image("Photo", model="bad/model")
        self.assertEqual(self.images.calls, [])

    def test_cli_text_outputs_valid_draft_json_without_extra_provider(self):
        with (
            patch("mark_api.generation_cli.AdvertisementGenerator",
                  return_value=self.generator),
            patch("sys.stdout", io.StringIO()) as output,
        ):
            self.assertEqual(main(["text", "--prompt", "Fahrrad"]), 0)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["published"])
        self.assertEqual(payload["title"], "Gut erhaltenes Fahrrad")

    def test_cli_image_output_only_in_explicit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "image.png"
            with (
                patch("mark_api.generation_cli.AdvertisementGenerator",
                      return_value=self.generator),
                patch("sys.stdout", io.StringIO()) as output,
            ):
                self.assertEqual(main(["image", "--prompt", "Fahrrad",
                                       "--output", str(target)]), 0)
            self.assertEqual(target.read_bytes(), valid_png())
            self.assertFalse(json.loads(output.getvalue())["published"])
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_cli_existing_output_file_is_not_replaced_or_billed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "exists.png"
            target.write_bytes(b"original")
            with (
                patch("mark_api.generation_cli.AdvertisementGenerator",
                      return_value=self.generator),
                patch("sys.stderr", io.StringIO()) as error,
            ):
                code = main(["image", "--prompt", "Fahrrad",
                             "--output", str(target)])
            self.assertEqual(code, 2)
            self.assertIn("already exists", error.getvalue())
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(self.images.calls, [])

    def test_cli_prompt_file_utf8_is_explicit_and_size_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt_path = Path(tmp) / "input.txt"
            prompt_path.write_text("Fahrrad aus eigener Sammlung", encoding="utf-8")
            with (
                patch("mark_api.generation_cli.AdvertisementGenerator",
                      return_value=self.generator),
                patch("sys.stdout", io.StringIO()),
            ):
                self.assertEqual(main(["text", "--prompt-file", str(prompt_path)]), 0)
            self.assertEqual(self.responses.calls[-1]["input"], "Fahrrad aus eigener Sammlung")
            prompt_path.write_bytes(b"x" * (16 * 1024 + 1))
            with patch("sys.stderr", io.StringIO()) as error:
                self.assertEqual(main(["text", "--prompt-file", str(prompt_path)]), 2)
            self.assertIn("16 KiB", error.getvalue())

    def test_corrupt_image_checksum_rejected_before_output(self):
        payload = bytearray(valid_png())
        payload[-1] ^= 1  # corrupt final IEND checksum
        self.images.encoded = base64.b64encode(payload).decode("ascii")
        with self.assertRaisesRegex(GenerationError, "corrupt PNG"):
            self.generator.generate_image("Fahrrad")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bad.png"
            with self.assertRaises(GenerationError):
                save_generated_image(bytes(payload), target)
            self.assertFalse(target.exists())

    def test_crc_valid_but_incomplete_png_raster_is_rejected(self):
        # All chunks have correct CRCs; the advertised 1024x1024 RGBA
        # image contains only a handful of decompressed bytes.
        header = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 1024, 1024, 8, 6, 0, 0, 0)
        fake = (
            header + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
            + _chunk(b"IEND", b"")
        )
        self.images.encoded = base64.b64encode(fake).decode("ascii")
        with self.assertRaisesRegex(GenerationError, "incomplete PNG raster"):
            self.generator.generate_image("Fahrrad")

    def test_crc_valid_but_oversized_decoded_png_is_rejected(self):
        header = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 1024, 1024, 8, 6, 0, 0, 0)
        raw = b"\x00" * ((1 + 1024 * 4) * 1024 + 1)
        fake = (
            header + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b"")
        )
        self.images.encoded = base64.b64encode(fake).decode("ascii")
        with self.assertRaisesRegex(GenerationError, "oversized PNG raster"):
            self.generator.generate_image("Fahrrad")

    def test_valid_crc_and_pixels_with_invalid_row_filter_are_rejected(self):
        header = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 1024, 1024, 8, 6, 0, 0, 0)
        raster = b"\x05" + b"\x00" * ((1 + 1024 * 4) * 1024 - 1)
        fake = (
            header + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raster))
            + _chunk(b"IEND", b"")
        )
        self.images.encoded = base64.b64encode(fake).decode("ascii")
        with self.assertRaisesRegex(GenerationError, "row filter"):
            self.generator.generate_image("Fahrrad")

    def test_generate_text_rejects_form_overflow_without_silent_truncation(self):
        self.responses.output_text = json.dumps({
            "title": "T" * 66, "description": "okay",
        })
        with self.assertRaisesRegex(GenerationError, "draft lengths"):
            self.generator.generate_text("Kleines Fahrrad")
        self.responses.output_text = json.dumps({
            "title": "Okay", "description": "a" * 4001,
        })
        with self.assertRaisesRegex(GenerationError, "draft lengths"):
            self.generator.generate_text("Kleines Fahrrad")

    def test_symlink_target_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "other.txt"
            original.write_bytes(b"original")
            link = Path(tmp) / "image.png"
            link.symlink_to(original)
            with self.assertRaisesRegex(GenerationError, "already exists"):
                save_generated_image(valid_png(), link)
            self.assertTrue(link.is_symlink())
            self.assertEqual(original.read_bytes(), b"original")

    def test_sdk_has_no_automatic_retry(self):
        calls = []
        class FakeOpenAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-only"}),
            patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FakeOpenAI)}),
        ):
            client = _client()
        self.assertIsInstance(client, FakeOpenAI)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_retries"], 0)

    def test_import_and_help_do_not_import_optional_sdk_or_platform(self):
        code = (
            "import sys; import mark_api.generation_cli; "
            "blocked=[name for name in sys.modules "
            "if name=='openai' or name.startswith('mark_api.adapters.')]; "
            "print(blocked); raise SystemExit(bool(blocked))"
        )
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=repo, capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONPATH": str(repo / "src")},
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")
        with patch("sys.stdout", io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exc:
                main(["--help"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("never publishes", output.getvalue().lower())


if __name__ == "__main__":
    unittest.main()