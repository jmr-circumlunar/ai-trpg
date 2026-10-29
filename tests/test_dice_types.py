"""S1-2：骰型注册表（`trpg/rules/dice_types.py` + `rules/dice.yaml`）。

这里钉的是**机制与边界**，不是运气：

- 池运算：保留最高/最低 N 颗，丢弃的是被舍掉的那几颗；
- 条件成功计数：total = 成功次数，不是总和；
- 爆炸：触发条件正确、**必定终止**（轮数上限）、结果累加；
- 护栏：数量越界 / 阈值越界 / 未注册名字都要**报错**（不静默回落）；
- **缺省零变化**：老表达式路径 `NdS±M` 的行为与 rng_state 序列一字不改。
"""

from __future__ import annotations

import pytest

from trpg.rules.dice import DiceEngine, DiceError
from trpg.rules.dice_types import (
    RegisteredDiceType,
    bands_for,
    build_registry,
    clear_registry,
    get_dice_type,
    register_dice_type,
    registered_names,
)
from trpg.rules.loader import Rules

ROOT = "."


@pytest.fixture
def registry():
    """按 `rules/dice.yaml` 建表；每个用例后清空，避免用例间互相污染。"""
    rules = Rules(ROOT)
    built = build_registry(rules.dice_config)
    yield built
    clear_registry()


# ── 注册表本身 ──────────────────────────────────────────────
def test_registry_is_built_from_yaml(registry):
    names = registered_names()
    for want in ("basic", "pool", "success_count", "explode"):
        assert want in names, f"yaml 里声明的骰型没进注册表：{want}"
    assert registry["pool"].kind == "pool"
    assert registry["pool"].params["keep"] == 3


def test_unknown_dice_type_raises(registry):
    with pytest.raises(DiceError):
        get_dice_type("穿透必杀")


def test_register_dice_type_overrides_same_name(registry):
    register_dice_type("basic", RegisteredDiceType(name="basic", kind="basic", params={"sides": 8}))
    assert get_dice_type("basic").params["sides"] == 8


def test_limits_come_from_yaml(registry):
    limits = get_dice_type("basic").limits
    assert limits["max_count"] == 20
    assert limits["min_sides"] == 2


# ── 基础骰 ──────────────────────────────────────────────────
def test_basic_dice_type_sums(registry):
    engine = DiceEngine("seed-basic")
    result = engine.roll_type("basic", count=3, sides=6)
    assert len(result.rolls) == 3
    assert result.total == sum(result.rolls)
    assert all(1 <= r <= 6 for r in result.rolls)


# ── 池运算 ──────────────────────────────────────────────────
def test_pool_keeps_highest(registry):
    engine = DiceEngine("seed-pool")
    result = engine.roll_type("pool", count=4, sides=6, keep=3, keep_mode="highest")
    assert len(result.rolls) == 4, "被丢弃的骰面也要留在明细里（可复盘）"
    assert len(result.detail["kept"]) == 3
    assert len(result.detail["dropped"]) == 1
    assert result.total == sum(result.detail["kept"])
    assert min(result.detail["kept"]) >= max(result.detail["dropped"])


def test_pool_keeps_lowest(registry):
    engine = DiceEngine("seed-pool-low")
    result = engine.roll_type("pool", count=4, sides=6, keep=2, keep_mode="lowest")
    assert result.total == sum(result.detail["kept"])
    assert max(result.detail["kept"]) <= min(result.detail["dropped"])


def test_pool_rejects_keep_greater_than_count(registry):
    engine = DiceEngine("seed-pool-bad")
    with pytest.raises(DiceError):
        engine.roll_type("pool", count=2, sides=6, keep=5)


def test_pool_rejects_bad_keep_mode(registry):
    engine = DiceEngine("seed-pool-mode")
    with pytest.raises(DiceError):
        engine.roll_type("pool", count=2, sides=6, keep=1, keep_mode="middle")


# ── 条件成功计数 ────────────────────────────────────────────
def test_success_count_counts_hits(registry):
    engine = DiceEngine("seed-success")
    result = engine.roll_type("success_count", count=5, sides=6, threshold=5)
    assert result.total == len(result.detail["successes"])
    assert all(r >= 5 for r in result.detail["successes"])
    assert result.total <= 5


def test_success_count_rejects_out_of_range_threshold(registry):
    engine = DiceEngine("seed-success-bad")
    with pytest.raises(DiceError):
        engine.roll_type("success_count", count=2, sides=6, threshold=9)


