"""B49 交流：`say` 工具的可见性、出局禁言与配额（C53）。

三条硬规则：
① 世界没开交流（`comms.mode: none`）时 `say` 不可见；
② 出局者不能发言（不是「发出去被忽略」，是直接拒绝并说明原因）；
③ 配额由**代码**计数，超限拒绝且回合不崩。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card, make_state  # noqa: E402
from trpg.agents.agent.tools import ToolMixin  # noqa: E402
from trpg.models.enums import Position  # noqa: E402


class FakePack:
    """鸭子类型的 pack：只提供 turn_flow 声明，够 `_tool_say` 用。"""

    def __init__(self, comms: dict | None) -> None:
        self._comms = comms

    def turn_flow_declaration(self) -> dict:
        return {"comms": self._comms} if self._comms is not None else {}


class FakeAgent(ToolMixin):
    def __init__(self, pack) -> None:
        self.pack = pack
        self.spoken: list[dict] = []


def agent_with(comms: dict | None) -> FakeAgent:
    return FakeAgent(FakePack(comms))


def test_say_hidden_when_comms_none():
    agent = agent_with(None)

    assert "say" not in agent.visible_tools()
    assert "look" in agent.visible_tools()


def test_say_visible_when_comms_enabled():
    agent = agent_with({"mode": "one_way", "max_per_turn": 1})

    assert "say" in agent.visible_tools()


def test_dead_actor_cannot_speak():
    state = make_state()
    cid = next(iter(state.characters))
    state.characters[cid].position = Position.dead
    agent = agent_with({"mode": "one_way", "max_per_turn": 1})

    result = agent._tool_say(cid, make_card(cid=cid), state, {"speech": "我说话"}, 1)

    assert "出局" in result
    assert agent.spoken == []


def test_speech_is_recorded():
    state = make_state()
    cid = next(iter(state.characters))
    agent = agent_with({"mode": "one_way", "max_per_turn": 2})

    result = agent._tool_say(cid, make_card(cid=cid), state, {"speech": "别动"}, 1)

    assert "别动" in result
    assert agent.spoken[0]["text"] == "别动"
    assert state.comms_used[cid] == 1


def test_quota_blocks_second_speech():
    state = make_state()
    cid = next(iter(state.characters))
    agent = agent_with({"mode": "one_way", "max_per_turn": 1})
    card = make_card(cid=cid)

    agent._tool_say(cid, card, state, {"speech": "第一次"}, 1)
    second = agent._tool_say(cid, card, state, {"speech": "第二次"}, 1)

    assert "已经说过" in second
    assert len(agent.spoken) == 1


def test_empty_speech_is_rejected():
    state = make_state()
    cid = next(iter(state.characters))
    agent = agent_with({"mode": "one_way", "max_per_turn": 1})

    result = agent._tool_say(cid, make_card(cid=cid), state, {"speech": "  "}, 1)

    assert "为空" in result
    assert state.comms_used.get(cid, 0) == 0, "空发言不应消耗配额"


def test_global_quota_is_not_consumed_per_actor():
    """quota=global_per_turn 时不写 per-actor 计数（由编排层另行统计）。"""
    state = make_state()
    cid = next(iter(state.characters))
    agent = agent_with({"mode": "one_way", "quota": "global_per_turn", "max_per_turn": 1})

    agent._tool_say(cid, make_card(cid=cid), state, {"speech": "大家好"}, 1)

    assert state.comms_used == {}


def test_comms_used_starts_empty_on_state():
    assert make_state().comms_used == {}
