"""S5-5：天气钩子 + 封锁触发器 + 建筑命名固化。

判据对应 39 号方案 S5-5：三件事各有测试；天气演化可复现；缺省零变化。
"""

from __future__ import annotations

from trpg.engine.events import EventScheduler, parse_action_spec
from trpg.engine.weather import advance_weather, next_weather, transitions_of
from trpg.map.worldgen import name_minor_buildings


# ── ① 天气演化钩子 ────────────────────────────────────────────


def test_weather_transitions_default_is_empty():
    assert transitions_of({}) == {}
    assert transitions_of(None) == {}


def test_weather_next_respects_transition_table():
    table = {"a": ["b"], "b": ["a"]}
    assert next_weather("a", table, turn=1, seed="s") == "b"


def test_weather_illegal_transition_keeps_current():
    table = {"a": ["b"]}
    assert next_weather("z", table, turn=1, seed="s") is None
    new, changed = advance_weather("z", table, turn=1, seed="s")
    assert (new, changed) == ("z", False)
    assert advance_weather("a", {}, turn=1, seed="s") == ("a", False)


def test_weather_evolution_is_reproducible():
    table = {"a": ["b", "c", "d"], "b": ["a"], "c": ["a"], "d": ["a"]}
    first = [next_weather("a", table, turn=t, seed="seed-1") for t in range(1, 8)]
    second = [next_weather("a", table, turn=t, seed="seed-1") for t in range(1, 8)]
    assert first == second, "同 seed 同 turn 必须同结果"


# ── ② 封锁走声明式触发器 ──────────────────────────────────────


def _spec(event_id, category, **extra):
    raw = {"id": event_id, "category": category, "intensity": 1}
    raw.update(extra)
    return parse_action_spec(raw)


def test_lock_blocks_category_until_it_expires():
    blocker = _spec("locker", "social", locks={"categories": ["discovery"], "duration_turns": 2})
    discovery = _spec("disc", "discovery")
    scheduler = EventScheduler([blocker, discovery], seed="s", budget=1)
    chosen = scheduler.select(turn=1)
    assert [s.id for s in chosen] == ["locker"], "强度相同按稳定 tie-break，先声明的先中"

    blocked = EventScheduler([blocker, discovery], seed="s", budget=1)
    blocked.select(turn=1)
    blocked.events = [discovery]
    assert blocked.select(turn=2) == [], "被封锁的类别在封锁期内不再发生"
    assert blocked.select(turn=4) != [], "封锁到期自动解除"


def test_lock_state_is_pure_code_and_reproducible():
    blocker = _spec("locker", "social", locks={"categories": ["discovery"], "duration_turns": 3})
    discovery = _spec("disc", "discovery")
    results = []
    for _ in range(2):
        scheduler = EventScheduler([blocker, discovery], seed="s", budget=1)
        scheduler.select(turn=1)
        results.append(dict(scheduler.blocks))
    assert results[0] == results[1], "封锁状态是纯代码记账，同输入必同结果"


def test_lock_without_duration_is_noop():
    plain = _spec("plain", "social")
    scheduler = EventScheduler([plain], seed="s", budget=1)
    scheduler.select(turn=1)
    assert scheduler.blocks == {}


def test_budget_zero_is_quiet_turn():
    scheduler = EventScheduler([_spec("a", "social")], seed="s", budget=0)
    assert scheduler.select(turn=1) == [], "预算 0 = 安静回合"


# ── ③ 次要建筑命名只发生一次 ──────────────────────────────────


def _map_with_secondary(count=2):
    nodes = [{"id": "loc_main", "label": "主场景", "kind": "primary", "exits": []}]
    for i in range(count):
        nodes.append(
            {"id": "loc_s%d" % i, "label": "旧巷", "kind": "secondary", "bearing": "北", "exits": []}
        )
    return {"region": "r", "nodes": nodes, "edges": []}


def test_minor_buildings_named_once_then_frozen():
    calls = []

    def namer(kind, bearing, label):
        calls.append((kind, bearing, label))
        return "命名结果%d" % len(calls)

    region_map = _map_with_secondary(2)
    _, first = name_minor_buildings(region_map, namer)
    assert first == 2, "两个次要建筑都该被命名"
    assert len(calls) == 2

    again, second = name_minor_buildings(region_map, namer)
    assert second == 0, "命名只发生一次：已命名的不会再命名"
    assert len(calls) == 2, "命名器不会被再次调用"
    assert again["nodes"][1]["label"] == "命名结果1", "名字固化，不会改口"


def test_no_namer_means_no_naming():
    region_map = _map_with_secondary(2)
    _, changed = name_minor_buildings(region_map, None)
    assert changed == 0, "没有命名器就不命名（行为零变化）"
    assert region_map["nodes"][1]["label"] == "旧巷"


def test_primary_locations_are_not_renamed():
    region_map = _map_with_secondary(1)
    _, changed = name_minor_buildings(region_map, lambda *a: "x")
    assert changed == 1, "只命名次要建筑，主场景不动"
    assert region_map["nodes"][0]["label"] == "主场景"
