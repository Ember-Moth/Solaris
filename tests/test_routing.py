"""The routing data of docs/routing.md §9."""

from dataclasses import replace
from pathlib import Path

import pytest

from solaris.models.profiles import KIND_SUBSCRIPTION, Profile, ProfileList
from solaris.models.routing import (
    DIRECT,
    MODE_PLAN,
    MODE_PROFILE,
    REJECT,
    Plan,
    PlanGroup,
    PlanRule,
    RoutingState,
    RoutingStore,
)
from solaris.models.store import ProfileStore
from tests.support.storage import stored


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


SELECT = PlanGroup("select", "builtin", "select", hidden=False)
AUTO = PlanGroup("auto", "builtin", "url-test", options={"interval": 300})
HK = PlanGroup("region:HK", "region", "url-test")
WORK = PlanGroup(
    "6f1e0c2a",
    "custom",
    "select",
    name="工作",
    conditions={"include": "香港|日本", "exclude": "IPLC"},
)
ADS = PlanRule("r-ads", "category", {"geosite": "category-ads-all"}, REJECT)
TELEGRAM = PlanRule("r-tg", "service", {"service": "telegram"}, "region:HK")
CORP = PlanRule("r-corp", "domain", {"suffix": "corp.example.com"}, DIRECT, enabled=False)
DEFAULT = Plan(
    "plan-1",
    "默认方案",
    "select",
    groups=(SELECT, AUTO, HK, WORK),
    rules=(ADS, TELEGRAM, CORP),
)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def profiles(tmp_path: Path, clock: Clock) -> ProfileStore:
    return ProfileStore(tmp_path, clock=clock)


@pytest.fixture
def routing(profiles: ProfileStore) -> RoutingStore:
    return RoutingStore(profiles.database)


def test_the_state_starts_unchosen(routing: RoutingStore) -> None:
    assert routing.state() == RoutingState()
    assert routing.plans() == ()


def test_the_state_keeps_what_another_writer_stored(
    profiles: ProfileStore, routing: RoutingStore
) -> None:
    profile = Profile(id="a1", name="A", kind=KIND_SUBSCRIPTION, url="https://e/a")
    profiles.save(ProfileList((profile,), "a1"))  # the profile in use
    state = routing.update_state(mode=MODE_PLAN, plan_id="plan-1")
    assert state == RoutingState(MODE_PLAN, "a1", "plan-1", None)
    assert routing.update_state(info_pattern="剩余|到期") == replace(
        state, info_pattern="剩余|到期"
    )
    routing.update_state(mode=MODE_PROFILE)
    assert profiles.load().active == "a1"
    with pytest.raises(ValueError, match="invalid mode"):
        routing.update_state(mode="mine")
    with pytest.raises(TypeError):
        routing.update_state(plan="plan-1")
    assert routing.state().mode == MODE_PROFILE


def test_plans_round_trip(tmp_path: Path, routing: RoutingStore) -> None:
    other = Plan("plan-2", "游戏", DIRECT, groups=(SELECT, HK), rules=(replace(ADS, id="r-2"),))
    routing.put_plan(DEFAULT)
    routing.put_plan(other)
    assert routing.plans() == (DEFAULT, other)
    assert routing.plan("plan-2") == other
    # Built-in and region groups have the same ids in every plan.
    assert stored(tmp_path, "SELECT count(*) FROM plan_groups WHERE id = 'region:HK'") == [(2,)]
    with pytest.raises(KeyError):
        routing.plan("nope")


def test_the_exits_settings_are_stored_with_the_plan(tmp_path: Path, routing: RoutingStore) -> None:
    egress = {"warp": {"enabled": True, "via": "region:HK"}}
    routing.put_plan(replace(DEFAULT, egress=egress))
    assert routing.plan(DEFAULT.id).egress == egress
    assert stored(tmp_path, "SELECT egress FROM plans") == [
        ('{"warp":{"enabled":true,"via":"region:HK"}}',)
    ]
    routing.put_plan(DEFAULT)
    assert routing.plan(DEFAULT.id).egress == {}


def test_a_plan_is_written_whole(tmp_path: Path, clock: Clock, routing: RoutingStore) -> None:
    routing.put_plan(DEFAULT)
    clock.now = 2_000.0
    edited = replace(
        DEFAULT,
        groups=(SELECT, AUTO, HK, replace(WORK, hidden=True)),
        rules=(TELEGRAM, ADS),  # moved up; the corp rule is deleted
    )
    routing.put_plan(edited)
    assert routing.plan("plan-1") == edited
    rules = {
        row[0]: row[1:]
        for row in stored(tmp_path, "SELECT id, position, updated_at, deleted_at FROM plan_rules")
    }
    assert rules == {
        "r-tg": (0, 2_000.0, None),
        "r-ads": (1, 2_000.0, None),
        "r-corp": (2, 2_000.0, 2_000.0),  # kept, marked
    }
    groups = dict(stored(tmp_path, "SELECT id, updated_at FROM plan_groups"))
    assert groups == {"select": 1_000.0, "auto": 1_000.0, "region:HK": 1_000.0, "6f1e0c2a": 2_000.0}

    clock.now = 3_000.0
    routing.put_plan(DEFAULT)  # undo
    assert routing.plan("plan-1") == DEFAULT
    assert stored(tmp_path, "SELECT deleted_at FROM plan_rules WHERE id = 'r-corp'") == [(None,)]
    # Writing the same plan again changes nothing.
    clock.now = 4_000.0
    routing.put_plan(DEFAULT)
    assert stored(tmp_path, "SELECT max(updated_at) FROM plan_rules") == [(3_000.0,)]
    assert stored(tmp_path, "SELECT updated_at FROM plans") == [(1_000.0,)]


def test_deleting_and_restoring_a_plan(tmp_path: Path, routing: RoutingStore) -> None:
    second = Plan("plan-2", "游戏", DIRECT)
    routing.put_plan(DEFAULT)
    routing.put_plan(second)
    routing.delete_plan("plan-1")
    assert routing.plans() == (second,)
    with pytest.raises(KeyError):
        routing.plan("plan-1")
    assert stored(tmp_path, "SELECT count(*) FROM plan_rules WHERE deleted_at IS NULL") == [(3,)]
    routing.put_plan(DEFAULT)
    assert routing.plans() == (DEFAULT, second)  # back in its place


def test_ordering_plans(routing: RoutingStore) -> None:
    plans = [Plan(f"p{n}", f"方案 {n}", DIRECT) for n in range(4)]
    for plan in plans:
        routing.put_plan(plan)
    routing.order_plans(["p2", "p0", "gone"])
    assert [p.id for p in routing.plans()] == ["p2", "p0", "p1", "p3"]
    routing.put_plan(Plan("p4", "新的", DIRECT))
    assert [p.id for p in routing.plans()][-1] == "p4"


@pytest.mark.parametrize(
    "plan",
    [
        replace(DEFAULT, groups=(replace(SELECT, kind="special"),)),
        replace(DEFAULT, rules=(replace(ADS, kind="regex"),)),
    ],
)
def test_unknown_kinds_are_refused(tmp_path: Path, routing: RoutingStore, plan: Plan) -> None:
    with pytest.raises(ValueError, match="kind"):
        routing.put_plan(plan)
    assert stored(tmp_path, "SELECT count(*) FROM plans") == [(0,)]
