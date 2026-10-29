"""M2：schema 约束的结构化输出。

两条硬约束：

1. **schema 的字段名必须与 `PlayerAction.from_raw` 读的键一一对齐**——
   换成结构化输出后，`from_raw` 仍然是唯一的解析入口；键名对不上就会**静默丢字段**
   （模型给了 `skill`，`from_raw` 读 `skills`，行动就没技能了，而且不报错）。
2. **mock 与自研 adapter 必须继续走自由文本路径**——它们没有 `structured()`，
   `_chat_json` 会自动回落，`ironvale` 的 golden 因此逐字节不变。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents.schemas import AgentTurnOut  # noqa: E402
from trpg.models import PlayerAction  # noqa: E402

#: `PlayerAction.from_raw` 会读的键。schema 少任何一个，那个字段就会丢。
FROM_RAW_KEYS = ("thought", "action", "approach", "skill", "target", "intent", "mode")


def test_schema_covers_every_field_from_raw_reads():
    fields = set(AgentTurnOut.model_fields)

    missing = [key for key in FROM_RAW_KEYS if key not in fields]
    assert not missing, f"schema 缺字段，from_raw 会静默丢弃：{missing}"


def test_schema_defaults_to_final():
    out = AgentTurnOut(action="看看四周")

    assert out.type == "final"
    assert out.tool == ""
    assert out.thought == ""


def test_tool_shape_carries_name_and_args():
    out = AgentTurnOut(type="tool", tool="recall", args={"query": "铁栅"})

    assert out.type == "tool"
    assert out.tool == "recall"
    assert out.args == {"query": "铁栅"}


def test_final_shape_maps_into_player_action():
    data = AgentTurnOut(
        thought="先看谁在撒谎",
        action="盯着 chr_2 的手",
        approach="装作无事",
        skill="观察",
        target="chr_2",
        intent="normal",
        mode="attack",
    ).model_dump()

    action = PlayerAction.from_raw("chr_1", data)

    assert action.actor_id == "chr_1"
    assert action.thought == "先看谁在撒谎"
    assert action.action == "盯着 chr_2 的手"
    assert action.skill == "观察"
    assert action.target == "chr_2"
    assert action.mode.value == "attack"


def test_empty_action_falls_back_to_placeholder():
    """模型给了空 action 也不能产出一个空行动——`from_raw` 的兜底要仍然有效。"""
    action = PlayerAction.from_raw("chr_1", AgentTurnOut().model_dump())

    assert action.action == "按兵不动"


def test_mode_alias_still_works_through_schema():
    """模型写中文战斗意图，仍要能解析（别名表在 `from_raw` 里，不在 schema 里）。"""
    action = PlayerAction.from_raw("chr_1", AgentTurnOut(mode="逃跑").model_dump())

    assert action.mode.value == "flee"


def test_unknown_mode_does_not_raise():
    action = PlayerAction.from_raw("chr_1", AgentTurnOut(mode="随便").model_dump())

    assert action.mode is None
