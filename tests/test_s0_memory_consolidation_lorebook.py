"""S5-3：反思层（巩固）+ Lorebook 词条触发（与语义检索并存）。

判据对应 39 号方案 S5-3：
- **两形态各有测试**（巩固触发与产出 / 词条命中）；
- **未命中不注入有断言**；
- 缺省关闭 → 行为零变化。
"""

from __future__ import annotations

from pathlib import Path

from trpg.memory.consolidation import consolidate, render_consolidations, should_consolidate
from trpg.memory.lorebook import match_lorebook, parse_entries, render_lorebook
from trpg.memory.settings import MemorySettings
from trpg.models.memory import CharacterEvent

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


# ── 反思层 ────────────────────────────────────────────────────


def test_should_consolidate_by_turn_multiple():
    assert should_consolidate(10, every_n=5)
    assert not should_consolidate(11, every_n=5)
    assert not should_consolidate(3, every_n=0, importance_sum=0, threshold=0)


def test_should_consolidate_by_importance_threshold():
    assert should_consolidate(3, every_n=0, importance_sum=30, threshold=30)
    assert not should_consolidate(3, every_n=0, importance_sum=29, threshold=30)


def test_consolidate_clusters_shared_entities():
    events = [
        _event(1, "在东塔见到守卫", entities=["守卫"]),
        _event(2, "守卫提到密道", entities=["守卫"]),
        _event(3, "在集市买了面包", entities=["面包摊主"]),
    ]
    out = consolidate(events, "c1", 5)
    assert len(out) == 1, "只有共享实体的簇才成立"
    assert out[0].entities == ["守卫"]
    assert out[0].kind == "cognition"
    assert out[0].importance == 5
    assert len(out[0].event_refs) == 2


def test_consolidate_marks_rumor_when_relation_present():
    events = [
        _event(1, "守卫对我有敌意", kind="relation", entities=["守卫"]),
        _event(2, "守卫提到密道", entities=["守卫"]),
    ]
    out = consolidate(events, "c1", 5)
    assert out[0].kind == "rumor"


def test_consolidate_needs_at_least_two_events():
    events = [_event(1, "见到守卫", entities=["守卫"])]
    assert consolidate(events, "c1", 5) == [], "一条不算「多条」"


def test_consolidation_text_renders():
    events = [
        _event(1, "见到守卫", entities=["守卫"]),
        _event(2, "守卫提到密道", entities=["守卫"]),
    ]
    lines = render_consolidations(consolidate(events, "c1", 5))
    assert lines and "守卫" in lines[0]


def test_consolidation_default_is_off():
    settings = MemorySettings.load(ROOT)
    assert settings.consolidation_every_n == 0
    assert settings.consolidation_threshold == 0


# ── Lorebook 词条触发 ──────────────────────────────────────────


def test_parse_entries_skips_broken():
    entries = parse_entries(
        [
            {"id": "a", "keys": ["甲"], "content": "设定甲"},
            {"id": "b", "keys": [], "content": "无触发词"},
            {"id": "c", "keys": ["丙"], "content": ""},
        ]
    )
    assert [e.id for e in entries] == ["a"]


def test_lorebook_hit_injects():
    entries = parse_entries([{"id": "a", "keys": ["东塔"], "content": "东塔的设定"}])
    hits = match_lorebook(entries, ["我们往东塔去了"], max_entries=3)
    assert [h.id for h in hits] == ["a"]
    assert render_lorebook(hits) == ["（设定）东塔的设定"]


def test_lorebook_miss_does_not_inject():
    entries = parse_entries([{"id": "a", "keys": ["东塔"], "content": "东塔的设定"}])
    hits = match_lorebook(entries, ["我们往西塔去了"], max_entries=3)
    assert hits == [], "未命中不注入"
    assert render_lorebook(hits) == []


def test_lorebook_scan_depth_limits_window():
    entries = parse_entries([{"id": "a", "keys": ["东塔"], "content": "设定"}])
    texts = ["东塔", "西塔"]
    # 触发词只在更早的文本里，扫描深度 1 看不到 → 不命中
    assert match_lorebook(entries, texts, default_scan_depth=1) == []
    # 深度 2 时能命中
    assert len(match_lorebook(entries, texts, default_scan_depth=2)) == 1


def test_lorebook_entry_scan_depth_overrides_default():
    entries = parse_entries(
        [{"id": "a", "keys": ["东塔"], "content": "设定", "scan_depth": 2}]
    )
    texts = ["东塔", "西塔"]
    # 条目自己写死深度 2 → 覆盖全局默认 1
    assert len(match_lorebook(entries, texts, default_scan_depth=1)) == 1


def test_lorebook_sticky_keeps_firing():
    entries = parse_entries(
        [{"id": "a", "keys": ["东塔"], "content": "设定", "sticky": 2}]
    )
    state: dict[str, int] = {}
    assert len(match_lorebook(entries, ["东塔"], sticky_state=state)) == 1
    assert len(match_lorebook(entries, ["别的"], sticky_state=state)) == 1, "sticky 期间继续命中"
    assert len(match_lorebook(entries, ["别的"], sticky_state=state)) == 1
    assert match_lorebook(entries, ["别的"], sticky_state=state) == [], "sticky 用完就不再命中"


def test_lorebook_priority_order_and_cap():
    entries = parse_entries(
        [
            {"id": "low", "keys": ["塔"], "content": "低优先", "priority": 1},
            {"id": "high", "keys": ["塔"], "content": "高优先", "priority": 9},
        ]
    )
    hits = match_lorebook(entries, ["东塔"], max_entries=1)
    assert [h.id for h in hits] == ["high"]


def test_lorebook_no_entries_no_injection():
    assert match_lorebook([], ["东塔"]) == []
    assert render_lorebook([]) == []


def test_world_pack_has_lorebook_field():
    from trpg.world.pack import WorldPack

    assert "lorebook_entries" in WorldPack.__dataclass_fields__
    assert WorldPack.__dataclass_fields__["lorebook_entries"].default_factory is not None
