"""B45 阶段管线（纯逻辑层）：声明 → 阶段序列 → 本回合执行计划。

这一层不执行阶段、不碰 loop，只回答三个问题：
① 这个世界的回合由哪些阶段组成；② 本回合哪些阶段该跑；③ 谁命中提前结束。
行为接线在 loop（下一批），因此这里的改动不影响任何既有世界。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_state  # noqa: E402
from trpg.engine.pipeline import (  # noqa: E402
    PipelineError,
    PhaseSpec,
    parse_turn_flow,
    plan_turn,
)
from trpg.models import Enemy  # noqa: E402
from trpg.rulespack.predicates import PredicateContext  # noqa: E402


@pytest.fixture()
def state():
    return make_state()


def ctx(state, turn: int = 1, **params) -> PredicateContext:
    return PredicateContext(state=state, turn=turn, params=params)


# ── 解析：preset 与显式声明 ─────────────────────────────────


def test_empty_declaration_falls_back_to_standard_preset():
    phases = parse_turn_flow(None)

    assert [p.id for p in phases] == ["scene", "declare", "resolve", "narrate", "commit"]
    assert [p.kind for p in phases] == [
        "narration",
        "action",
        "action",
        "broadcast",
        "commit",
    ]


def test_minimal_preset():
    phases = parse_turn_flow({"from": "minimal"})

    assert [p.id for p in phases] == ["scene", "declare", "narrate"]


def test_explicit_phases_override_preset():
    phases = parse_turn_flow(
        {
            "from": "standard",
            "phases": [
                {"id": "open", "kind": "narration"},
                {"id": "act", "kind": "action", "order": "fixed"},
            ],
        }
    )

    assert [p.id for p in phases] == ["open", "act"]
    assert phases[1].params["order"] == "fixed"


def test_unknown_kind_raises():
    with pytest.raises(PipelineError) as err:
        parse_turn_flow({"phases": [{"id": "x", "kind": "moonlight"}]})

    assert "moonlight" in str(err.value)


def test_unknown_preset_raises():
    with pytest.raises(PipelineError):
        parse_turn_flow({"from": "chaos"})


def test_missing_kind_raises():
    with pytest.raises(PipelineError):
        parse_turn_flow({"phases": [{"id": "x"}]})


def test_params_are_filled_with_manifest_defaults():
    phases = parse_turn_flow({"phases": [{"id": "act", "kind": "action"}]})
    spec = phases[0]

    assert spec.params["order"] == "dex"
    assert spec.params["visibility"] == "previous"
    assert spec.params["groups"] == []


def test_unknown_param_raises():
    with pytest.raises(PipelineError) as err:
        parse_turn_flow({"phases": [{"id": "act", "kind": "action", "nonsense": 1}]})

    assert "nonsense" in str(err.value)


def test_repeat_must_be_positive():
    with pytest.raises(PipelineError):
        parse_turn_flow({"phases": [{"id": "c", "kind": "check", "repeat": 0}]})


# ── 计划：when / break_if / repeat ──────────────────────────


def test_plain_plan_executes_all(state):
    phases = parse_turn_flow(None)
    outcome = plan_turn(phases, ctx(state))

    assert outcome.executed == ["scene", "declare", "resolve", "narrate", "commit"]
    assert outcome.skipped == []
    assert outcome.broken is None


def test_when_false_skips_phase(state):
    phases = parse_turn_flow(
        {
            "phases": [
                {"id": "scene", "kind": "narration"},
                {"id": "fight", "kind": "action", "when": "enemies_present"},
            ]
        }
    )

    outcome = plan_turn(phases, ctx(state))

    assert outcome.executed == ["scene"]
    assert outcome.skipped[0]["id"] == "fight"
    assert "enemies_present" in outcome.skipped[0]["reason"]


def test_when_true_runs_phase(state):
    state.enemies = {"e1": Enemy(id="e1", hp=2)}
    phases = parse_turn_flow(
        {"phases": [{"id": "fight", "kind": "action", "when": "enemies_present"}]}
    )

    assert plan_turn(phases, ctx(state)).executed == ["fight"]


def test_break_if_stops_remaining_phases(state):
    state.characters = {}  # 让 win_decided 之外的断言不依赖具体胜负规则
    phases = parse_turn_flow(
        {
            "phases": [
                {"id": "check", "kind": "check", "break_if": "always"},
                {"id": "after", "kind": "narration"},
            ]
        }
    )

    outcome = plan_turn(phases, ctx(state))

    assert outcome.executed == ["check"]
    assert outcome.broken == "always"
    assert outcome.skipped[0]["id"] == "after"
    assert "break_if" in outcome.skipped[0]["reason"]


def test_repeat_runs_phase_multiple_times(state):
    phases = parse_turn_flow(
        {"phases": [{"id": "check_win", "kind": "check", "repeat": 2}]}
    )

    outcome = plan_turn(phases, ctx(state))

    assert outcome.executed == ["check_win", "check_win"]


def test_repeat_stops_after_break(state):
    phases = parse_turn_flow(
        {
            "phases": [
                {"id": "check", "kind": "check", "repeat": 2, "break_if": "always"},
            ]
        }
    )

    outcome = plan_turn(phases, ctx(state))

    assert outcome.executed == ["check"]
    assert len(outcome.skipped) == 1


def test_plan_records_order_for_receipt(state):
    """C54：本回合实际执行顺序要能直接写进 TurnRecord.order。"""
    phases = parse_turn_flow(None)
    outcome = plan_turn(phases, ctx(state))

    assert outcome.order == outcome.executed


def test_phase_spec_carries_visibility_and_order(state):
    phases = parse_turn_flow(
        {
            "phases": [
                {"id": "night", "kind": "action", "visibility": "group"},
                {"id": "talk", "kind": "action", "visibility": "public"},
            ]
        }
    )

    assert phases[0].params["visibility"] == "group"
    assert phases[1].params["visibility"] == "public"


def test_groups_are_parsed(state):
    phases = parse_turn_flow(
        {
            "phases": [
                {
                    "id": "night",
                    "kind": "action",
                    "groups": [{"by": "camp", "value": "wolf", "mode": "consensus"}],
                }
            ]
        }
    )

    assert phases[0].params["groups"][0]["by"] == "camp"


def test_phase_spec_is_immutable():
    spec = PhaseSpec(id="a", kind="action")

    with pytest.raises(Exception):
        spec.id = "b"  # type: ignore[misc]


# ── 回执字段（B45，三处同步的第一处） ───────────────────────


def test_turn_record_has_pipeline_receipt_fields():
    import dataclasses
    import json

    from trpg.engine.loop import TurnRecord

    rec = TurnRecord(turn=1, scene="x")

    assert rec.order == []
    assert rec.skipped == []
    assert rec.skipped_phases == []
    assert rec.votes == []
    assert rec.vote_result == {}
    # API/trace 会 asdict 序列化，新增字段不能破坏它
    json.dumps(dataclasses.asdict(rec), ensure_ascii=False, default=str)


def test_plan_skipped_reasons_are_receipt_ready(state):
    """skipped 的每条都要有 id/kind/reason，能直接写进 TurnRecord.skipped。"""
    phases = parse_turn_flow(
        {
            "phases": [
                {"id": "fight", "kind": "action", "when": "enemies_present"},
                {"id": "after", "kind": "check", "when": "always"},
            ]
        }
    )

    outcome = plan_turn(phases, ctx(state))

    for item in outcome.skipped:
        assert {"id", "kind", "reason"} <= set(item)
    assert outcome.executed == ["after"]


# ── 校验与解析必须用同一份判据（2026-10-23 修） ─────────────


def test_parser_and_validator_share_meta_keys():
    """元键只有一份：解析器用的就是 `manifest.META_KEYS`。

    以前两处各写一套，校验侧是 14 项超集 → 世界包写个不认识参数，
    `check_world` 放行、运行时抛错后**静默回落到默认流程**。
    这条断言防它再分叉。
    """
    from trpg.engine import pipeline
    from trpg.rulespack.manifest import META_KEYS

    assert pipeline._META_KEYS == META_KEYS


def test_unknown_param_raises_instead_of_falling_back():
    """写错参数必须当场报错——不能悄悄换成默认流程。"""
    from trpg.engine.pipeline import PipelineError, parse_turn_flow

    with pytest.raises(PipelineError):
        parse_turn_flow({"phases": [{"id": "x", "kind": "apply", "bogus_key": 1}]})


def test_apply_accepts_last_words():
    """`last_words` 已进 `apply` 的参数表（否则带遗言的世界包会被拒）。"""
    from trpg.engine.pipeline import parse_turn_flow

    phases = parse_turn_flow(
        {
            "phases": [
                {
                    "id": "x",
                    "kind": "apply",
                    "effect": "eliminate",
                    "last_words": True,
                }
            ]
        }
    )

    assert phases[0].param("last_words") is True
