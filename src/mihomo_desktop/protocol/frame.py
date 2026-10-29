"""Frame header for the Windows service pipe (docs/architecture.md §4.2).

Twelve bytes, little-endian: magic, u16 protocol version, u16 kind, u32 payload length.
macOS provider messages carry the payload without a header. Mirrors src/core/internal/frame.
"""

from __future__ import annotations

import asyncio
import struct

from mihomo_desktop.product import FRAME_MAGIC, PROTOCOL_VERSION

HEADER = struct.Struct("<4sHHI")
HEADER_SIZE = HEADER.size
KIND_REQUEST = 1
KIND_RESPONSE = 2
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class FrameError(ValueError):
    pass


def _limit(kind: int) -> int:
    return MAX_REQUEST_BYTES if kind == KIND_REQUEST else MAX_RESPONSE_BYTES


def encode(kind: int, payload: bytes) -> bytes:
    if not 0 < len(payload) <= _limit(kind):
        raise FrameError(f"control frame payload of {len(payload)} bytes is out of range")
    return HEADER.pack(FRAME_MAGIC, PROTOCOL_VERSION, kind, len(payload)) + payload


def parse_header(header: bytes, expected_kind: int) -> int:
    """Return the payload length after validating the header against expected_kind."""
    if len(header) != HEADER_SIZE:
        raise FrameError("truncated control frame header")
    magic, version, kind, length = HEADER.unpack(header)
    if magic != FRAME_MAGIC:
        raise FrameError("bad control frame magic")
    if version != PROTOCOL_VERSION:
        raise FrameError(f"control protocol version {version}, want {PROTOCOL_VERSION}")
    if kind != expected_kind:
        raise FrameError(f"control frame kind {kind}, want {expected_kind}")
    if not 0 < length <= _limit(expected_kind):
        raise FrameError(f"control frame payload length {length} is out of range")
    return length


async def read_frame(reader: asyncio.StreamReader, expected_kind: int) -> bytes:
    length = parse_header(await reader.readexactly(HEADER_SIZE), expected_kind)
    return await reader.readexactly(length)
