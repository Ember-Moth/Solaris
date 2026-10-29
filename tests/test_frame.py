import asyncio
import struct

import pytest

from mihomo_desktop.protocol.frame import (
    HEADER_SIZE,
    KIND_REQUEST,
    KIND_RESPONSE,
    MAX_RESPONSE_BYTES,
    FrameError,
    encode,
    parse_header,
    read_frame,
)


def test_round_trip() -> None:
    async def scenario() -> bytes:
        reader = asyncio.StreamReader()
        reader.feed_data(encode(KIND_REQUEST, b"payload"))
        reader.feed_eof()
        return await read_frame(reader, KIND_REQUEST)

    assert asyncio.run(scenario()) == b"payload"


@pytest.mark.parametrize(
    ("offset", "value"),
    [
        (0, b"XDCP"),  # magic
        (4, struct.pack("<H", 2)),  # version
        (6, struct.pack("<H", KIND_RESPONSE)),  # kind
        (8, struct.pack("<I", 0)),  # empty payload
    ],
)
def test_rejects_bad_headers(offset: int, value: bytes) -> None:
    header = bytearray(encode(KIND_REQUEST, b"x")[:HEADER_SIZE])
    header[offset : offset + len(value)] = value
    with pytest.raises(FrameError):
        parse_header(bytes(header), KIND_REQUEST)


def test_rejects_oversized_lengths() -> None:
    header = bytearray(encode(KIND_RESPONSE, b"x")[:HEADER_SIZE])
    header[8:12] = struct.pack("<I", MAX_RESPONSE_BYTES + 1)
    with pytest.raises(FrameError):
        parse_header(bytes(header), KIND_RESPONSE)
    with pytest.raises(FrameError):
        encode(KIND_RESPONSE, bytes(MAX_RESPONSE_BYTES + 1))
