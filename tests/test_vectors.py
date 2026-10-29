"""Cross-language samples shared with src/core/internal/control/vectors_test.go."""

import json
from pathlib import Path

from mihomo_desktop.protocol import control_pb2
from mihomo_desktop.protocol.frame import (
    HEADER_SIZE,
    KIND_REQUEST,
    KIND_RESPONSE,
    encode,
    parse_header,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "testdata/control-vectors.json").read_text("utf-8")
)


def test_request_encoding_matches_go() -> None:
    request = control_pb2.Request(protocol_version=1, id=1, get_info=control_pb2.GetInfoRequest())
    frame = encode(KIND_REQUEST, request.SerializeToString())
    assert frame.hex() == VECTORS["get_info_request_frame"]


def test_decodes_go_response() -> None:
    frame = bytes.fromhex(VECTORS["get_info_response_frame"])
    length = parse_header(frame[:HEADER_SIZE], KIND_RESPONSE)
    response = control_pb2.Response.FromString(frame[HEADER_SIZE : HEADER_SIZE + length])
    assert response.id == 1
    assert response.get_info.core_version == "unlinked"
    assert response.get_info.state == control_pb2.RUN_STATE_STOPPED
    assert response.get_info.ownership == control_pb2.OWNERSHIP_AVAILABLE
