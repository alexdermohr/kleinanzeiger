"""Explicit, draft-only advertisement text and image generation.

The optional OpenAI SDK is imported *only* when a generation request is made.
This module neither reads credentials from files nor imports platform adapters.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAX_PROMPT_CHARS = 4000
_MAX_TEXT_CHARS = 12000
_MAX_IMAGE_B64_CHARS = 26_000_000
_MAX_IMAGE_BYTES = 18_000_000
_IMAGE_SIZE = (1024, 1024)
_TEXT_INSTRUCTIONS = (
    "Erstelle einen deutschen Kleinanzeigen-Entwurf aus den vom Nutzer "
    "gegebenen Fakten. Antworte ausschließlich als JSON-Objekt mit genau "
    "den Feldern title und description. Der Titel soll prägnant sein, "
    "die Beschreibung sachlich und gut lesbar. Erfinde keine Tatsachen "
    "zu Zustand, Marke, Größe, Preis, Versand, Garantie oder Besitz. "
    "Gib keine Kontaktinformationen hinzu. Veröffentliche nichts."
)
_TEXT_FORMAT = {
    "format": {
        "type": "json_schema",
        "name": "mark_advertisement_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "description"],
            "additionalProperties": False,
        },
    }
}


class GenerationError(RuntimeError):
    """Invalid input, missing provider or untrusted provider output."""


@dataclass(frozen=True, slots=True)
class AdvertisementDraft:
    title: str
    description: str

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "status": "draft",
            "published": False,
        }


def _validated_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise GenerationError("prompt must be text")
    value = prompt.strip()
    if not 1 <= len(value) <= _MAX_PROMPT_CHARS:
        raise GenerationError("prompt must be between 1 and 4000 characters")
    if any((ord(character) < 32 and character not in "\n\t") or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise GenerationError("prompt contains unsupported control characters")
    return value


def _validated_model(model: str) -> str:
    if not isinstance(model, str) or not 1 <= len(model) <= 100:
        raise GenerationError("model must be a non-empty bounded string")
    if not all(char.isascii() and (char.isalnum() or char in "-._") for char in model):
        raise GenerationError("model contains unsupported characters")
    return model


def _client() -> Any:
    if not os.environ.get("OPENAI_API_KEY"):
        raise GenerationError("OPENAI_API_KEY is required for generation")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise GenerationError(
            "optional generation dependency missing; install mark-api[generation]"
        ) from exc
    # Generations cost money; an uncertain failure must never prompt an
    # automatic second generation. Explicit user retry is a new request.
    try:
        return OpenAI(max_retries=0, timeout=120.0)
    except Exception:
        raise GenerationError("generation provider initialization failed") from None


class AdvertisementGenerator:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _provider(self) -> Any:
        if self._client is None:
            self._client = _client()
        return self._client

    def generate_text(
        self, prompt: str, *, model: str = "gpt-5-mini"
    ) -> AdvertisementDraft:
        clean_prompt = _validated_prompt(prompt)
        clean_model = _validated_model(model)
        try:
            result = self._provider().responses.create(
                model=clean_model,
                instructions=_TEXT_INSTRUCTIONS,
                input=clean_prompt,
                text=_TEXT_FORMAT,
            )
        except GenerationError:
            raise
        except Exception:
            raise GenerationError("text generation request failed") from None

        output = getattr(result, "output_text", None)
        if not isinstance(output, str) or len(output) > _MAX_TEXT_CHARS:
            raise GenerationError("text provider returned invalid output")
        try:
            data = json.loads(output)
        except (ValueError, TypeError):
            raise GenerationError("text provider returned invalid JSON") from None
        if not isinstance(data, dict) or set(data) != {"title", "description"}:
            raise GenerationError("text provider returned unexpected fields")
        title = data["title"]
        description = data["description"]
        if not isinstance(title, str) or not isinstance(description, str):
            raise GenerationError("text provider returned non-text fields")
        title = title.strip()
        description = description.strip()
        if not 1 <= len(title.encode("utf-16-le", "surrogatepass")) // 2 <= 65 or not 1 <= len(description.encode("utf-16-le", "surrogatepass")) // 2 <= 4000:
            raise GenerationError("text provider returned invalid draft lengths")
        if any((ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF) for c in title) or any(
            (ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF
            for c in description
        ):
            raise GenerationError("text provider returned control characters")
        return AdvertisementDraft(title=title, description=description)

    def generate_image(
        self, prompt: str, *, model: str = "gpt-image-1"
    ) -> bytes:
        clean_prompt = _validated_prompt(prompt)
        clean_model = _validated_model(model)
        try:
            result = self._provider().images.generate(
                model=clean_model,
                prompt=clean_prompt,
                n=1,
                size="1024x1024",
            )
        except GenerationError:
            raise
        except Exception:
            raise GenerationError("image generation request failed") from None

        items = getattr(result, "data", None)
        if not isinstance(items, (list, tuple)) or len(items) != 1:
            raise GenerationError("image provider returned invalid response")
        encoded = getattr(items[0], "b64_json", None)
        if not isinstance(encoded, str) or not 1 <= len(encoded) <= _MAX_IMAGE_B64_CHARS:
            raise GenerationError("image provider returned invalid image data")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise GenerationError("image provider returned invalid base64") from None
        _validate_png_payload(payload)
        return payload


def _validate_png_payload(data: bytes) -> None:
    """Fully decode one bounded, non-interlaced 1024px RGB/RGBA PNG.

    CRC and IHDR alone do not establish that the advertised image has a
    complete decompressed raster. Strictly bound zlib output and scanline
    filters before accepting provider bytes or writing draft files.
    """
    if not isinstance(data, bytes) or not 45 <= len(data) <= _MAX_IMAGE_BYTES:
        raise GenerationError("image provider returned invalid image size")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise GenerationError("image provider returned non-PNG data")

    offset = 8
    chunk_count = 0
    channels = None
    idat_chunks: list[bytes] = []
    idat_total = 0
    idat_finished = False
    seen_end = False
    while offset < len(data):
        chunk_count += 1
        if chunk_count > 2048 or offset + 12 > len(data):
            raise GenerationError("image provider returned incomplete PNG")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        if length > _MAX_IMAGE_BYTES or length + 12 > len(data) - offset:
            raise GenerationError("image provider returned invalid PNG chunk")
        tag = data[offset + 4:offset + 8]
        if len(tag) != 4 or not all(
            65 <= byte <= 90 or 97 <= byte <= 122 for byte in tag
        ):
            raise GenerationError("image provider returned invalid PNG chunk")
        end = offset + length + 12
        chunk = data[offset + 8:end - 4]
        expected_crc = struct.unpack(">I", data[end - 4:end])[0]
        if (zlib.crc32(tag + chunk) & 0xFFFFFFFF) != expected_crc:
            raise GenerationError("image provider returned corrupt PNG")
        if offset == 8:
            if tag != b"IHDR" or length != 13:
                raise GenerationError("image provider returned invalid PNG header")
            width, height, bit_depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk)
            )
            if (width, height) != _IMAGE_SIZE:
                raise GenerationError("image provider returned unexpected dimensions")
            if bit_depth != 8 or color not in (2, 6) or (
                compression, filtering, interlace
            ) != (0, 0, 0):
                raise GenerationError("image provider returned unsupported PNG encoding")
            channels = 3 if color == 2 else 4
        elif tag == b"IHDR":
            raise GenerationError("image provider returned duplicate PNG header")
        elif tag == b"IDAT":
            if idat_finished:
                raise GenerationError("image provider returned noncontiguous IDAT")
            idat_total += len(chunk)
            if idat_total > _MAX_IMAGE_BYTES:
                raise GenerationError("image provider returned oversized IDAT")
            idat_chunks.append(chunk)
        elif tag == b"IEND":
            if length != 0 or end != len(data):
                raise GenerationError("image provider returned invalid PNG ending")
            seen_end = True
            break
        else:
            if tag in {b"acTL", b"fcTL", b"fdAT"} or (tag[0] & 0x20) == 0:
                raise GenerationError("image provider returned unsupported PNG chunk")
        if idat_chunks and tag != b"IDAT":
            idat_finished = True
        offset = end
    if not (idat_chunks and seen_end and channels is not None):
        raise GenerationError("image provider returned incomplete PNG")

    # One filter byte plus all pixel channel bytes for every raster row.
    row_bytes = 1 + _IMAGE_SIZE[0] * channels
    expected_raster = row_bytes * _IMAGE_SIZE[1]
    decoder = zlib.decompressobj()
    raster = bytearray()
    try:
        for chunk in idat_chunks:
            remaining = expected_raster - len(raster)
            block = decoder.decompress(chunk, remaining + 1)
            raster.extend(block)
            if len(raster) > expected_raster or decoder.unconsumed_tail:
                raise GenerationError("image provider returned oversized PNG raster")
        raster.extend(decoder.flush(expected_raster - len(raster) + 1))
    except (zlib.error, ValueError):
        raise GenerationError("image provider returned invalid PNG raster") from None
    if (
        len(raster) != expected_raster or not decoder.eof
        or decoder.unused_data or decoder.unconsumed_tail
    ):
        raise GenerationError("image provider returned incomplete PNG raster")
    if any(raster[index] not in range(5) for index in range(0, expected_raster, row_bytes)):
        raise GenerationError("image provider returned invalid PNG row filter")


def save_generated_image(data: bytes, output: Path) -> None:
    """Create a local draft image securely without clobbering any file."""
    if not isinstance(output, Path) or not isinstance(data, bytes):
        raise GenerationError("invalid image destination or data")
    _validate_png_payload(data)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError:
        raise GenerationError("image destination already exists") from None
    except OSError:
        raise GenerationError("image destination is unavailable") from None
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            current = output.lstat()
            if (current.st_dev, current.st_ino) == (
                identity.st_dev, identity.st_ino
            ):
                output.unlink()
        except OSError:
            pass
        raise GenerationError("image write failed") from None