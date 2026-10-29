"""B48 阶段谓词：纯函数、只读 state、与能力清单一一对应。

谓词是 `phases[].when` / `break_if` 的全部表达能力，所以必须：
① 与 manifest 注册的名字完全一致；② 不改 state；③ 缺依赖系统时不可用（由 check_world 拦）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_state  # noqa: E402
from trpg.models import Enemy, Quest  # noqa: E402
from trpg.rulespack import manifest  # noqa: E402
from trpg.rulespack.predicates import (  # noqa: E402
    PREDICATES,
    PredicateContext,
    PredicateError,
    evaluate,
)


@pytest.fixture()
def state():
    return make_state()


def ctx(state, **params) -> PredicateContext:
    turn = params.pop("turn", 1)
    return PredicateContext(state=state, turn=turn, params=params)


def test_registry_matches_manifest():
    assert set(PREDICATES) == set(manifest.predicates)


def test_always(state):
    assert evaluate("always", ctx(state)) is True


def test_every_n(state):
    assert evaluate("every_n", ctx(state, turn=6, n=3)) is True
    assert evaluate("every_n", ctx(state, turn=7, n=3)) is False


def test_every_n_requires_positive_n(state):
    with pytest.raises(PredicateError):
        evaluate("every_n", ctx(state, n=0))


def test_stage_is(state):
    state.stage = 2

    assert evaluate("stage_is", ctx(state, stage=2)) is True
    assert evaluate("stage_is", ctx(state, stage=1)) is False


def test_flag_set(state):
    state.party.flags = ["alarm"]

    assert evaluate("flag_set", ctx(state, flag="alarm")) is True
    assert evaluate("flag_set", ctx(state, flag="quiet")) is False


def test_quest_active(state):
    state.quests = [Quest(id="q1", title="找猫")]

    assert evaluate("quest_active", ctx(state, quest="q1")) is True
    assert evaluate("quest_active", ctx(state, quest="q9")) is False


def test_enemies_present(state):
    assert evaluate("enemies_present", ctx(state)) is False

    state.enemies = {"e1": Enemy(id="e1", hp=2)}
    assert evaluate("enemies_present", ctx(state)) is True

    state.enemies["e1"].hp = 0
    assert evaluate("enemies_present", ctx(state)) is False, "hp 归 0 视为不在场"


def test_in_combat(state):
    assert evaluate("in_combat", ctx(state)) is False

    state.enemies = {"e1": Enemy(id="e1", hp=2)}
    assert evaluate("in_combat", ctx(state)) is True

    state.enemies = {}
    state.party.flags = ["in_combat"]
    assert evaluate("in_combat", ctx(state)) is True


def test_location_tag(state):
    inside = PredicateContext(
        state=state, turn=1, params={"tag": "indoors"}, location_tags=("indoors", "dark")
    )
    outside = PredicateContext(
        state=state, turn=1, params={"tag": "water"}, location_tags=("indoors",)
    )

    assert evaluate("location_tag", inside) is True
    assert evaluate("location_tag", outside) is False


def test_has_human_seat(state):
    assert evaluate("has_human_seat", ctx(state)) is False
    assert evaluate("has_human_seat", PredicateContext(state=state, turn=1, human_seat=True)) is True


def test_clock_phase(state):
    phases = {"morning": {"from": 6, "to": 11}, "night": {"from": 22, "to": 5}}
    state.clock.hour = 23

    night = PredicateContext(state=state, turn=1, params={"phase": "night"}, phases=phases)
    morning = PredicateContext(state=state, turn=1, params={"phase": "morning"}, phases=phases)

    assert evaluate("clock_phase", night) is True
    assert evaluate("clock_phase", morning) is False


def test_predicates_never_mutate_state(state):
    state.enemies = {"e1": Enemy(id="e1", hp=2)}
    before = state.model_dump_json()

    for name in manifest.predicates:
        try:
            evaluate(
                name,
                ctx(state, n=1, stage=1, quest="q", tag="t", flag="f", phase="night"),
            )
        except PredicateError:
            pass

    assert state.model_dump_json() == before


def test_unknown_predicate_raises(state):
    with pytest.raises(PredicateError) as err:
        evaluate("moon_phase", ctx(state))

    assert "moon_phase" in str(err.value)
