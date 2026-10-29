"""Director Agent：主线生成、固定事件时机、幕次推进。

边界（一期）：Director 只决定「发生什么骨架」，不写叙事、不给数值。
固定事件由 `timeline.yaml` 声明、代码按 turn 匹配，模型改不了。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trpg.engine.loop import GameLoop
from trpg.game import build_cards, build_state
from trpg.agents import Director, MockLLM
from trpg.rules.loader import Rules
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]

WORLD_YAML = """
world_id: dirtest
name: 导演测试地
default_era: modern
default_region: hub
eras:
  modern: {label: 现代, tier_max: 3}
regions:
  hub: {label: 中枢, entry: loc_hub}
"""

TIMELINE_YAML = """
timeline:
  - id: year1
    label: 第一年
    order: 1
    available: true
    start_clock: {day: 1, hour: 8}
    premise: 你刚报到，门卫看了一眼你的登记表。
    fixed_events:
      - {turn: 1, id: evt_open, locked: true}
      - {turn: 4, id: evt_four, locked: true}
  - id: year2
    label: 第二年
    order: 2
    available: false
"""

SEEDS_YAML = """
seeds:
  - {id: evt_open, era: [modern], stage_range: [1, 1], type: social, skeleton: 开学典礼上被点名, weight_base: 1.0}
  - {id: evt_four, era: [modern], stage_range: [1, 4], type: crisis, skeleton: 第四回合的意外, weight_base: 1.0}
  - {id: evt_any, era: [modern], stage_range: [1, 4], type: mystery, skeleton: 一份对不上的记录, weight_base: 1.0}
"""

REGION_MD = """
# 中枢

| id | 名称 | 钩子 |
|---|---|---|
| loc_hub | 中枢广场 | 一根没有影子的旗杆 |
"""


@pytest.fixture
def pack(tmp_path):
    base = tmp_path / "worlds" / "dirtest"
    (base / "regions").mkdir(parents=True)
    (base / "world.yaml").write_text(WORLD_YAML, encoding="utf-8")
    (base / "timeline.yaml").write_text(TIMELINE_YAML, encoding="utf-8")
    (base / "seeds.yaml").write_text(SEEDS_YAML, encoding="utf-8")
    (base / "regions" / "hub.md").write_text(REGION_MD, encoding="utf-8")
    return load_world("dirtest", tmp_path)


def _state(pack):
    cards = build_cards(1, pack, seed="1")
    return cards, build_state(pack, cards)


def test_open_session_makes_quests(pack):
    director = Director(pack, seed="1")
    cards, state = _state(pack)
    quests = director.open_session(state, cards)
    assert 1 <= len(quests) <= 3
    assert all(q["id"].startswith("q_") for q in quests)
    assert all(q["progress"] == 0 and q["status"] == "active" for q in quests)
    assert all(len(q["title"]) <= 18 for q in quests)


def test_open_session_is_seeded(pack):
    cards, state = _state(pack)
    a = Director(pack, seed="9").open_session(state, cards)
    b = Director(pack, seed="9").open_session(state, cards)
    assert [q["title"] for q in a] == [q["title"] for q in b]


def test_fixed_event_matches_turn(pack):
    director = Director(pack, seed="1")
    _, state = _state(pack)
    assert director.fixed_event(state, 1)["id"] == "evt_open"
    assert director.fixed_event(state, 4)["id"] == "evt_four"
    assert director.fixed_event(state, 2) is None


def test_plan_turn_stage_rhythm(pack):
    director = Director(pack, seed="1")
    _, state = _state(pack)
    assert director.plan_turn(state, 10)["advance_stage"] is True
    assert director.plan_turn(state, 9)["advance_stage"] is False


def test_loop_writes_quests_and_marks_fixed_event(pack):
    cards, state = _state(pack)
    director = Director(pack, seed="1")
    loop = GameLoop(
        Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack, director=director
    )
    asyncio.run(loop.run(2))

    assert loop.state.quests, "开场主线必须写进 quests"
    assert director.log[0]["turn"] == 0
    turn1 = [row for row in director.log if row.get("turn") == 1]
    assert turn1 and turn1[0]["source"] == "fixed_event"
    assert loop.state.stage == 1, "两回合内不该推幕次"


def test_loop_without_director_still_runs(pack):
    """没接 Director 时保持旧行为（不报错、不写 quests）。"""
    cards, state = _state(pack)
    loop = GameLoop(Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack)
    asyncio.run(loop.run(1))
    assert loop.state.quests == []
