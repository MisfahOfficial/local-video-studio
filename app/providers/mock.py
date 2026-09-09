from __future__ import annotations

import hashlib
import struct
import zlib

from ..domain import GeneratedAsset, GenerationRequest, MediaKind
from .base import MediaProvider


def _chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)


def _placeholder_png(prompt: str, width: int = 640, height: int = 360) -> bytes:
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    base = digest[:3]
    rows: list[bytes] = []
    for y in range(height):
        factor = 0.65 + 0.35 * y / max(1, height - 1)
        pixel = bytes(min(255, int(channel * factor + 28)) for channel in base)
        rows.append(b"\x00" + pixel * width)
    raw = b"".join(rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


class MockImageProvider(MediaProvider):
    """Free offline provider used to test the complete workflow before spending money."""

    name = "mock"
    supported_kinds = (MediaKind.IMAGE,)

    def generate(self, request: GenerationRequest) -> GeneratedAsset:
        return GeneratedAsset(
            content=_placeholder_png(request.prompt),
            extension="png",
            provider=self.name,
            model="offline-placeholder",
            metadata={"purpose": "workflow-test"},
        )

