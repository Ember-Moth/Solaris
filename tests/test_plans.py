"""Plans as the core reads them (docs/routing.md §6, §8.5) and the add-rule dialog's text."""

import json

from solaris.models import plans
from solaris.models.plans import Parsed, parse_cidrs, parse_domains, plan_json
from solaris.models.routing import Plan, PlanGroup, PlanRule


def test_the_default_template() -> None:
    plan = plans.default_plan("p", "日常")
    assert (plan.id, plan.name, plan.fallback) == ("p", "日常", "proxy")
    assert [(g.id, g.kind, g.type) for g in plan.groups] == [
        ("proxy", "builtin", "select"),
        ("auto", "builtin", "url-test"),
    ]
    assert [(r.content["service"], r.target) for r in plan.rules] == [
        ("ads", "REJECT"),
        ("private", "DIRECT"),
        ("openai", "region:US"),
        ("anthropic", "region:US"),
        ("gemini", "region:US"),
        ("cn", "DIRECT"),
    ]
    assert len({r.id for r in plan.rules}) == len(plan.rules)
    assert all(r.kind == "service" and r.enabled for r in plan.rules)


def test_a_copy_gets_ids_of_its_own() -> None:
    plan = plans.default_plan("p", "日常")
    copy = plans.copy_plan(plan, "日常 副本")
    assert (copy.name, copy.groups, copy.fallback) == ("日常 副本", plan.groups, "proxy")
    assert copy.id != plan.id
    assert not {r.id for r in copy.rules} & {r.id for r in plan.rules}
    assert [r.content for r in copy.rules] == [r.content for r in plan.rules]


def test_plan_json_expands_every_kind_of_entry() -> None:
    plan = Plan(
        "p",
        "日常",
        "auto",
        (
            PlanGroup("proxy", "builtin", "select"),
            PlanGroup("region:JP", "region", "select", hidden=True),
        ),
        (
            PlanRule("tg", "service", {"service": "telegram"}, "region:JP"),
            PlanRule("site", "category", {"type": "GEOSITE", "value": "cn"}, "DIRECT"),
            PlanRule("ip-cat", "category", {"type": "GEOIP", "value": "cn"}, "DIRECT"),
            PlanRule(
                "dom", "domain", {"items": ["example.com", "=a.example.org", "~ads"]}, "REJECT"
            ),
            PlanRule(
                "ip",
                "ip",
                {"items": ["10.0.0.0/8", "2001:db8::/32"], "no_resolve": False},
                "DIRECT",
            ),
            PlanRule("off", "service", {"service": "ads"}, "REJECT", enabled=False),
            PlanRule("gone", "service", {"service": "no-such-service"}, "proxy"),
        ),
    )
    document = json.loads(plan_json(plan, "en"))
    assert (document["version"], document["locale"], document["fallback"]) == (1, "en", "auto")
    assert document["groups"] == [
        {"id": "proxy", "kind": "builtin", "type": "select"},
        {"id": "region:JP", "kind": "region", "type": "select", "hidden": True},
    ]
    rules = {rule["id"]: rule for rule in document["rules"]}
    assert rules["tg"] == {
        "id": "tg",
        "kind": "service",
        "enabled": True,
        "items": [
            {"type": "GEOSITE", "value": "telegram"},
            {"type": "GEOIP", "value": "telegram", "no_resolve": True},
        ],
        "target": "region:JP",
        "service": "telegram",
    }
    assert rules["site"]["items"] == [{"type": "GEOSITE", "value": "cn"}]
    assert rules["ip-cat"]["items"] == [{"type": "GEOIP", "value": "cn", "no_resolve": True}]
    assert rules["dom"]["items"] == [
        {"type": "DOMAIN-SUFFIX", "value": "example.com"},
        {"type": "DOMAIN", "value": "a.example.org"},
        {"type": "DOMAIN-KEYWORD", "value": "ads"},
    ]
    assert rules["ip"]["items"] == [
        {"type": "IP-CIDR", "value": "10.0.0.0/8"},
        {"type": "IP-CIDR6", "value": "2001:db8::/32"},
    ]
    assert rules["off"]["enabled"] is False
    # An entry that stands for no rule any more stays out, in the order of the rest.
    assert list(rules) == ["tg", "site", "ip-cat", "dom", "ip", "off"]


