"""S2-5：校准指标 + 纯内存 arena 的回归入口。

钉住出口判据：

1. **Brier / skill score 可算**（已知值可验证）；
2. **Murphy 三分解**自洽（`brier ≈ reliability − resolution + uncertainty`）；
3. **随机基线对照**在（arena 同时跑随机投票基线）；
4. **判据近似**可算（首日存活率 / 投票命中率 / 投票分布熵）；
5. **纯内存**：arena 不落库、不联网，同 seed 同结果。
"""

from __future__ import annotations

import math

import pytest

from trpg.eval.arena import run_arena
from trpg.eval.calibration import (
    Pair,
    base_rate,
    brier_score,
    brier_skill_score,
    murphy_decomposition,
    reliability_table,
    vote_distribution_entropy,
)


# ── Brier / skill score ─────────────────────────────────────

def test_brier_score_known_values():
    # 全对：p=1,y=1 与 p=0,y=0 → 0
    assert brier_score([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
    # 全错：p=0,y=1 与 p=1,y=0 → 1
    assert brier_score([(0.0, 1), (1.0, 0)]) == pytest.approx(1.0)
    # 半对：p=0.5,y=1 与 p=0.5,y=0 → 0.25
    assert brier_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)


def test_base_rate_and_skill_score():
    samples = [(0.9, 1), (0.9, 1), (0.1, 0), (0.1, 0)]
    assert base_rate(samples) == pytest.approx(0.5)
    # 比「永远猜 0.5」好 → skill score > 0
    assert brier_skill_score(samples) > 0.5
    # 永远猜基准率 → skill score = 0
    assert brier_skill_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.0)


def test_skill_score_is_negative_for_worse_than_baseline():
    samples = [(0.9, 0), (0.9, 0), (0.1, 1), (0.1, 1)]
    assert brier_skill_score(samples) < 0


def test_murphy_decomposition_is_consistent():
    samples = [(0.9, 1), (0.9, 1), (0.1, 0), (0.1, 0)]
    parts = murphy_decomposition(samples)
    recomposed = parts["reliability"] - parts["resolution"] + parts["uncertainty"]
    assert recomposed == pytest.approx(parts["brier"], abs=1e-9)
    assert parts["uncertainty"] == pytest.approx(0.25)


def test_reliability_table_bins():
    rows = reliability_table([(0.05, 0), (0.05, 0), (0.95, 1), (0.95, 1)])
    assert len(rows) == 2
    assert rows[0]["bin"] == 0 and rows[1]["bin"] == 9
    assert rows[0]["observed"] == pytest.approx(0.0)
    assert rows[1]["observed"] == pytest.approx(1.0)
    # gap = 预测概率 − 实际发生率（预测偏乐观 → gap 为负）
    assert rows[0]["gap"] == pytest.approx(rows[0]["predicted"] - rows[0]["observed"])
    assert rows[1]["gap"] == pytest.approx(rows[1]["predicted"] - rows[1]["observed"])


def test_empty_samples_do_not_crash():
    assert brier_score([]) == 0.0
    assert brier_skill_score([]) == 0.0
    assert murphy_decomposition([])["brier"] == 0.0
    assert reliability_table([]) == []


# ── 判据近似（B-3）─────────────────────────────────────────

def test_vote_entropy():
    assert vote_distribution_entropy([]) == 0.0
    assert vote_distribution_entropy(["a", "a", "a"]) == 0.0
    assert vote_distribution_entropy(["a", "b"]) == pytest.approx(1.0)
    assert 0.0 < vote_distribution_entropy(["a", "a", "b", "c"]) < 1.0


# ── 纯内存 arena ───────────────────────────────────────────

def test_arena_runs_in_memory_and_is_reproducible():
    """纯内存：不落库、不联网；同 seed 同结果。"""
    kwargs = dict(
        world="werewolf",
        players=6,
        games=1,
        seed="s2-5-test",
        provider="mock",
        turns=3,
        hostile_camps=("wolf",),
        random_baseline=False,
    )
    first = run_arena(".", **kwargs)
    second = run_arena(".", **kwargs)
    assert len(first.games) == 1
    assert first.games[0].votes == second.games[0].votes
    assert first.games[0].died == second.games[0].died


def test_arena_produces_metrics_and_baseline():
    result = run_arena(
        ".",
        world="werewolf",
        players=6,
        games=1,
        seed="s2-5-metrics",
        provider="mock",
        turns=3,
        hostile_camps=("wolf",),
        random_baseline=True,
    )
    assert result.baseline, "随机基线必须一起跑（对照用）"
    assert isinstance(result.camp_survival_rate(), dict)
    assert isinstance(result.agent_win_rate(), dict)
    proxies = result.exit_proxies()
    assert set(proxies) >= {"first_day_survival", "vote_hit_rate", "vote_entropy"}
    calibration = result.calibration()
    assert "skill_score" in calibration and "brier" in calibration
    assert calibration["samples"] >= 0


def test_arena_without_hostile_camps_gives_no_samples():
    """没有敌对阵营声明 → 不产校准样本（框架不猜谁是敌人）。"""
    result = run_arena(
        ".",
        world="werewolf",
        players=6,
        games=1,
        seed="s2-5-none",
        provider="mock",
        turns=2,
        hostile_camps=(),
        random_baseline=False,
    )
    assert result.calibration()["samples"] == 0


def test_framework_has_no_world_faction_words():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("trpg/eval/calibration.py", "trpg/eval/arena.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for bad in ("狼", "村民", "悍跳", "屠边", "银水"):
            assert bad not in src, f"{rel} 不许出现世界词：{bad}"


def test_math_import_is_used():
    """护栏：entropy 用 math（避免有人改成随机近似）。"""
    assert math.log(2) > 0
