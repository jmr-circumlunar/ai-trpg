"""S5-4：事件声明 + 区域自治 + 动作三态 + 纯代码调度器 + GM schema 裁剪。

出口判据逐条对应：
1. **调度纯代码零 LLM**：同 seed 同结果（抽样对照）；
2. **打分与冷却**：冷却期内同一事件不再出现；
3. **安静回合可复现**：预算 0 → 无事件；
4. **动作三态**：`text` 确定性文本 / `effects` 确定性效果（只认结构化 path+值）/ `intent` 模型解释（不自动执行）；
5. **GM schema 裁剪**：只允许的类别进 schema，没有允许类别时不出现空段落；
6. **框架零世界词**：框架代码与默认声明里不含阵营/地名/具体玩法词。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trpg.agents.prompts.gm import gm_scene_user
from trpg.engine.events import (
    ActionSpec,
    EventScheduler,
    autonomy_for,
    category_allowed,
    parse_action_spec,
    parse_action_specs,
)
from trpg.models.state import GameState, Scene, WorldRef
from trpg.storage import db
from trpg.world.events import load_event_declarations, region_autonomy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "events.db"), check_same_thread=False)
    db.init_db(connection)
    yield connection
    connection.close()


def _specs() -> list[ActionSpec]:
    return parse_action_specs(
        [
            {
                "id": "evt_a",
                "category": "discovery",
                "intensity": 2,
                "cooldown_turns": 3,
                "text": "A",
                "intent": "给玩家一个线索",
            },
            {
                "id": "evt_b",
                "category": "social",
                "intensity": 1,
                "cooldown_turns": 0,
                "text": "B",
            },
        ]
    )


# ── 声明读取（世界包优先、规则层兜底）────────────────────────
def test_rules_layer_fallback_exists():
    """世界包没声明时，规则层有兜底声明（谁都能用的框架默认）。"""
    declarations = load_event_declarations(ROOT, "werewolf")
    assert declarations, "rules/events.yaml 必须有兜底事件声明"
    for item in declarations:
        assert item.get("id")
        assert item.get("category")


def test_declarations_are_neutral_words():
    """框架层声明不许含阵营/地名/玩法语义。"""
    banned = ["werewolf", "wolf", "village", "seer", "witch", "hunter", "狼", "村民", "预言", "铁谷"]
    for item in load_event_declarations(ROOT, "werewolf"):
        text = " ".join(str(v) for v in item.values()).lower()
        for word in banned:
            assert word.lower() not in text, f"默认声明含世界语义 {word}: {item}"


# ── 动作三态 ────────────────────────────────────────────────
def test_action_spec_has_three_states():
    spec = parse_action_spec(
        {
            "id": "evt_x",
            "category": "hazard",
            "text": "确定性文本",
            "effects": [{"path": "tension", "op": "add", "value": 1}],
            "intent": "模型解释",
        }
    )
    assert spec.text == "确定性文本"
    assert spec.effects == [{"path": "tension", "op": "add", "value": 1}]
    assert spec.intent == "模型解释"


def test_effects_must_be_structured():
    """确定性效果必须是结构化的（path + 值），自由文本一律忽略。"""
    spec = parse_action_spec({"id": "e", "effects": ["随便一句话", {"no_path": 1}, {"path": "tension"}]})
    assert spec.effects == [{"path": "tension"}], "只有带 path 的 dict 才算确定性效果"


def test_intent_is_not_executed_by_scheduler():
    """意图只是给模型参考，调度器不执行它（也不会把它写进效果）。"""
    spec = parse_action_spec({"id": "e", "intent": "让玩家去查", "effects": []})
    scheduler = EventScheduler([spec], seed="s", budget=1)
    chosen = scheduler.select(turn=1)
    assert chosen and chosen[0].effects == [], "意图不能变成确定性效果"


# ── 调度器：打分 / 冷却 / 预算 / 可复现 ───────────────────────
def test_scheduler_picks_by_intensity():
    scheduler = EventScheduler(_specs(), seed="s", budget=1)
    chosen = scheduler.select(turn=1)
    assert len(chosen) == 1
    assert chosen[0].id == "evt_a", "强度高的先选"


def test_budget_zero_is_quiet_turn():
    """预算 0 → 安静回合（不是出错后的兜底，是默认行为）。"""
    scheduler = EventScheduler(_specs(), seed="s", budget=0)
    assert scheduler.select(turn=1) == []


def test_same_seed_same_result():
    """同 seed 同结果（可预测、可复现、可进 CI）。"""
    a = EventScheduler(_specs(), seed="seed-1", budget=2).select(turn=3)
    b = EventScheduler(_specs(), seed="seed-1", budget=2).select(turn=3)
    assert [x.id for x in a] == [x.id for x in b]


def test_cooldown_blocks_repeat():
    """冷却期内同一事件不再出现。"""
    scheduler = EventScheduler(_specs(), seed="s", budget=1)
    cooldowns = {"evt_a": 2}  # evt_a 冷却 3 回合 → 第 3 回合仍在冷却
    chosen = scheduler.select(turn=3, cooldowns=cooldowns)
    assert chosen and chosen[0].id != "evt_a"
    later = scheduler.select(turn=6, cooldowns=cooldowns)
    assert later and later[0].id == "evt_a", "冷却结束后可以再次出现"


def test_requires_min_turn_and_region():
    specs = parse_action_specs(
        [
            {"id": "late", "category": "social", "requires": {"min_turn": 5}},
            {"id": "here", "category": "social", "requires": {"regions": ["r1"]}},
        ]
    )
    scheduler = EventScheduler(specs, seed="s", budget=5)
    ids = [x.id for x in scheduler.select(turn=1, region="r1")]
    assert "late" not in ids, "未到起始回合不触发"
    assert "here" in ids
    ids2 = [x.id for x in scheduler.select(turn=1, region="r2")]
    assert "here" not in ids2, "区域不匹配不触发"


# ── 区域自治 ────────────────────────────────────────────────
def test_autonomy_levels_and_lists():
    rule = autonomy_for({"level": "low", "allow": [], "forbid": ["hazard"]})
    assert rule["level"] == "low"
    assert category_allowed("discovery", rule) is True
    assert category_allowed("hazard", rule) is False


def test_autonomy_allow_list_limits_categories():
    rule = autonomy_for({"level": "mid", "allow": ["social"], "forbid": []})
    assert category_allowed("social", rule) is True
    assert category_allowed("hazard", rule) is False


def test_low_autonomy_gives_no_categories():
    """区域自治 low = GM 只能描述既有事实 → 不给任何事件类别。"""
    scheduler = EventScheduler(_specs(), seed="s", budget=1)
    cats = scheduler.allowed_categories(turn=1, autonomy={"level": "low"})
    assert cats == []


def test_allowed_categories_follow_candidates():
    scheduler = EventScheduler(_specs(), seed="s", budget=1)
    cats = scheduler.allowed_categories(turn=1, autonomy={"level": "mid"})
    assert cats == ["discovery", "social"]
    high = scheduler.allowed_categories(turn=1, autonomy={"level": "high"})
    assert high == ["discovery", "social"]


def test_region_autonomy_reads_from_pack_meta():
    class FakePack:
        region_meta = {"r1": {"label": "某处", "autonomy": {"level": "low", "forbid": []}}}

    assert region_autonomy(FakePack(), "r1")["level"] == "low"
    assert region_autonomy(FakePack(), "r2") == {}, "没有声明就没有自治规则"


# ── GM schema 裁剪 ──────────────────────────────────────────
def _state() -> GameState:
    return GameState(
        session_id="ses_x",
        world=WorldRef(world_id="w", era="e"),
        scene=Scene(location_id="loc_1", region="r1"),
    )


def test_gm_schema_contains_allowed_categories_only():
    text = gm_scene_user(
        _state(),
        {},
        1,
        allowed_event_categories=["discovery"],
    )
    assert "本回合允许引入的事件类型" in text
    assert "discovery" in text
    assert "hazard" not in text


def test_gm_schema_without_categories_says_no_new_event():
    text = gm_scene_user(_state(), {}, 1, allowed_event_categories=[])
    assert "不引入新事件" in text
    assert "本回合允许引入的事件类型" not in text, "没有允许类别时不该出现空标题"


# ── 事件落库与冷却 ──────────────────────────────────────────
def test_event_log_is_append_only_and_gives_cooldowns(conn):
    db.save_event_record(conn, "ses_x", 2, "evt_a", "discovery", {"note": "第一次"})
    db.save_event_record(conn, "ses_x", 5, "evt_a", "discovery", {"note": "第二次"})
    rows = db.load_event_records(conn, "ses_x")
    assert len(rows) == 2, "历史都要留着（append-only）"
    assert db.event_cooldowns(conn, "ses_x") == {"evt_a": 5}, "冷却起点 = 最后一次出现回合"


def test_events_are_session_scoped(conn):
    db.save_event_record(conn, "ses_a", 1, "evt_a")
    db.save_event_record(conn, "ses_b", 1, "evt_b")
    assert len(db.load_event_records(conn, "ses_a")) == 1
    assert len(db.load_event_records(conn, "ses_b")) == 1
