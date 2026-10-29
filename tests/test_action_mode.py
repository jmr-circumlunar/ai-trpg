"""战斗意图 `mode` 的解析。

背景（真实模型首测发现）：prompt 让玩家输出 `mode`，但 `_to_action` 曾完全丢弃它，
导致 `combat.resolve()` 永远拿到 None —— 战斗在真实局里是死代码。
"""

from __future__ import annotations

import pytest

from trpg.agents.agent import AgentLLM


def _to_action(data: dict):
    """不跑 __init__（不需要 config/cards），只测解析。"""
    obj = object.__new__(AgentLLM)
    return obj._to_action("chr_x", data)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("attack", "attack"),
        ("攻击", "attack"),
        ("进攻", "attack"),
        ("defend", "defend"),
        ("防御", "defend"),
        ("flee", "flee"),
        ("撤退", "flee"),
        ("ATTACK", "attack"),
    ],
)
def test_mode_aliases(raw, expected):
    action = _to_action({"action": "动手", "mode": raw})
    assert action.mode is not None
    assert action.mode.value == expected


@pytest.mark.parametrize("raw", [None, "", "dance", "cast"])
def test_mode_unknown_is_none(raw):
    action = _to_action({"action": "观望", "mode": raw})
    assert action.mode is None


def test_target_preserved_with_mode():
    action = _to_action({"action": "砍", "mode": "attack", "target": "en_1", "skill": "器械"})
    assert action.mode.value == "attack"
    assert action.target == "en_1"
    assert action.skill == "器械"
