"""S4-1：统一动作提交入口（人类与 AI 同一条路径）。

钉住出口判据：

1. **一条入口两个来源**：人提交与 AI 提交都过 `submit_action()`；
2. **幂等**：重复提交不产生重复行动（返回既有动作）；
3. **契约未变**：`/pending` 与 `/action` 的字段不变（`HumanActionReq` 仍可用）；
4. **行动者由引擎决定**：提交函数只管「交什么动作」，不管「轮到谁」。
"""

from __future__ import annotations

import pytest

from trpg.engine.actions import parse_skill_choice, submit_action
from trpg.models import PlayerAction


def test_human_and_ai_go_through_the_same_entry():
    registry: dict = {}
    human = submit_action(
        "chr_a", {"action": "我去查一下", "skill": "观察"}, source="human", registry=registry
    )
    ai = submit_action(
        "chr_b", {"action": "我跟着投"}, source="ai", registry=registry
    )
    assert isinstance(human.action, PlayerAction)
    assert isinstance(ai.action, PlayerAction)
    assert human.action.action == "我去查一下"
    assert ai.action.actor_id == "chr_b"
    assert list(registry) == ["chr_a", "chr_b"]


def test_duplicate_submission_is_idempotent():
    registry: dict = {}
    first = submit_action("chr_a", {"action": "第一下"}, source="human", registry=registry)
    second = submit_action("chr_a", {"action": "又一下"}, source="human", registry=registry)
    assert second.duplicate is True
    assert second.accepted is False
    assert second.action is first.action, "重复提交必须返回既有动作"
    assert second.action.action == "第一下"
    assert len(registry) == 1, "重复提交不产生第二个动作"


def test_ai_action_object_is_accepted_as_is():
    registry: dict = {}
    action = PlayerAction(actor_id="chr_x", action="原本的动作")
    out = submit_action("chr_x", action, source="ai", registry=registry)
    assert out.accepted is True
    assert out.action.action == "原本的动作"


def test_skill_choice_uses_the_same_schema_for_both():
    skill, target = parse_skill_choice({"skill": "观察", "target": "chr_b"})
    assert skill == "观察" and target == "chr_b"
    # 没给就是空（= 不发动），与 AI 侧 SkillChoiceOut 的缺省一致
    assert parse_skill_choice({}) == ("", "")
    assert parse_skill_choice(None) == ("", "")


def test_actor_id_is_required():
    with pytest.raises(ValueError):
        submit_action("", {"action": "没有行动者"})


def test_pending_and_action_contract_unchanged():
    """`/action` 的请求契约没变（人类提交字段仍被接受）。"""
    from trpg.api.schemas import HumanActionReq

    req = HumanActionReq(
        char_id="chr_a",
        action="我去了",
        skill="观察",
        target="chr_b",
    )
    out = submit_action(req.char_id, req.model_dump(), source="human")
    assert out.action.action == "我去了"
    assert out.action.skill == "观察"
    assert out.action.target == "chr_b"
