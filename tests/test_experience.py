"""经验系统：agent 跨局积累的判断（程序记忆）。

设计依据三条：
- **Reflexion**（Shinn et al. 2023）：语言化反思替代梯度更新，但必须 **cap 数量**
- **Voyager**（Wang et al. 2023）：只有验证成功的才入库（门槛）
- **程序记忆**（认知科学）：固化进提示词，不走检索——所以经验要少而精

经验挂 `agent_id`（跨局、跨存档），不挂 `session_id`。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.storage.experience import (  # noqa: E402
    DEFAULT_KEEP,
    add_experience,
    count_experience,
    list_experience,
    prune_experience,
)
from trpg.storage.conn import init_db  # noqa: E402


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    init_db(connection)
    yield connection
    connection.close()


def test_add_and_list_experience(conn):
    add_experience(conn, "agt_1", "werewolf", "别第一个跳预言家", kind="lesson")

    items = list_experience(conn, "agt_1", "werewolf")

    assert len(items) == 1
    assert items[0]["text"] == "别第一个跳预言家"
    assert items[0]["kind"] == "lesson"


def test_experience_is_isolated_per_agent(conn):
    add_experience(conn, "agt_1", "werewolf", "我只信自己看到的")
    add_experience(conn, "agt_2", "werewolf", "装傻比装聪明活得久")

    assert [i["text"] for i in list_experience(conn, "agt_1", "werewolf")] == [
        "我只信自己看到的"
    ]


def test_experience_is_isolated_per_world(conn):
    add_experience(conn, "agt_1", "werewolf", "狼人杀里的教训")
    add_experience(conn, "agt_1", "ironvale", "铁谷里的教训")

    assert count_experience(conn, "agt_1", "werewolf") == 1
    assert [
        i["text"] for i in list_experience(conn, "agt_1", "ironvale")
    ] == ["铁谷里的教训"]


def test_empty_text_is_not_stored(conn):
    """不写空经验：空话会占额度，还会被当成「有经验」。"""
    assert add_experience(conn, "agt_1", "werewolf", "   ") == 0
    assert add_experience(conn, "agt_1", "werewolf", "") == 0
    assert count_experience(conn, "agt_1", "werewolf") == 0


def test_duplicate_experience_is_not_stored_twice(conn):
    """同一件事换个说法不该再记一遍——否则 prompt 会被同一条教训撑满。"""
    add_experience(conn, "agt_1", "werewolf", "别第一个跳预言家")

    assert add_experience(conn, "agt_1", "werewolf", "别第一个跳预言家") == 0
    assert add_experience(conn, "agt_1", "werewolf", "别 第一个 跳预言家") == 0
    assert count_experience(conn, "agt_1", "werewolf") == 1


def test_different_experience_is_kept(conn):
    add_experience(conn, "agt_1", "werewolf", "别第一个跳预言家")
    add_experience(conn, "agt_1", "werewolf", "白天别抢着替人辩护")

    assert count_experience(conn, "agt_1", "werewolf") == 2


def test_prompt_carries_experience_of_this_agent_only(conn):
    """经验进 prompt 时以「过去的局」为标题，且不会混进共享段。"""
    from support.builders import make_card

    from trpg.agents.prompts.pc import pc_system

    text = pc_system(
        make_card(),
        ["观察"],
        experience=["别第一个跳预言家", "先听后说"],
    )

    assert "你从过去的局里学到的" in text
    assert "别第一个跳预言家" in text
    assert "这是你的经验，不是这一局发生的事" in text


def test_prompt_without_experience_is_unchanged():
    from support.builders import make_card

    from trpg.agents.prompts.pc import pc_system

    text = pc_system(make_card(), ["观察"])

    assert "你从过去的局里学到的" not in text


def test_unknown_kind_falls_back_to_lesson(conn):
    add_experience(conn, "agt_1", "werewolf", "随便说说", kind="乱写的")

    assert list_experience(conn, "agt_1", "werewolf")[0]["kind"] == "lesson"


def test_prune_keeps_most_recent(conn):
    """超上限时淘汰最旧的——十局前的教训不如上一局刚吃过的亏管用。"""
    for index in range(DEFAULT_KEEP + 3):
        add_experience(conn, "agt_1", "werewolf", f"经验 {index}", dedupe=False)

    removed = prune_experience(conn, "agt_1", "werewolf", keep=DEFAULT_KEEP)

    assert removed == 3
    assert count_experience(conn, "agt_1", "werewolf") == DEFAULT_KEEP
    latest = list_experience(conn, "agt_1", "werewolf")
    assert latest[0]["text"] == f"经验 {DEFAULT_KEEP + 2}"
    assert all(i["text"] != "经验 0" for i in latest), "最旧的应当被淘汰"


def test_list_is_newest_first(conn):
    add_experience(conn, "agt_1", "werewolf", "先写的")
    add_experience(conn, "agt_1", "werewolf", "后写的")

    assert [i["text"] for i in list_experience(conn, "agt_1", "werewolf")] == [
        "后写的",
        "先写的",
    ]


def test_experience_system_is_registered():
    """能力清单里要有 experience，世界包才能用 `systems.experience` 开关它。"""
    from trpg.rulespack import manifest

    spec = manifest.systems.get("experience")
    assert spec is not None
    assert spec.status == "active"
