"""B31 GM 输出 schema 按能力裁剪：关掉 combat 的世界不该被要求申报敌人。

为什么重要：schema 里留着 `enemies`，模型就会照它输出，而那边 `state.enemies`
不可写 → 每回合白挨一次 delta 被拒。裁剪后 prompt 与能力一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card, make_state  # noqa: E402
from trpg.agents.prompts.gm import gm_scene_user  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402


def render(pack) -> str:
    cards = {"chr_1": make_card(cid="chr_1")}
    return gm_scene_user(make_state(), cards, 1, pack=pack)


def test_combat_off_world_has_no_enemies_field():
    pack = load_world("werewolf", ROOT)

    text = render(pack)

    assert "enemies" not in text
    assert "threat" not in text


def test_full_world_keeps_enemies_declaration():
    pack = load_world("ironvale", ROOT)

    text = render(pack)

    assert "enemies" in text
    assert "threat" in text


def test_without_pack_keeps_full_schema():
    text = gm_scene_user(make_state(), {"chr_1": make_card(cid="chr_1")}, 1)

    assert "enemies" in text


def test_options_section_survives_in_both():
    for world in ("werewolf", "ironvale"):
        text = render(load_world(world, ROOT))
        assert "`options`" in text, world


def render_narrate(pack) -> str:
    from trpg.agents.prompts.gm import gm_narrate_batch_user

    return gm_narrate_batch_user(make_state(), {}, [], 1, pack=pack)


def test_equipment_off_world_has_no_loot_field():
    text = render_narrate(load_world("werewolf", ROOT))

    assert "loot" not in text
    assert "战利品" not in text


def test_full_world_keeps_loot_field():
    text = render_narrate(load_world("ironvale", ROOT))

    assert "loot" in text
    assert "战利品" in text
