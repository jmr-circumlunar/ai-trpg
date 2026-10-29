"""S5-2：三因子打分（时衰 + 重要度 + 相关性）+ 矛盾标记（标冲突，不覆盖）。

判据对应 39 号方案 S5-2：
- 三因子与矛盾**各有测试**；
- **缺省零变化**（`scoring_mode=rrf` → 原 RRF 路径逐字段不变）。
"""

from __future__ import annotations

from pathlib import Path

from trpg.memory.contradiction import detect_conflicts, looks_like_conflict
from trpg.memory.decay import combine, importance_score, recency_score, relevance_score
from trpg.memory.recall import hybrid_recall
from trpg.memory.settings import MemorySettings
from trpg.models.memory import CharacterEvent
from trpg.storage.memory import EventStore

ROOT = Path(__file__).resolve().parent.parent


def _event(turn, summary, importance=5, kind="discovery", entities=None):
    return CharacterEvent(
        session_id="s1",
        char_id="c1",
        turn=turn,
        kind=kind,
        summary=summary,
        entities=list(entities or []),
        importance=importance,
    )


def _store(*events):
    store = EventStore()
    for event in events:
        store.add(event)
    return store


# ── 算子 ────────────────────────────────────────────────────────


def test_recency_decays_exponentially():
    assert recency_score(10, 10, 20) == 1.0
    assert abs(recency_score(0, 20, 20) - 0.5) < 1e-9
    assert recency_score(0, 40, 20) == 0.25
    # 半衰期 <= 0 → 不衰减（稳定事实不因久远而褪色）
    assert recency_score(0, 999, 0) == 1.0


def test_importance_and_relevance_are_normalized():
    assert importance_score(10, 10) == 1.0
    assert importance_score(0, 10) == 0.0
    assert relevance_score(0.5, 0.0) == 0.5
    assert relevance_score(1.0, 0.0) == 1.0
    assert relevance_score(0.1, 0.3) == 0.0  # 低于阈值 → 0
    value = combine(0.5, 0.5, 0.5, {"relevance": 0.6, "importance": 0.2, "recency": 0.2})
    assert 0.0 <= value <= 1.0


# ── 三因子排序 ──────────────────────────────────────────────────


def test_three_factor_orders_by_importance_when_weighted():
    events = _store(
        _event(1, "旧事", importance=5),
        _event(9, "新事", importance=2),
        _event(5, "要紧的事", importance=9),
    )
    result = hybrid_recall(
        "我要想起什么",
        "c1",
        events,
        None,
        limit=3,
        important_limit=3,
        important_threshold=1,
        recent_limit=3,
        scoring_mode="three_factor",
        score_weights={"relevance": 0.0, "importance": 1.0, "recency": 0.0},
        half_life_turns=20,
        importance_scale=10,
    )
    assert result.debug["scoring_mode"] == "three_factor"
    assert result.items[0].text.endswith("要紧的事"), "只按重要度加权时，最重要的排第一"
    assert result.items[0].factors["importance"] == 0.9


def test_three_factor_orders_by_recency_when_weighted():
    events = _store(
        _event(1, "旧事", importance=9),
        _event(9, "新事", importance=2),
    )
    result = hybrid_recall(
        "查询",
        "c1",
        events,
        None,
        limit=2,
        important_limit=3,
        important_threshold=1,
        recent_limit=3,
        scoring_mode="three_factor",
        score_weights={"relevance": 0.0, "importance": 0.0, "recency": 1.0},
        half_life_turns=20,
        importance_scale=10,
    )
    assert result.items[0].text.endswith("新事"), "只按时衰加权时，最近的排第一"


def test_three_factor_weights_come_from_config():
    settings = MemorySettings.load(ROOT)
    # 缺省配置 = RRF（行为零变化）
    assert settings.scoring_mode == "rrf"
    assert settings.weight_relevance == 0.6
    assert settings.weight_importance == 0.2
    assert settings.weight_recency == 0.2
    assert settings.half_life_turns == 20


# ── 矛盾标记 ────────────────────────────────────────────────────


def test_looks_like_conflict_same_entity_different_fact():
    left = {"kind": "discovery", "entities": ["守卫"], "summary": "守卫在东塔"}
    right = {"kind": "discovery", "entities": ["守卫"], "summary": "守卫在西塔"}
    assert looks_like_conflict(left, right)
    # 不同类型不算矛盾
    assert not looks_like_conflict(left, {**right, "kind": "action"})
    # 同一件事不算矛盾
    assert not looks_like_conflict(left, {**right, "summary": left["summary"]})


def test_detect_conflicts_marks_older_entry_only():
    entries = [
        {"ref": "turn1", "kind": "discovery", "entities": ["守卫"], "summary": "守卫在东塔", "turn": 1, "importance": 5},
        {"ref": "turn7", "kind": "discovery", "entities": ["守卫"], "summary": "守卫在西塔", "turn": 7, "importance": 5},
    ]
    conflicts = detect_conflicts(entries)
    assert conflicts == {"turn1": ["turn7"]}, "只标旧版本，新版本不标"


def test_conflict_is_marked_not_overwritten():
    events = _store(
        _event(1, "守卫在东塔", kind="discovery", entities=["守卫"]),
        _event(7, "守卫在西塔", kind="discovery", entities=["守卫"]),
    )
    result = hybrid_recall(
        "守卫在哪",
        "c1",
        events,
        None,
        limit=5,
        important_limit=3,
        important_threshold=1,
        recent_limit=5,
        scoring_mode="three_factor",
        score_weights={"relevance": 0.0, "importance": 1.0, "recency": 0.0},
        conflict_enabled=True,
    )
    texts = [i.text for i in result.items]
    assert len(texts) == 2, "矛盾不覆盖：两条都要在"
    assert any(i.conflict for i in result.items), "旧版本应被标冲突"
    marked = result.texts(limit=5)
    assert any("与另一条记忆不一致" in t for t in marked), "冲突标记要进 prompt 文本"


# ── 缺省零变化 ──────────────────────────────────────────────────


def test_default_path_is_unchanged_rrf():
    events = _store(
        _event(1, "旧事", importance=5),
        _event(7, "新事", importance=8),
    )
    result = hybrid_recall(
        "查询",
        "c1",
        events,
        None,
        limit=3,
        important_limit=3,
        important_threshold=1,
        recent_limit=3,
    )
    assert "scoring_mode" not in result.debug, "缺省仍走原 RRF 路径"
    assert result.debug["rrf_k"] == 60
    # RRF 贡献：1/(60 + rank + 1)；这条同时命中 important 与 recent 两路 → 两份贡献相加
    assert abs(result.items[0].score - (1.0 / 61.0 + 1.0 / 62.0)) < 1e-9
    assert not any(i.conflict for i in result.items)
    assert result.texts(limit=3) == [i.text for i in result.items], "无冲突时文本不带标记"