def test_custom_groups_carry_their_name_and_members() -> None:
    group = PlanGroup("g", "custom", "url-test", "低倍率", {"regions": ["HK"]}, {"interval": 600})
    document = json.loads(plan_json(Plan("p", "日常", "proxy", (group,)), "zh"))
    assert document["groups"] == [
        {
            "id": "g",
            "kind": "custom",
            "type": "url-test",
            "name": "低倍率",
            "members": {"regions": ["HK"]},
            "options": {"interval": 600},
        }
    ]


def test_domains_are_read_a_line_each() -> None:
    text = "example.com\n\n  =www.example.org  \n~tracker\nexa mple.com\n=\n~a,b\n"
    parsed = parse_domains(text + "example.com\n中文.例子")
    assert parsed == Parsed(
        ("example.com", "=www.example.org", "~tracker", "中文.例子"),
        ((5, "exa mple.com"), (6, "="), (7, "~a,b")),
        suffixes=2,
        full=1,
        keywords=1,
    )
    assert parse_domains("") == Parsed((), ())


def test_ip_ranges_are_read_a_line_each() -> None:
    parsed = parse_cidrs("10.0.0.0/8\n192.168.1.7/24\n2001:db8::/32\n1.2.3.4\n10.0.0.0/33\nnope")
    assert parsed.items == ("10.0.0.0/8", "192.168.1.0/24", "2001:db8::/32", "1.2.3.4/32")
    assert parsed.invalid == ((5, "10.0.0.0/33"), (6, "nope"))


def test_a_plan_the_core_converted_reads_back() -> None:
    converted = json.dumps(
        {
            "version": 1,
            "locale": "zh",
            "groups": [
                {
                    "id": "g-1",
                    "kind": "custom",
                    "name": "媒体",
                    "type": "url-test",
                    "members": {"regions": ["HK"], "others": ["proxy"]},
                    "options": {"interval": 600},
                }
            ],
            "rules": [
                {
                    "id": "r-1",
                    "kind": "domain",
                    "items": [
                        {"type": "DOMAIN-SUFFIX", "value": "a.example"},
                        {"type": "DOMAIN", "value": "b.example"},
                        {"type": "DOMAIN-KEYWORD", "value": "tracker"},
                    ],
                    "target": "g-1",
                },
                {
                    "id": "r-2",
                    "kind": "ip",
                    "items": [{"type": "IP-CIDR", "value": "10.0.0.0/8"}],
                    "target": "DIRECT",
                },
                {
                    "id": "r-3",
                    "kind": "category",
                    "items": [{"type": "GEOIP", "value": "cn"}],
                    "target": "DIRECT",
                },
                {
                    "id": "r-4",
                    "kind": "raw",
                    "items": [{"type": "PROCESS-NAME", "value": "ssh"}],
                    "target": "DIRECT",
                },
            ],
            "fallback": "g-1",
        }
    )
    plan = plans.plan_from_json(converted, "p-1", "导入")
    assert (plan.id, plan.name, plan.fallback) == ("p-1", "导入", "g-1")
    assert [(g.id, g.kind) for g in plan.groups] == [
        ("proxy", "builtin"),
        ("auto", "builtin"),
        ("g-1", "custom"),
    ]
    assert plan.groups[2].conditions == {"regions": ["HK"], "others": ["proxy"]}
    assert [(r.kind, r.content) for r in plan.rules] == [
        ("domain", {"items": ["a.example", "=b.example", "~tracker"]}),
        ("ip", {"items": ["10.0.0.0/8"], "no_resolve": False}),
        ("category", {"type": "GEOIP", "value": "cn", "no_resolve": False}),
        ("raw", {"items": [{"type": "PROCESS-NAME", "value": "ssh"}]}),
    ]
    # The items go back to the core as they came: the GEOIP rule still resolves names.
    document = json.loads(plans.plan_json(plan, "zh"))
    assert [r["items"] for r in document["rules"]] == [
        [
            {"type": "DOMAIN-SUFFIX", "value": "a.example"},
            {"type": "DOMAIN", "value": "b.example"},
            {"type": "DOMAIN-KEYWORD", "value": "tracker"},
        ],
        [{"type": "IP-CIDR", "value": "10.0.0.0/8"}],
        [{"type": "GEOIP", "value": "cn"}],
        [{"type": "PROCESS-NAME", "value": "ssh"}],
    ]


