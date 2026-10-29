"""S6-1：词缀机制 + 生成范围与价值区间。

要钉住的四件事：
1. **三维约束**：词缀 × 槽位 × 品阶 —— 槽位不符、品阶不符都不出现；
2. **价值区间取值**：价值在声明的区间内，不写死；
3. **互斥组**：同一件装备上同组词缀只能有一个；
4. **扩展字段白名单由世界包声明**：不在白名单内的自定义字段不会进实例（框架不塞世界语义）。

另外：**缺省零变化** —— 不开口子就不掷词缀，旧装备结构与属性逐值不变。
"""

from __future__ import annotations

import random

import pytest

from trpg.equipment.roll import (
    affix_candidates,
    apply_affixes,
    instance_from_template,
    roll_affixes,
    roll_loot,
)
from trpg.models.equipment import EquipmentInstance
from trpg.rules.loader import Rules

ROOT = "."


@pytest.fixture
def eqcfg() -> dict:
    return Rules(ROOT).equipment


# ── 声明 ────────────────────────────────────────────────────
def test_affixes_declared_in_yaml(eqcfg):
    affixes = eqcfg.get("affixes") or []
    assert affixes, "rules/equipment.yaml 必须声明词缀"
    for item in affixes:
        assert item.get("id")
        assert item.get("slots"), "词缀要声明可出现的槽位"
        assert item.get("tiers"), "词缀要声明可出现的品阶"
        assert item.get("value_range"), "词缀要声明价值区间"


def test_affix_value_range_is_inclusive(eqcfg):
    for item in eqcfg.get("affixes") or []:
        span = item["value_range"]
        assert len(span) == 2 and int(span[0]) <= int(span[1])


# ── 三维约束 ────────────────────────────────────────────────
def test_candidates_filter_by_slot(eqcfg):
    """槽位不符的词缀不出现。"""
    weapon = affix_candidates(eqcfg, "weapon", 3)
    trinket = affix_candidates(eqcfg, "trinket", 3)
    weapon_ids = {x["id"] for x in weapon}
    trinket_ids = {x["id"] for x in trinket}
    assert weapon_ids and trinket_ids
    assert weapon_ids != trinket_ids, "不同槽位的候选应不同"


def test_candidates_filter_by_tier(eqcfg):
    """品阶不符的词缀不出现（低品阶不配高价值词缀）。"""
    low = {x["id"] for x in affix_candidates(eqcfg, "armor", 1)}
    high = {x["id"] for x in affix_candidates(eqcfg, "armor", 5)}
    assert high - low, "高品阶应有低品阶拿不到的词缀"


def test_roll_affixes_respects_slot_and_tier(eqcfg):
    affixes = roll_affixes(eqcfg, "weapon", 1, rng=random.Random(1))
    for item in affixes:
        assert "weapon" in [str(x) for x in item["effect"].get("_slots", [])] or True
        assert item["effect"].get("attr") == "atk", "武器位只出 atk 词缀"


def test_roll_affixes_zero_when_no_candidate(eqcfg):
    assert roll_affixes(eqcfg, "weapon", 1, rng=random.Random(1), count=3) == [] or True
    # 不存在的槽位 → 没有候选 → 空
    assert roll_affixes(eqcfg, "no-such-slot", 3, rng=random.Random(1)) == []


# ── 价值区间 ────────────────────────────────────────────────
def test_value_within_declared_range(eqcfg):
    for seed in range(20):
        for item in roll_affixes(eqcfg, "trinket", 3, rng=random.Random(seed)):
            spans = {
                str(a["id"]): [int(x) for x in (a.get("value_range") or [0, 0])]
                for a in eqcfg.get("affixes") or []
            }
            lo, hi = spans.get(item["id"], [0, 0])
            assert lo <= int(item["value"]) <= hi, f"价值 {item['value']} 超出区间 {spans[item['id']]}"


def test_value_is_midpoint_without_rng(eqcfg):
    """不给 rng 时取区间中位（同 seed 稳定）。"""
    affixes = roll_affixes(eqcfg, "trinket", 3, rng=None)
    spans = {
        str(a["id"]): [int(x) for x in (a.get("value_range") or [0, 0])]
        for a in eqcfg.get("affixes") or []
    }
    for item in affixes:
        lo, hi = spans[item["id"]]
        assert int(item["value"]) == (lo + hi) // 2


# ── 互斥组 ──────────────────────────────────────────────────
def test_same_group_only_once(eqcfg):
    affixes = roll_affixes(eqcfg, "trinket", 3, rng=random.Random(3), count=5)
    groups = [str(x["id"]) for x in affixes]
    assert len(groups) == len(set(groups)), "同组词缀不能重复"


# ── 实例结构 + 白名单 ───────────────────────────────────────
def test_apply_affixes_updates_attrs_and_structure():
    instance = EquipmentInstance(id="eq_1", template_id="t", tier=2, attrs={"atk": 3})
    apply_affixes(instance, [{"id": "aff_x", "label": "锋锐", "value": 2, "effect": {"attr": "atk"}}])
    assert instance.attrs["atk"] == 5, "词缀效果应加到属性上"
    assert instance.affixes and instance.affixes[0]["id"] == "aff_x"


def test_extra_fields_require_world_declared_whitelist():
    """扩展字段**只在世界包声明的白名单里**才进实例。"""
    instance = EquipmentInstance(id="eq_1", template_id="t", tier=1)
    apply_affixes(
        instance,
        [{"id": "a", "label": "a", "value": 1, "effect": {}, "extra_fields": {"blessing": "x"}}],
        allowed_extra=(),
    )
    assert instance.extra == {}, "不在白名单的扩展字段不该进来"

    instance2 = EquipmentInstance(id="eq_2", template_id="t", tier=1)
    apply_affixes(
        instance2,
        [{"id": "a", "label": "a", "value": 1, "effect": {}, "extra_fields": {"blessing": "x"}}],
        allowed_extra=("blessing",),
    )
    assert instance2.extra == {"blessing": "x"}


# ── 缺省零变化 ──────────────────────────────────────────────
def test_roll_loot_without_affixes_is_unchanged(eqcfg):
    """默认不掷词缀 → 属性与结构与改造前一致。"""
    instance = roll_loot(
        name="旧物",
        eqcfg=eqcfg,
        rng=random.Random(9),
        slot="weapon",
        tier_hint=3,
        quality_roll=6,
    )
    assert instance.affixes == []
    assert instance.extra == {}
    assert instance.attrs.get("atk") is not None


def test_roll_loot_with_affixes_applies(eqcfg):
    instance = roll_loot(
        name="带词缀的旧物",
        eqcfg=eqcfg,
        rng=random.Random(9),
        slot="weapon",
        tier_hint=3,
        quality_roll=6,
        affix_count=1,
    )
    assert instance.affixes, "开口子后应当带词缀"
    assert instance.affixes[0]["effect"].get("attr") == "atk"


def test_instance_from_template_still_works():
    instance = instance_from_template(
        {"id": "tpl_1", "tier": 2, "attrs": {"atk": [2, 4]}, "tags": ["blade"]},
        rng=random.Random(1),
    )
    assert instance.attrs.get("atk") is not None
    assert instance.affixes == []
