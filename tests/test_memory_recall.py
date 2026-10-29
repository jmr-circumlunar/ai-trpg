"""记忆召回「允许偏差」：模糊印象要标出来。

设计立场：召回**不是查档案**，是「角色想起了什么」。
结构化命中（有回合/时间锚点）是记得清楚的事；向量命中只是隐约印象。
角色可以按不准的记忆行动——但它得知道那是模糊的，否则就成了伪准确。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card, make_state  # noqa: E402
from trpg.agents.prompts.pc import pc_user  # noqa: E402
from trpg.memory.recall import RecallItem, RecallResult  # noqa: E402


def test_vector_hits_are_marked_as_fuzzy_impressions():
    result = RecallResult(
        query="铁栅",
        items=[
            RecallItem(source="recent", text="第3回合我在门口等过", score=0.9),
            RecallItem(source="vector", text="好像有人提过铁栅", score=0.42),
        ],
    )

    texts = result.texts()

    assert "第3回合我在门口等过" in texts
    fuzzy = [t for t in texts if "隐约记得" in t]
    assert fuzzy, "向量命中应当标成模糊印象"
    assert "好像有人提过铁栅" in fuzzy[0]


def test_structured_hits_are_not_marked():
    result = RecallResult(
        query="q",
        items=[RecallItem(source="important", text="有人推了我一把", score=0.8)],
    )

    texts = result.texts()

    assert texts == ["有人推了我一把"], "结构化命中是「记得清楚」，不该加模糊标记"


def test_prompt_tells_agent_memories_may_be_wrong():
    text = pc_user(
        make_state(),
        make_card(),
        [],
        1,
        memories=["（隐约记得）好像有人提过铁栅"],
    )

    assert "隐约记得" in text
    assert "可能记错" in text, "prompt 必须告诉角色记忆可能不准"
    assert "模糊印象" in text


def test_duplicate_text_is_collapsed():
    result = RecallResult(
        query="q",
        items=[
            RecallItem(source="recent", text="同一件事", score=0.9),
            RecallItem(source="vector", text="同一件事", score=0.4),
        ],
    )

    assert len(result.texts()) == 1