def test_what_the_rule_test_reads() -> None:
    assert plans.match_target("Web.Telegram.org.") == ("web.telegram.org", 0)
    assert plans.match_target("example.com:8443") == ("example.com", 8443)
    assert plans.match_target("https://chat.openai.com/c/1") == ("chat.openai.com", 0)
    assert plans.match_target("http://example.com:8080/x") == ("example.com", 8080)
    assert plans.match_target("10.1.2.3") == ("10.1.2.3", 0)
    assert plans.match_target("2001:db8::1") == ("2001:db8::1", 0)
    assert plans.match_target("[2001:db8::1]:443") == ("2001:db8::1", 443)
    for bad in ("", "not a host", "example.com:99999", "a,b.example", "[::1]x"):
        assert plans.match_target(bad) is None, bad


def test_the_warp_exit_goes_to_the_core_only_when_on() -> None:
    """docs/exits.md §7.1: egress.warp.via, by group id; the switch stays in the UI's plan."""
    plan = plans.default_plan("p", "日常")
    assert plans.warp_exit(plan) == plans.WarpExit(False, "proxy")
    assert "egress" not in json.loads(plan_json(plan, "zh"))
    off = plans.with_warp_exit(plan, plans.WarpExit(False, "region:JP"))
    assert off.egress == {"warp": {"enabled": False, "via": "region:JP"}}
    assert "egress" not in json.loads(plan_json(off, "zh"))
    on = plans.with_warp_exit(off, plans.WarpExit(True, "region:JP"))
    assert json.loads(plan_json(on, "zh"))["egress"] == {"warp": {"via": "region:JP"}}
    # A copy keeps it; a plan the core wrote with it turns it on.
    assert plans.warp_exit(plans.copy_plan(on, "副本")) == plans.WarpExit(True, "region:JP")
    read = plans.plan_from_json(plan_json(on, "zh"), "q", "读回")
    assert plans.warp_exit(read) == plans.WarpExit(True, "region:JP")


def test_mesh_targets_and_their_common_rules() -> None:
    target = "mesh:sub-a/家里/书房"
    assert plans.is_mesh(target)
    assert not plans.is_mesh(plans.WARP)
    assert plans.mesh_name(target) == "家里/书房"
    ranges, domains = plans.common_mesh_rules("Tailscale", target)
    assert (ranges.kind, ranges.content, ranges.target) == (
        "ip",
        {"items": ["100.64.0.0/10", "fd7a:115c:a1e0::/48"], "no_resolve": True},
        target,
    )
    assert (domains.kind, domains.content) == ("domain", {"items": ["ts.net"]})
    assert plans.common_mesh_rules("ZeroTier", target) == ()
    expanded = [plans.rule_items(rule) for rule in (ranges, domains)]
    assert expanded == [
        [
            {"type": "IP-CIDR", "value": "100.64.0.0/10", "no_resolve": True},
            {"type": "IP-CIDR6", "value": "fd7a:115c:a1e0::/48", "no_resolve": True},
        ],
        [{"type": "DOMAIN-SUFFIX", "value": "ts.net"}],
    ]