# ── 爆炸骰 ──────────────────────────────────────────────────
def test_explode_on_max_face_accumulates(registry):
    engine = DiceEngine("seed-explode")
    result = engine.roll_type("explode", count=1, sides=6, threshold=None, max_rounds=50)
    assert result.total == sum(result.rolls)
    assert result.detail["exploded"] >= 0
    # 掷出 6 会继续爆：只要出现 6，展开的骰面就比初始一颗多
    if 6 in result.rolls:
        assert len(result.rolls) > 1


def test_explode_terminates(registry):
    """爆炸必须有终止保证：轮数上限到点就收尾，绝不死循环。"""
    engine = DiceEngine("seed-explode-stop")
    result = engine.roll_type("explode", count=1, sides=2, threshold=None, max_rounds=3)
    # 2 面骰一半概率爆炸：上限 3 轮 → 最多 1（初始）+ 3（重掷）= 4 颗
    assert len(result.rolls) <= 4
    assert result.detail["exploded"] <= 3


def test_explode_respects_global_cap(registry):
    """yaml 里的轮数上限不能超过全局护栏。"""
    engine = DiceEngine("seed-explode-cap")
    result = engine.roll_type("explode", count=1, sides=2, threshold=None, max_rounds=9999)
    assert len(result.rolls) <= 1 + int(registry["explode"].limits["max_explode_rounds"])


def test_explode_with_threshold(registry):
    engine = DiceEngine("seed-explode-th")
    result = engine.roll_type("explode", count=3, sides=6, threshold=5, max_rounds=10)
    assert result.total == sum(result.rolls)
    assert result.detail["threshold"] == 5


# ── 护栏 ────────────────────────────────────────────────────
def test_count_limit_is_enforced(registry):
    engine = DiceEngine("seed-limit")
    with pytest.raises(DiceError):
        engine.roll_type("basic", count=21, sides=6)
    with pytest.raises(DiceError):
        engine.roll_type("basic", count=0, sides=6)


def test_sides_limit_is_enforced(registry):
    engine = DiceEngine("seed-sides")
    with pytest.raises(DiceError):
        engine.roll_type("basic", count=1, sides=1)
    with pytest.raises(DiceError):
        engine.roll_type("basic", count=1, sides=1001)


# ── 缺省零变化 ──────────────────────────────────────────────
def test_legacy_expression_path_unchanged():
    """老路径 `NdS±M` 的结果与 rng 序列一字不改（golden 的前提）。"""
    engine = DiceEngine("legacy-seed")
    rolls, total = engine.roll("2d6+1")
    assert len(rolls) == 2
    assert total == sum(rolls) + 1
    assert engine.rng_state == "legacy-seed:2"
    assert engine.roll("1d20")[1] >= 1


def test_legacy_error_messages_unchanged():
    engine = DiceEngine("legacy-err")
    with pytest.raises(DiceError):
        engine.roll("abc")
    with pytest.raises(DiceError):
        engine.roll("21d6")


def test_registry_does_not_change_legacy_rng_sequence(registry):
    """注册表与老路径**共用同一个随机源**，但不该改变老路径的解析与汇总语义。

    - 注册表掷骰**之前**：同 seed 的两个 engine，老路径结果完全一致；
    - 注册表掷骰**之后**：随机源被推进（同一 engine 的后续掷骰自然不同），
      但那与「连续掷骰」是同一条语义，不是注册表引入的偏差。
    """
    a = DiceEngine("same-seed")
    b = DiceEngine("same-seed")
    assert a.roll("3d6") == b.roll("3d6"), "注册表未参与时老路径必须逐位一致"

    a.roll_type("pool", count=4, sides=6, keep=2)
    assert a.rng_state != b.rng_state, "注册表掷骰消耗随机数（同一 engine 的预期行为）"
    assert a.rng_state.startswith("same-seed:"), "rng_state 语义不变（仍是 seed:计数）"


# ── 档位映射 ────────────────────────────────────────────────
def test_bands_from_yaml():
    rules = Rules(ROOT)
    assert rules.dice_band_of(20) == "大成功"
    assert rules.dice_band_of(15) == "成功"
    assert rules.dice_band_of(3) == "失败"
    assert rules.dice_band_of(1) == "大失败"
    assert rules.dice_band_of(99) == "", "超出档位范围不猜"


def test_bands_for_helper():
    bands = [{"name": "高", "min": 5, "max": 6}]
    assert bands_for(6, bands) == "高"
    assert bands_for(1, bands) == ""
