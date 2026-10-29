"""B47 prompt 骨架外置：世界包用 `prompts/pc.md` 追加自己的设定。

设计约束：**只追加、不替换**。变量插值（姓名 / 秘密 / 工具 / 技能表）仍由框架做，
否则骨架一改（加字段、调顺序），世界包里的半份骨架就会静默错位。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents.prompts.pc import pc_system  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402


def test_world_prompt_is_appended():
    pack = load_world("werewolf", ROOT)

    text = pc_system(make_card(), ["观察"], pack=pack)

    assert "本世界的补充设定" in text
    assert "八个村民" in text


def test_world_without_prompt_is_untouched():
    pack = load_world("ironvale", ROOT)

    text = pc_system(make_card(), ["观察"], pack=pack)

    assert "本世界的补充设定" not in text


def test_without_pack_is_untouched():
    text = pc_system(make_card(), ["观察"])

    assert "本世界的补充设定" not in text


def test_gm_world_prompt_is_appended():
    from trpg.agents.prompts.gm import gm_system

    text = gm_system(
        "狼人杀", "base", "冷峻", [], pack=load_world("werewolf", ROOT)
    )

    assert "本世界的补充设定" in text
    assert "只写看得见的事" in text


def test_gm_without_prompt_is_untouched():
    from trpg.agents.prompts.gm import gm_system

    text = gm_system("铁谷", "medieval", "粗粝", [], pack=load_world("ironvale", ROOT))

    assert "本世界的补充设定" not in text


def test_shared_prefix_comes_first():
    """B32：system 要以「所有角色相同」的段开头，provider 的 prompt cache 才可能命中。"""
    pack = load_world("werewolf", ROOT)

    first = pc_system(make_card(cid="chr_1"), ["观察"], pack=pack)
    second = pc_system(make_card(cid="chr_2"), ["观察"], pack=pack)

    assert first.startswith("# 规则")
    head_first = first.split("# 你在扮演")[0]
    head_second = second.split("# 你在扮演")[0]
    assert head_first == head_second, "共享前缀必须逐字相同"
    assert "本世界的补充设定" in head_first


def test_framework_skeleton_still_present():
    """追加不能挤掉框架自己的段落。"""
    pack = load_world("werewolf", ROOT)

    text = pc_system(make_card(), ["观察"], pack=pack)

    for section in ("# 你在扮演", "# 你的秘密", "# 规则", "# 纪律"):
        assert section in text
