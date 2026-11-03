"""Text survives byte/UTF-16 boundaries, transport and storage without normalization."""

from pathlib import Path

import pytest

from solaris.models.backup import Backups, Snapshot
from solaris.models.backup_google import _fit
from solaris.models.profiles import KIND_FILE, Profile, ProfileError, ProfileList
from solaris.models.store import ProfileStore
from solaris.models.subscription import decode_config
from solaris.text import utf8_prefix
from tests.support.unicode import CLUSTERS, TEXT


@pytest.mark.parametrize("cluster", CLUSTERS)
def test_byte_limited_summary_keeps_whole_graphemes(cluster: str) -> None:
    text = "a" + cluster + "z"
    end = 1 + len(cluster.encode("utf-8"))
    for budget in range(1, end):
        assert utf8_prefix(text, budget) == "a"
    assert utf8_prefix(text, end) == "a" + cluster
    assert utf8_prefix(text, end + 1) == text
    assert utf8_prefix(text, 0) == ""
    assert utf8_prefix(text, -1) == ""


def test_cloud_summary_keeps_sequence_and_original_text() -> None:
    # Drive's 124-byte budget includes the property key; leave room for only part
    # of the next family, which must be omitted as a unit instead of split.
    family = "👨‍👩‍👧‍👦"
    value = "x" * 116 + family
    assert _fit("device", value) == "x" * 116
    assert value.endswith(family)
    assert _fit("device", TEXT) == utf8_prefix(TEXT, 118)


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_utf8_config_preserves_names_credentials_and_line_endings(bom: bytes) -> None:
    config = f'proxies:\r\n  - name: "{TEXT}"\r\n    password: "e\u0301\u200d"\r\n'
    assert decode_config(bom + config.encode("utf-8")) == config


@pytest.mark.parametrize(
    "body",
    [
        "中文".encode("gbk"),
        "proxies: []".encode("utf-16"),
        b"proxies: [\xff]",
        b"name: \xf0\x9f\x98",  # truncated astral character
        b"name: \xed\xa0\x80",  # surrogate, not a Unicode scalar value
        b"name: \xc0\xaf",  # overlong UTF-8
    ],
)
def test_invalid_config_encoding_is_rejected(body: bytes) -> None:
    with pytest.raises(ProfileError) as raised:
        decode_config(body)
    assert raised.value.code == "not_text"


def test_names_and_references_survive_database_and_backup(tmp_path: Path) -> None:
    config = (
        f'proxy-groups:\n  - name: "{TEXT}"\n    type: select\n    proxies: [DIRECT]\n'
        f'rules:\n  - "MATCH,{TEXT}"\n'
    )
    store = ProfileStore(tmp_path)
    try:
        store.write_config("unicode", config)
        store.save(ProfileList((Profile("unicode", TEXT, KIND_FILE, path="local.yaml"),)))
        assert store.load().profiles[0].name == TEXT
        assert store.read_config("unicode") == config
        snapshot = Backups(store.database, device=TEXT, app_version="1.0.0").snapshot()
        encoded = snapshot.encode()
        assert not encoded.startswith(b"\xef\xbb\xbf")
        decoded = Snapshot.decode(encoded)
        profile = decoded.document["profiles"][0]
        assert profile["name"] == TEXT
        assert profile["config"]["yaml"] == config
        assert decoded.document["device"] == TEXT
    finally:
        store.close()
