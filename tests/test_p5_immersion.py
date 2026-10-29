"""P5 上半：种子空间约束 / 按幕补充 / 分层，以及介入规则的纯函数。

这三个是「学院里凭空跑出实验体、打几回合没人来」的根因修复：
- 种子没有空间约束 → 教室也能抽到宿舍楼后巷的实验体
- `_used_seeds` 永不清空 → 幕次 1 只有 6 条合格种子，第 6 回合后无剧情骨架
- 介入靠 GM 自觉 → 有秩序的地方出了事也没人来
"""

from __future__ import annotations

import random

import pytest

from trpg.rules import immersion
from trpg.engine.loop import GameLoop
from trpg.agents import MockLLM
from trpg.agents.agent import AgentLLM
from trpg.rules.loader import Rules
from support.builders import make_card, make_state

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _picker(seeds: list[dict]) -> AgentLLM:
    llm = object.__new__(AgentLLM)
    llm.seeds = seeds
    llm._used_seeds = set()
    llm._seed_stage = 0
    llm._last_seed_id = ""
    llm.seed_rng = random.Random(7)
    llm.seed_log = []
    return llm


def _state(region="campus", location="loc_gate", stage=1, era="academy"):
    state = make_state(make_card())
    state.scene.region = region
    state.scene.location_id = location
    state.stage = stage
    state.world.era = era
    return state


# ── 种子空间约束 ────────────────────────────────────────────
def test_seed_respects_region():
    seeds = [
        {
            "id": "evt_stray",
            "era": ["academy"],
            "stage_range": [1, 2],
            "regions": ["dorm"],
        }
    ]
    assert _picker(seeds)._pick_seed(_state(region="campus")) is None, (
        "教室不该抽到宿舍楼后巷的实验体"
    )
    assert _picker(seeds)._pick_seed(_state(region="dorm")) is not None


def test_seed_respects_location():
    seeds = [{"id": "s", "era": ["academy"], "locations": ["loc_elevator"]}]
    assert _picker(seeds)._pick_seed(_state(location="loc_gate")) is None
    assert _picker(seeds)._pick_seed(_state(location="loc_elevator")) is not None


def test_seed_without_spatial_fields_still_works():
    seeds = [{"id": "s", "era": ["academy"]}]
    assert _picker(seeds)._pick_seed(_state(region="campus")) is not None, (
        "没写空间约束的种子保持向后兼容"
    )


# ── 种子用尽 ────────────────────────────────────────────────
def test_stage_change_refills_seed_pool():
    seeds = [{"id": "s1", "era": ["academy"], "stage_range": [1, 4]}]
    llm = _picker(seeds)
    assert llm._pick_seed(_state(stage=1)) is not None
    assert llm._pick_seed(_state(stage=1)) is None, "同一幕内 main 种子只用一次"

    # 换幕：清空已用集合，于是又有骨架可抽（这就是修「第 6 回合后失速」的地方）
    assert llm._pick_seed(_state(stage=2)) is not None


def test_ambient_seed_can_repeat():
    seeds = [{"id": "amb", "era": ["academy"], "tier": "ambient"}]
    llm = _picker(seeds)
    assert llm._pick_seed(_state()) is not None
    # 氛围种子可复用，但避免连续两次同一条
    assert llm._pick_seed(_state()) is None


# ── 介入规则 ────────────────────────────────────────────────
def test_responders_fire_after_combat_in_high_order():
    out = immersion.responders(
        turn=4,
        combat_turns=[3],
        tension=10,
        order="high",
        authority=["npc_schneider"],
        cfg={"turns_by_order": {"high": 2}, "tension_threshold": 45, "cooldown_turns": 3},
    )
    assert out == ["npc_schneider"]


def test_responders_never_in_lawless_region():
    assert (
        immersion.responders(
            turn=4,
            combat_turns=[3],
            tension=99,
            order="none",
            authority=["npc_x"],
            cfg={"turns_by_order": {"high": 2}},
        )
        == []
    ), "无秩序区域不该凭空来人"


def test_responders_respect_cooldown():
    cfg = {"turns_by_order": {"high": 2}, "cooldown_turns": 3, "tension_threshold": 45}
    assert (
        immersion.responders(
            turn=5,
            combat_turns=[4],
            tension=0,
            order="high",
            authority=["npc_x"],
            cfg=cfg,
            last_response_turn=4,
        )
        == []
    ), "刚派过人不要每回合都来一队"


def test_responders_fire_on_high_tension():
    cfg = {"turns_by_order": {"medium": 3}, "tension_threshold": 45, "cooldown_turns": 3}
    assert immersion.responders(6, [], 60, "medium", ["npc_watch"], cfg) == ["npc_watch"]
    assert immersion.responders(6, [], 10, "medium", ["npc_watch"], cfg) == []


def test_loop_pulls_authority_into_present(monkeypatch):
    """打起来之后，代码会把 authority 强制塞进在场名单（GM 拒绝无效）。"""
    pack = __import__("trpg.world", fromlist=["load_world"]).load_world("longzu", ROOT)
    cards = {"chr_p1": make_card()}
    from trpg.game import build_state

    state = build_state(pack, cards, seed="1")
    loop = GameLoop(Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack)
    loop._combat_turns = [1]

    got = loop._responders_for(2)
    assert got == ["npc_schneider"], f"学院主区 2 回合内必到，实际 {got}"
