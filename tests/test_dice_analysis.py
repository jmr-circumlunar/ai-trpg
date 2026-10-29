"""S1-3：骰子概率分析（`analyze`）+ 判定档位表（`rules/ruleset.yaml: outcomes`）。

三条要钉住的事：

1. **分析必须与实际掷骰一致** —— 用抽样交叉验证（经验频率 vs 理论分布）；
2. **四档行为零变化** —— 穷举 face × margin，新旧判定逐格相等；
3. **`when` 只允许命名谓词** —— 未知谓词要报错，不执行任何表达式。
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from trpg.models.enums import Outcome
from trpg.rules.dice import DiceEngine, DiceError
from trpg.rules.loader import Rules
from trpg.rules.outcomes import (
    OutcomeContext,
    OutcomeError,
    evaluate_outcome,
    outcome_label,
    outcome_labels,
    predicate_names,
)

ROOT = "."


@pytest.fixture
def rules() -> Rules:
    return Rules(ROOT)


# ── analyze：分布正确性 ─────────────────────────────────────
def test_analyze_uniform_d20(rules):
    result = DiceEngine("an-1").analyze("1d20")
    assert result["count"] == 1
    assert result["sides"] == 20
    assert result["min"] == 1 and result["max"] == 20
    assert len(result["distribution"]) == 20
    assert all(abs(p - 0.05) < 1e-9 for p in result["distribution"].values())
    assert abs(sum(result["distribution"].values()) - 1.0) < 1e-9
    assert abs(result["mean"] - 10.5) < 1e-9
    assert abs(result["stddev"] - (20 ** 2 - 1) ** 0.5 / 12 ** 0.5) < 1e-6


def test_analyze_two_d6_counts_match_combinatorics(rules):
    result = DiceEngine("an-2").analyze("2d6")
    dist = result["distribution"]
    assert len(dist) == 11
    assert abs(dist[7] - 6 / 36) < 1e-9
    assert abs(dist[2] - 1 / 36) < 1e-9
    assert abs(dist[12] - 1 / 36) < 1e-9
    assert abs(result["mean"] - 7.0) < 1e-9


def test_analyze_applies_modifier(rules):
    result = DiceEngine("an-3").analyze("1d20+2")
    assert result["min"] == 3 and result["max"] == 22
    assert abs(result["mean"] - 12.5) < 1e-9
    assert abs(result["modifier"] - 2) < 1e-9


def test_analyze_rejects_bad_expression(rules):
    engine = DiceEngine("an-4")
    with pytest.raises(DiceError):
        engine.analyze("abc")
    with pytest.raises(DiceError):
        engine.analyze("21d6")


def test_analyze_rejects_huge_expression(rules):
    """枚举规模有护栏：不能被一句表达式拖垮。"""
    with pytest.raises(DiceError):
        DiceEngine("an-5").analyze("20d1000")


# ── analyze 与实际掷骰一致（抽样交叉验证）──────────────────
def test_analyze_matches_empirical_sampling(rules):
    """经验频率必须逼近理论分布（2 万次抽样，最大偏差 < 3%）。"""
    engine = DiceEngine("an-sample")
    theory = engine.analyze("1d20")["distribution"]
    counts: Counter[int] = Counter()
    trials = 20000
    for _ in range(trials):
        rolls, _total = engine.roll("1d20")
        counts[rolls[0]] += 1
    worst = max(abs(counts[face] / trials - theory[face]) for face in theory)
    assert worst < 0.03, f"理论分布与实测偏差过大：{worst:.4f}"


def test_analyze_supports_match_rolls(rules):
    """分析给出的每个总值，真实掷骰都能掷出来（支持集一致）。"""
    engine = DiceEngine("an-support")
    theory = engine.analyze("2d6")["distribution"]
    seen = set()
    for _ in range(3000):
        rolls, total = engine.roll("2d6")
        seen.add(total)
        assert total in theory, f"掷出理论分布里没有的总数：{total}"
    assert seen == set(theory), "实测支持集与理论支持集不一致"


# ── 判定档位表 ──────────────────────────────────────────────
def test_outcomes_table_shape(rules):
    ids = [str(item.get("id")) for item in rules.outcomes]
    assert ids == ["crit_success", "success", "fail_forward", "failure"]
    for item in rules.outcomes:
        assert item.get("label"), f"档位 {item.get('id')} 缺中文 label"
        assert item.get("when") in predicate_names(), f"用了未注册的谓词：{item.get('when')}"
    assert rules.outcome_label("crit_success") == "大成功"
    assert rules.outcome_label("success") == "成功"
    assert rules.outcome_label("不存在的档位") == "", "查不到不猜"


def test_outcome_ids_match_enum(rules):
    """yaml 的 id 必须与 `Outcome` 枚举一一对应（否则前端拿不到）。"""
    enum_ids = {item.value for item in Outcome}
    table_ids = {str(item.get("id")) for item in rules.outcomes}
    assert table_ids == enum_ids


def test_outcome_labels_map(rules):
    labels = rules.outcome_labels()
    assert labels["failure"] == "失败"
    assert set(labels) == {str(item.get("id")) for item in rules.outcomes}


def test_outcome_for_matches_legacy_logic(rules):
    """**四档行为零变化**：穷举 face × margin，新旧判定逐格相等。"""

    def legacy(face: int, margin: int) -> str:
        if face == rules.crit_face or margin >= rules.crit_margin:
            return "crit_success"
        if margin >= 0:
            return "success"
        if margin >= rules.fail_forward_floor:
            return "fail_forward"
        return "failure"

    checked = 0
    for face in range(1, rules.dice_sides + 1):
        for margin in range(-30, 31):
            assert rules.outcome_for(face, margin) == legacy(face, margin), (
                f"判定变了：face={face} margin={margin}"
            )
            checked += 1
    assert checked == rules.dice_sides * 61


def test_outcome_examples(rules):
    assert rules.outcome_for(20, 0) == "crit_success"   # 掷出关键面
    assert rules.outcome_for(5, 12) == "crit_success"    # 余量够大
    assert rules.outcome_for(5, 0) == "success"
    assert rules.outcome_for(5, -1) == "fail_forward"
    assert rules.outcome_for(5, -9) == "failure"


def test_unknown_predicate_raises(rules):
    bad = [{"id": "x", "label": "X", "when": "world_pack_expression"}]
    with pytest.raises(OutcomeError):
        evaluate_outcome(bad, OutcomeContext(1, 0, 20, 10, -2))


def test_missing_fallback_raises(rules):
    bad = [{"id": "x", "label": "X", "when": "margin_at_least_zero"}]
    with pytest.raises(OutcomeError):
        evaluate_outcome(bad, OutcomeContext(1, -9, 20, 10, -2))


def test_outcome_label_helpers(rules):
    items = rules.outcomes
    assert outcome_label(items, "success") == "成功"
    assert outcome_label(items, "?") == ""
    assert outcome_labels(items)["failure"] == "失败"


# ── 概率分析给判定用（智力步骤要有数字）─────────────────────
def test_success_probability_against_dc(rules):
    """给一个具体 DC，算出成功率（阶段 2 的信念模型要用这类数字）。"""
    engine = DiceEngine("an-prob")
    dist = engine.analyze("1d20")["distribution"]
    dc = 12
    success = sum(p for value, p in dist.items() if value >= dc)
    crit = sum(p for value, p in dist.items() if value == rules.crit_face)
    assert 0.4 < success < 0.5, f"DC 12 的成功率应在 45% 左右，实际 {success:.3f}"
    assert abs(crit - 0.05) < 1e-9


def test_sampling_matches_theory_for_2d6(rules):
    engine = DiceEngine("an-sample-2")
    theory = engine.analyze("2d6")["distribution"]
    counts: Counter[int] = Counter()
    trials = 20000
    for _ in range(trials):
        _rolls, total = engine.roll("2d6")
        counts[total] += 1
    worst = max(abs(counts[total] / trials - theory[total]) for total in theory)
    assert worst < 0.03, f"2d6 分布与实测偏差过大：{worst:.4f}"
    # 理论期望 7，实测均值也应接近
    assert abs(sum(total * counts[total] for total in counts) / trials - 7.0) < 0.06
    _ = random  # 保持 import（抽样用 engine 的可复现随机源，不用全局 random）
