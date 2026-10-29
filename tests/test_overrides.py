"""Overrides (docs/architecture.md §7.4): the merge the simulated host shares with the core,
and how the library stores, validates and applies them."""

from pathlib import Path

import pytest

from mihomo_desktop.host.base import HostError, TunnelConfig
from mihomo_desktop.models.profiles import ProfileError
from tests.support.simulated import SAMPLE_CONFIG, check_config
from tests.test_library import OTHER_CONFIG, Scene, run

BASE = """\
mode: rule
log-level: info
dns:
  enable: true
  nameserver: [223.5.5.5]
proxies:
  - {name: a, type: ss, server: a.example.net, port: 1}
proxy-groups:
  - {name: g, type: select, proxies: [a]}
rules:
  - MATCH,g
"""


def merged(*overrides: str) -> dict:
    return check_config(TunnelConfig(BASE, overrides))


def test_insert_keys_add_to_the_lists() -> None:
    document = merged(
        "prepend-rules: ['DOMAIN,first.example,DIRECT']\n"
        "append-rules: ['DOMAIN,last.example,DIRECT']\n"
        "prepend-proxies: [{name: b, type: ss, server: b.example.net, port: 2}]\n"
        "append-proxy-groups: [{name: h, type: select, proxies: [b]}]\n"
    )
    assert document["rules"] == [
        "DOMAIN,first.example,DIRECT",
        "MATCH,g",
        "DOMAIN,last.example,DIRECT",
    ]
    assert [p["name"] for p in document["proxies"]] == ["b", "a"]
    assert [g["name"] for g in document["proxy-groups"]] == ["g", "h"]
    assert "prepend-rules" not in document


def test_other_keys_merge_deeply() -> None:
    document = merged("dns: {nameserver: [1.1.1.1]}\nlog-level: debug\ntun: {stack: gvisor}\n")
    # Mappings merge key by key; lists and scalars are replaced.
    assert document["dns"] == {"enable": True, "nameserver": ["1.1.1.1"]}
    assert document["log-level"] == "debug"
    assert document["tun"] == {"stack": "gvisor"}


def test_overrides_apply_in_order() -> None:
    global_override = "mode: global\nprepend-rules: ['DOMAIN,global.example,DIRECT']\n"
    own_override = "mode: direct\nprepend-rules: ['DOMAIN,own.example,DIRECT']\n"
    document = merged(global_override, own_override)
    assert document["mode"] == "direct"  # the profile's own override comes last
    assert document["rules"] == [
        "DOMAIN,own.example,DIRECT",
        "DOMAIN,global.example,DIRECT",
        "MATCH,g",
    ]


def test_an_override_merges_before_it_inserts() -> None:
    document = merged("rules: ['MATCH,DIRECT']\nprepend-rules: ['DOMAIN,x.example,g']\n")
    assert document["rules"] == ["DOMAIN,x.example,g", "MATCH,DIRECT"]


def test_safety_settings_come_after_the_overrides() -> None:
    document = merged("external-controller: 0.0.0.0:9090\nsecret: hunter2\n")
    assert "external-controller" not in document
    assert "secret" not in document


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("mode: [", "override 2: "),
        ("- just\n- a list\n", "override 2: not a YAML mapping"),
        ("prepend-rules: MATCH,DIRECT\n", "override 2: prepend-rules and rules must be lists"),
    ],
)
def test_broken_overrides_are_invalid_configs(override: str, message: str) -> None:
    with pytest.raises(HostError) as raised:
        merged("# empty documents are skipped\n", override)
    assert raised.value.code == "invalid_config"
    assert raised.value.message.startswith(message)


# -- the library ---------------------------------------------------------------------------
def test_every_request_carries_the_overrides(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.store.write_override(None, "mode: global\n")
    scene.store.write_override("sample-b", "log-level: debug\n")
    validated: list[TunnelConfig] = []
    validate = scene.host.validate

    async def record(config: TunnelConfig) -> None:
        validated.append(config)
        await validate(config)

    scene.host.validate = record  # type: ignore[method-assign]

    async def scenario() -> None:
        await scene.library.import_url("https://new.example.com/s")
        await scene.library.update("sample-b")
        await scene.library.activate("sample-b")
        await scene.host.stop()
        await scene.library.start_tunnel()

    run(scenario)
    # A new profile has only the global override; a stored one has its own as well.
    assert [c.overrides for c in validated] == [
        ("mode: global\n",),
        ("mode: global\n", "log-level: debug\n"),
    ]
    assert scene.host.config is not None
    assert scene.host.config.overrides == ("mode: global\n", "log-level: debug\n")
    assert scene.host.effective is not None
    assert scene.host.effective["mode"] == "global"


def test_a_rejected_override_is_not_stored(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    for target in ("sample-b", None):
        with pytest.raises(HostError) as raised:
            run(lambda target=target: scene.library.save_override(target, "proxies: []\n"))
        assert raised.value.code == "invalid_config"  # it removed every proxy
        assert scene.store.read_override(target) == ""
    profiles = run(scene.library.current)
    assert not profiles.get("sample-b").has_override
    assert not profiles.has_global_override
    assert scene.host.reloads == 0


def test_saving_reloads_only_the_profile_in_use(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    # Another profile's override does not touch the running tunnel.
    profiles = run(lambda: scene.library.save_override("sample-b", "mode: direct\n"))
    assert profiles.get("sample-b").has_override
    assert scene.store.read_override("sample-b") == "mode: direct\n"
    assert scene.host.reloads == 0

    # The active profile's override and the global one reload it, keeping TUN on.
    run(lambda: scene.library.save_override("sample-a", "log-level: debug\n"))
    assert scene.host.reloads == 1
    profiles = run(lambda: scene.library.save_override(None, "mode: global\n"))
    assert profiles.has_global_override
    assert scene.host.reloads == 2
    assert scene.host.config is not None
    assert scene.host.config.overrides == ("mode: global\n", "log-level: debug\n")
    assert scene.host.config.options.tun_enabled

    # A stopped tunnel is not reloaded.
    async def stopped() -> None:
        await scene.host.stop()
        await scene.library.save_override(None, "mode: rule\n")

    run(stopped)
    assert scene.host.reloads == 2


def test_blank_text_removes_an_override(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    run(lambda: scene.library.save_override("sample-a", "mode: direct\n"))
    profiles = run(lambda: scene.library.save_override("sample-a", "  \n"))
    assert not profiles.get("sample-a").has_override
    assert not scene.store.override_path("sample-a").exists()
    assert run(lambda: scene.library.read_override("sample-a")) == ""


def test_the_global_override_is_checked_with_the_active_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.store.write_config("sample-a", OTHER_CONFIG)
    # OTHER_CONFIG has no proxy-groups to prepend to, which is fine; it has a proxy.
    run(lambda: scene.library.save_override(None, "prepend-proxy-groups: []\n"))
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == OTHER_CONFIG

    empty = Scene(tmp_path / "empty", seeded=False)
    with pytest.raises(ProfileError) as raised:
        run(lambda: empty.library.save_override(None, "mode: global\n"))
    assert raised.value.code == "no_profile"


def test_removing_a_profile_removes_its_override(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    run(lambda: scene.library.save_override("sample-b", "mode: direct\n"))
    run(lambda: scene.library.remove("sample-b"))
    assert not scene.store.override_path("sample-b").exists()
    assert scene.store.read_config("sample-a") == SAMPLE_CONFIG
