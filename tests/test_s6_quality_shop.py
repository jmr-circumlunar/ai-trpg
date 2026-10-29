"""S6-2：品质概率表（按幕次）+ 商店/带入支持 item 与 path。

判据对应 39 号方案 S6-2：两处 continue 已消；分布有测试；缺省零变化。
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from trpg.equipment.roll import quality_by_stage, quality_from_roll
from trpg.growth import shop as meta_shop
from trpg.growth import carry as carry_mod
from trpg.growth.hooks import MetaHooks, load_hooks
from trpg.growth.wallet import Wallet
from trpg.models.enums import Quality
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]
LONGZU = ROOT / "worlds" / "longzu"

from support.builders import make_card


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    from trpg.storage import db

    db.init_db(c)
    yield c
    c.close()


def _pack():
    return load_world("longzu", ROOT)


# ── ① 品质权重按幕次 ──────────────────────────────────────────


def test_quality_by_stage_returns_none_without_table():
    assert quality_by_stage({}, 1, rng=random.Random(1)) is None
    assert quality_by_stage({"quality_weights": {"by_stage": {}}}, 1, rng=random.Random(1)) is None


def test_quality_by_stage_follows_weights():
    cfg = {"quality_weights": {"by_stage": {1: {"rough": 100, "fine": 0}}}}
    assert quality_by_stage(cfg, 1, rng=random.Random(7)) == Quality.rough
    cfg2 = {"quality_weights": {"by_stage": {1: {"rough": 0, "fine": 100}}}}
    assert quality_by_stage(cfg2, 1, rng=random.Random(7)) == Quality.fine


def test_quality_by_stage_is_reproducible():
    cfg = {"quality_weights": {"by_stage": {1: {"rough": 50, "common": 30, "fine": 20}}}}
    first = quality_by_stage(cfg, 1, rng=random.Random("s"))
    second = quality_by_stage(cfg, 1, rng=random.Random("s"))
    assert first == second, "同 seed 同输入必须同结果"


def test_quality_by_stage_respects_legendary_tension_gate():
    cfg = {
        "quality_weights": {"by_stage": {1: {"legendary": 100}}},
        "quality": {"legendary": {"require": {"tension_gt": 70}}},
    }
    assert quality_by_stage(cfg, 1, rng=random.Random(3), tension=0) == Quality.rare
    assert quality_by_stage(cfg, 1, rng=random.Random(3), tension=99) == Quality.legendary


def test_default_quality_path_unchanged():
    """缺省（无权重表）仍旧是 2d6 映射：品质分布不受本步影响。"""
    eqcfg = {
        "quality": {
            "rough": {"roll": [2, 5]},
            "common": {"roll": [6, 8]},
            "fine": {"roll": [9, 10]},
            "rare": {"roll": [11, 12]},
        }
    }
    assert quality_from_roll(3, eqcfg) == Quality.rough
    assert quality_from_roll(7, eqcfg) == Quality.common
    assert quality_from_roll(12, eqcfg) == Quality.rare


# ── ② 商店支持 item / path ────────────────────────────────────


def test_can_apply_supports_item_and_path():
    ok, why = meta_shop.can_apply(None, None, "item", "some_item", {})
    assert ok and not why
    ok, why = meta_shop.can_apply(None, None, "path", "some_path", {})
    assert ok and not why
    ok, why = meta_shop.can_apply(None, None, "unknown_kind", "x", {})
    assert not ok and why


def test_apply_effect_writes_item_and_path():
    card = make_card("chr_x")
    assert meta_shop.apply_effect(card, None, "item", "old_lantern", {}) == "old_lantern"
    assert "old_lantern" in list(getattr(card.ext, "carried", []) or [])
    assert meta_shop.apply_effect(card, None, "path", "path_a", {}) == "path_a"
    assert card.ext.path == "path_a"


def test_purchase_item_does_not_deduct_on_failure(conn):
    """先校验后扣点：校验失败不扣点（退款语义）。"""
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("point", 5, world_id="longzu")
    card = make_card("chr_x")
    res = meta_shop.purchase(hooks, w, _pack(), [], card, "item", "not_exists_in_world")
    assert not res.get("ok")
    assert w.balance("point", world_id="longzu") == 5, "失败不该扣点"


# ── ③ 带入支持 item / path ────────────────────────────────────


def _carry_hooks(monkeypatch):
    """世界包声明能带什么；这里用合成声明隔离「世界侧校验」。

    世界侧校验（目标必须存在于本世界）由 resolve_carry_targets 负责，
    这里只测**机制**：kind=item / path 不再被 continue 跳过。
    """
    rules = [
        {"ok": True, "kind": "item", "target": "old_lantern"},
        {"ok": True, "kind": "path", "target": "path_a"},
    ]
    monkeypatch.setattr(carry_mod, "resolve_carry_targets", lambda hooks, pack: rules)
    return MetaHooks(world_id="longzu")


def test_carry_supports_item_and_path(conn, monkeypatch):
    hooks = _carry_hooks(monkeypatch)
    w = Wallet(conn)
    w.add("item:old_lantern", 2, world_id="longzu")
    w.add("path:path_a", 1, world_id="longzu")
    card = make_card("chr_x")
    applied = carry_mod.apply_carry(hooks, w, card, _pack())
    kinds = {row["kind"] for row in applied}
    assert "item" in kinds and "path" in kinds, "带入要支持 item 与 path"
    assert "old_lantern" in list(getattr(card.ext, "carried", []) or [])
    assert card.ext.path == "path_a"


def test_carry_does_not_duplicate_item(conn, monkeypatch):
    hooks = _carry_hooks(monkeypatch)
    w = Wallet(conn)
    w.add("item:old_lantern", 2, world_id="longzu")
    card = make_card("chr_x")
    carry_mod.apply_carry(hooks, w, card, _pack())
    carry_mod.apply_carry(hooks, w, card, _pack())
    carried = list(getattr(card.ext, "carried", []) or [])
    assert carried.count("old_lantern") == 1, "重复开局不能重复注入"
