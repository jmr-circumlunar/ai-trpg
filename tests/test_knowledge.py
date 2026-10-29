"""知识视图（方案 38 / C67 / C68）：agent 此刻知道什么 + 此刻该守的准则。

三条纪律各有测试守着：
1. **缺省为空**：世界包不声明 → 提示词逐字节不变（存量世界零改动）；
2. **如实 + 不泄露**：只给本人该知道的（身份 / 存活 / 本人笔记），不含别人的身份；
3. **分阶段**：首夜只给首夜准则（信息为零时不注入后期的推理话术）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.engine.knowledge import (  # noqa: E402
    build_knowledge,
    match_when,
    select_strategies,
)

# ── 替身 ──────────────────────────────────────────────────────


class FakeCard:
    def __init__(self, name: str, profession: str = "") -> None:
        self.data = SimpleNamespace(name=name, personality="")
        self.ext = SimpleNamespace(profession=profession)


class FakeRuntime:
    def __init__(self, camp: str = "", notes=None) -> None:
        self.camp = camp
        self.notes = list(notes or [])
        self.position = SimpleNamespace(value="scene")


class FakePack:
    def __init__(self, knowledge=None, camp_labels=None) -> None:
        self.knowledge = knowledge or {}
        self.camp_labels = camp_labels or {}


DECLARATION = {
    "enabled": True,
    "sections": ["identity", "alive", "dead", "notes", "stage"],
    "phases": {"hidden": ["night", "night_skill"], "open": ["discuss", "vote"]},
    "strategies": [
        {
            "id": "first_night_facts",
            "phase": "hidden",
            "when": "cycle == 1",
            "text": "还没有人发言过，别凭空推断谁可疑。",
        },
        {
            "id": "self_kill_conditions",
            "phase": "hidden",
            "when": "cycle >= 1",
            "text": "考虑自刀骗药的前提：女巫大概率会开解药，且队里有人接应。",
        },
        {
            "id": "day_speech",
            "phase": "open",
            "text": "发言要给三件事：立场、怀疑谁、依据。",
        },
    ],
}


def _state():
    return SimpleNamespace(
        characters={
            "chr_1": FakeRuntime(camp="village", notes=["我查过 2 号"]),
            "chr_2": FakeRuntime(camp="village"),
            "chr_3": FakeRuntime(camp="village"),
        }
    )


def _cards():
    return {
        "chr_1": FakeCard("顾知微", "预言家"),
        "chr_2": FakeCard("苏晚", "村民"),
        "chr_3": FakeCard("程野", "村民"),
    }


# ── 缺省为空 ──────────────────────────────────────────────────


def test_knowledge_is_empty_without_declaration():
    """世界包不声明知识视图 → 空包 → 提示词与改造前逐字节一致。"""
    knowledge = build_knowledge(_state(), _cards(), FakePack(), "chr_1")

    assert knowledge.is_empty()
    assert knowledge.render() == ""


def test_knowledge_render_is_empty_when_disabled():
    pack = FakePack(knowledge={"enabled": False, "sections": ["identity"]})

    knowledge = build_knowledge(_state(), _cards(), pack, "chr_1")

    assert knowledge.is_empty()
    assert knowledge.render() == ""


# ── 内容与裁剪 ────────────────────────────────────────────────


def test_knowledge_contains_own_identity_alive_and_notes():
    pack = FakePack(
        knowledge=DECLARATION, camp_labels={"village": "好人阵营", "wolf": "狼人阵营"}
    )

    knowledge = build_knowledge(
        _state(), _cards(), pack, "chr_1", phase="day", stage="天亮了"
    )

    assert not knowledge.is_empty()
    assert "预言家" in knowledge.identity
    assert "好人阵营" in knowledge.identity
    assert "苏晚" in knowledge.alive and "程野" in knowledge.alive
    assert knowledge.notes == ("我查过 2 号",)
    text = knowledge.render()
    assert "# 你现在知道的" in text
    assert "苏晚" in text


def test_knowledge_does_not_leak_other_players_identity():
    """知识视图是**本人的**：不能出现别人的身份（按 `secrets` 裁剪的单源在调用方）。"""
    pack = FakePack(knowledge=DECLARATION, camp_labels={"wolf": "狼人阵营"})

    knowledge = build_knowledge(_state(), _cards(), pack, "chr_2", phase="day")

    text = knowledge.render()
    # 本人是村民：不应看到「预言家」这样的身份标签，也不该看到别人的笔记
    assert "预言家" not in text
    assert "我查过 2 号" not in text


# ── 分阶段筛选 ────────────────────────────────────────────────


def test_strategies_are_filtered_by_phase():
    declaration = DECLARATION

    night = select_strategies(declaration, "hidden", {"cycle": 1, "phase": "hidden"})
    day = select_strategies(declaration, "open", {"cycle": 1, "phase": "open"})

    assert "还没有人发言过，别凭空推断谁可疑。" in night
    assert "发言要给三件事：立场、怀疑谁、依据。" not in night
    assert "发言要给三件事：立场、怀疑谁、依据。" in day
    assert "还没有人发言过，别凭空推断谁可疑。" not in day


def test_first_night_strategy_is_not_injected_on_later_nights():
    """首夜准则只在首夜生效——第二夜起不再重复「还没有人发言过」。"""
    declaration = DECLARATION

    first = select_strategies(declaration, "hidden", {"cycle": 1})
    later = select_strategies(declaration, "hidden", {"cycle": 3})

    assert any("还没有人发言过" in item for item in first)
    assert not any("还没有人发言过" in item for item in later)
    assert any("自刀骗药" in item for item in later)


def test_knowledge_strategies_follow_the_phase_in_prompt():
    pack = FakePack(knowledge=DECLARATION)

    night = build_knowledge(_state(), _cards(), pack, "chr_1", phase="hidden", cycle=1)
    day = build_knowledge(_state(), _cards(), pack, "chr_1", phase="open", cycle=1)

    assert any("还没有人发言过" in item for item in night.strategies)
    assert not any("还没有人发言过" in item for item in day.strategies)


# ── 条件式求值不许执行任意表达式 ──────────────────────────────


def test_when_only_accepts_simple_comparisons():
    assert match_when("cycle == 1", {"cycle": 1}) is True
    assert match_when("cycle >= 2", {"cycle": 3}) is True
    assert match_when("phase == open", {"phase": "open"}) is True
    assert match_when("cycle != 1", {"cycle": 2}) is True

    # 写法看不懂 = 不成立（宁可不注入，也不要执行世界包里的表达式）
    assert match_when("__import__('os').system('x')", {"night": 1}) is False
    assert match_when("night == 1 and phase == day", {"night": 1, "phase": "day"}) is False
    assert match_when("unknown_key == 1", {"cycle": 1}) is False
