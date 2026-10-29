"""装备接入：GM 说「是什么」，代码算「多强」。

覆盖契约点 C4/C5/C7/C8/C14：
- tier 受 era.tier_max 与幕次封顶
- 属性落在 tier 区间 × 成色倍率内
- 成色由 2d6 + stage_quality_mod 决定，传说额外要求张力
- tag 必须在封闭词表内，最多 3 个（loop 侧过滤）
- 装备 tag 只修正对应技能的 DC
"""

from __future__ import annotations

import random
from pathlib import Path

from trpg.equipment.roll import quality_from_roll, roll_loot, tool_dc_bonus
from trpg.engine.loop import tool_bonus_for
from trpg.rules.judge import judge
from trpg.engine.loop import GameLoop, TurnRecord
from trpg.agents import MockLLM
from trpg.models import EquipmentInstance, PlayerAction
from trpg.models.enums import Quality
from trpg.rules.dice import DiceEngine
from trpg.rules.loader import Rules
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]


def _eqcfg() -> dict:
    return Rules(ROOT).equipment


def test_tier_capped_by_stage_and_era():
    eqcfg = _eqcfg()
    early = roll_loot(name="x", eqcfg=eqcfg, rng=random.Random(1), stage=1, tier_hint=9)
    assert 1 <= early.tier <= 3
    late = roll_loot(name="y", eqcfg=eqcfg, rng=random.Random(1), stage=3, tier_hint=9)
    assert late.tier <= 4


def test_attrs_within_range_times_quality():
    eqcfg = _eqcfg()
    for seed in range(30):
        inst = roll_loot(name="刀", eqcfg=eqcfg, rng=random.Random(seed), stage=3, slot="weapon")
        lo, hi = eqcfg["tier_range"][inst.tier]["weapon"]
        low = lo * inst.quality.mult
        high = hi * inst.quality.mult
        assert low - 1 <= inst.attrs["atk"] <= high + 1


def test_quality_table_and_legendary_gate():
    eqcfg = _eqcfg()
    assert quality_from_roll(2, eqcfg) == Quality.rough
    assert quality_from_roll(7, eqcfg) == Quality.common
    assert quality_from_roll(10, eqcfg) == Quality.fine
    assert quality_from_roll(11, eqcfg) == Quality.rare
    assert quality_from_roll(20, eqcfg, tension=0) == Quality.rare  # 张力不够，压回 rare
    assert quality_from_roll(20, eqcfg, tension=90) == Quality.legendary


def test_tool_dc_bonus_only_on_matching_skill():
    eqcfg = _eqcfg()
    assert tool_dc_bonus(["lockpick"], eqcfg, "撬锁") == -2
    assert tool_dc_bonus(["lockpick"], eqcfg, "观察") == 0
    assert tool_dc_bonus(["blade"], eqcfg, "撬锁") == 0


def test_lockpick_tool_lowers_dc():
    rules = Rules(ROOT)
    card = make_card()
    state = make_state(card)
    action = PlayerAction(actor_id="chr_test01", action="撬锁", skill="撬锁", intent="normal")

    base = judge(action, card, state, rules, DiceEngine(1), tool_bonus=tool_bonus_for(rules, card, action))
    card.ext.equipment.append(
        EquipmentInstance(
            id="eq_tool01", template_id="tpl_lock_kit", tier=1, tags=["lockpick"]
        )
    )
    with_tool = judge(
        action, card, state, rules, DiceEngine(1), tool_bonus=tool_bonus_for(rules, card, action)
    )
    assert with_tool.dc == base.dc - 2


def test_loop_apply_loot_filters_tags_and_records():
    rules = Rules(ROOT)
    card = make_card()
    state = make_state(card)
    loop = GameLoop(rules, {"chr_test01": card}, state, MockLLM(42), seed=42)
    rec = TurnRecord(turn=1, scene="")

    loop._apply_loot(
        [
            {
                "name": "磨损的撬锁工具组",
                "slot": "tool",
                "tags": ["lockpick", "bogus_tag"],
                "tier_hint": 9,
            }
        ],
        rec,
        1,
    )

    assert rec.loot, "应记录战利品"
    got = rec.loot[0]
    assert got["owner"] == "chr_test01"
    assert got["tags"] == ["lockpick"], "词表外 tag 必须被丢掉"
    assert 1 <= got["tier"] <= 3
    assert card.ext.equipment[-1].id == got["id"]
    assert got["id"] in loop.state.characters["chr_test01"].inventory


def test_loop_apply_loot_ignores_empty():
    rules = Rules(ROOT)
    card = make_card()
    state = make_state(card)
    loop = GameLoop(rules, {"chr_test01": card}, state, MockLLM(42), seed=42)
    rec = TurnRecord(turn=1, scene="")
    before = len(card.ext.equipment)
    loop._apply_loot([], rec, 1)
    loop._apply_loot(None, rec, 1)
    loop._apply_loot([{"slot": "tool"}], rec, 1)  # 没名字
    assert len(card.ext.equipment) == before
    assert rec.loot == []


def test_starter_equipment_is_common_and_stable():
    """初始配装不掷骰：同模板两次造出来的属性一致（可复现）。"""
    from trpg.equipment.roll import instance_from_template

    tpl = {"id": "tpl_knife", "name": "短刀", "tier": 1, "req_level": 1, "attrs": {"atk": [2, 3]}, "tags": ["blade"]}
    a = instance_from_template(tpl)
    b = instance_from_template(tpl)
    assert a.attrs == b.attrs == {"atk": 2}
    assert a.quality == Quality.common
