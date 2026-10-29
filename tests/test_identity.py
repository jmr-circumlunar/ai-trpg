"""职业三层（origin / talents / path）的行为测试。

覆盖契约点 C11–C13、C17：
- 出身按权重抽，1000 次分布落在声明权重附近
- 天赋数量来自 `talent_count`，同一次抽取不重复
- 建卡：属性 = 10 + stats_mod、人格与目标来自世界包、初始装备来自 `starter`
- 身份变更：合法接受、非法拒绝、门槛未达拒绝、只能 set
- 身份授予（grants）生效且重复同步不会二次加成
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import pytest

from trpg.growth.build import new_card_by_origin, preset_cards
from trpg.engine.loop import GameLoop, TurnRecord
from trpg.engine.state_merge import DeltaValidationError, apply_delta
from trpg.game import build_cards, build_state
from trpg.agents import MockLLM
from trpg.models import CharacterRuntime, DeltaChange, StateDelta
from trpg.models.enums import DeltaOp
from trpg.growth import identity
from trpg.rules.loader import Rules
from trpg.world import WorldPackError, load_world

ROOT = Path(__file__).resolve().parents[1]

WORLD_YAML = """
world_id: testland
name: 测试地
default_era: modern
default_region: hub
eras:
  modern: {label: 现代, tier_max: 3}
regions:
  hub: {label: 中枢, entry: loc_hub}
"""

REGION_MD = """
# 中枢

| id | 名称 | 钩子 |
|---|---|---|
| loc_hub | 中枢广场 | 一根没有影子旗杆 |
"""

ORIGINS_YAML = """
random: true
talent_count: {min: 1, max: 2}
origins:
  - id: low
    label: 低
    weight: 600
    description: 低出身
    personality: 沉默
    private_goals: [一, 二, 三]
    talents: [耐打]
    stats_mod: {str: 1}
    cost: 无
  - id: mid
    label: 中
    weight: 300
    description: 中出身
    personality: 平静
    private_goals: [四, 五]
    talents: [基础]
    stats_mod: {dex: 2}
    cost: 慢
  - id: high
    label: 高
    weight: 100
    description: 高出身
    personality: 冷淡
    private_goals: [六]
    talents: [高级]
    stats_mod: {str: 3, int: 2}
    cost: 快
talent_pool:
  - {id: keen, label: 敏锐, effect: 观察 +1, skills: {观察: 1}}
  - {id: will, label: 硬骨, effect: 意志 +1, skills: {意志: 1}}
  - {id: plain, label: 普通, effect: 无数值}
"""

PATHS_YAML = """
paths:
  - id: freshman
    label: 新生
    default: true
    grants: {}
    granted_by: [招生办]
    requirements: {}
  - id: student
    label: 在校生
    grants: {skills: {学识: 1}, flags: [student_id]}
    granted_by: [教务处]
    requirements: {stage_min: 2, requires_path: [freshman]}
  - id: elite
    label: 精英
    grants: {skills: {威吓: 2}, tags: [精英]}
    granted_by: [执行部]
    requirements: {stage_min: 3, stats_min: {int: 14}, min_grade: mid, requires_path: [student]}
"""

ITEMS_YAML = """
items:
  - {id: tpl_card, name: 学生证, slot: tool, tier: 1, req_level: 1, attrs: {}, tags: [document]}
  - {id: tpl_knife, name: 短刀, slot: weapon, tier: 1, req_level: 1, attrs: {atk: [2, 3]}, tags: [blade]}
starter:
  default: [tpl_card]
  by_origin:
    mid: [tpl_knife]
    high: [tpl_knife]
"""


def _world_root(tmp: Path) -> Path:
    base = tmp / "worlds" / "testland"
    (base / "regions").mkdir(parents=True, exist_ok=True)
    (base / "world.yaml").write_text(WORLD_YAML, encoding="utf-8")
    (base / "regions" / "hub.md").write_text(REGION_MD, encoding="utf-8")
    (base / "origins.yaml").write_text(ORIGINS_YAML, encoding="utf-8")
    (base / "paths.yaml").write_text(PATHS_YAML, encoding="utf-8")
    (base / "items.yaml").write_text(ITEMS_YAML, encoding="utf-8")
    (base / "seeds.yaml").write_text("schema_version: 1\nseeds: []\n", encoding="utf-8")
    (base / "npcs.yaml").write_text("schema_version: 1\nnpcs: []\n", encoding="utf-8")
    return tmp


@pytest.fixture
def pack(tmp_path):
    return load_world("testland", _world_root(tmp_path))


# ── 抽取 ────────────────────────────────────────────────────
def test_origin_weight_distribution(pack):
    rng = random.Random(20261021)
    counts = Counter(identity.roll_origin(pack, rng)["id"] for _ in range(1000))
    total = sum(counts.values())
    for oid, expect in {"low": 0.60, "mid": 0.30, "high": 0.10}.items():
        share = counts[oid] / total
        assert abs(share - expect) < 0.05, f"{oid} 期望 {expect}，实际 {share:.3f}"


def test_roll_origin_is_seeded(pack):
    first = [identity.roll_origin(pack, random.Random(7))["id"] for _ in range(20)]
    again = [identity.roll_origin(pack, random.Random(7))["id"] for _ in range(20)]
    assert first == again


def test_talents_count_and_unique(pack):
    rng = random.Random(11)
    for _ in range(60):
        got = identity.roll_talents(pack, rng)
        assert 1 <= len(got) <= 2
        ids = [t["id"] for t in got]
        assert len(ids) == len(set(ids))


def test_talent_skill_bonus_aggregates(pack):
    bonus = identity.talent_skill_bonus([{"skills": {"观察": 1}}, {"skills": {"观察": 1, "意志": 2}}])
    assert bonus == {"观察": 2, "意志": 2}


# ── 建卡 ────────────────────────────────────────────────────
def test_new_card_by_origin_fields(pack):
    card = new_card_by_origin(name="甲", pack=pack, origin_id="mid", rng=random.Random(1))
    assert card.ext.origin == "mid"
    assert card.ext.profession == "中"  # 兼容字段 = 出身名
    assert card.ext.path == "freshman"
    assert card.ext.stats["dex"] == 12  # 10 + 2
    assert "基础" in card.ext.talents  # 出身自带天赋
    assert card.data.personality == "平静"
    assert card.ext.private_goal in {"四", "五"}
    tids = [e.template_id for e in card.ext.equipment]
    assert tids == ["tpl_card", "tpl_knife"]  # default + by_origin
    assert all(e.req_level == 1 for e in card.ext.equipment)
    assert card.ext.equipment[1].attrs["atk"] == 2  # (2+3)//2 中位


def test_unknown_origin_rejected(pack):
    with pytest.raises(WorldPackError):
        new_card_by_origin(name="乙", pack=pack, origin_id="nope")


def test_preset_cards_same_seed(pack):
    a = preset_cards(3, pack=pack, seed="42")
    b = preset_cards(3, pack=pack, seed="42")
    assert [c.ext.origin for c in a.values()] == [c.ext.origin for c in b.values()]
    assert [c.ext.talents for c in a.values()] == [c.ext.talents for c in b.values()]
    assert [c.ext.private_goal for c in a.values()] == [c.ext.private_goal for c in b.values()]


def test_build_state_uses_entry_and_start_clock(pack):
    cards = build_cards(1, pack, seed="1")
    state = build_state(pack, cards)
    assert state.scene.location_id == "loc_hub"  # 不是 loc_hub 的 f"loc_{region}" 猜测
    assert state.scene.region == "hub"
    cid = next(iter(cards))
    assert state.characters[cid].path == "freshman"


def test_ironvale_falls_back_to_professions():
    pack = load_world("ironvale", ROOT)
    cards = build_cards(2, pack, seed=1)
    assert all(c.ext.origin == "" for c in cards.values())
    assert all(c.ext.path == "" for c in cards.values())
    assert all(c.ext.profession for c in cards.values())


# ── 身份变更 ────────────────────────────────────────────────
def _ready(pack):
    cards = build_cards(1, pack, seed="7")
    cid = next(iter(cards))
    state = build_state(pack, cards)
    return cards, cid, state


def _path_delta(cid: str, path: str, op: DeltaOp = DeltaOp.set) -> StateDelta:
    return StateDelta(
        turn=1,
        changes=[DeltaChange(path=f"characters.{cid}.path", op=op, value=path)],
    )


def test_path_change_accepted(pack):
    cards, cid, state = _ready(pack)
    state.stage = 2
    new = apply_delta(state, _path_delta(cid, "student"), cards, None, pack)
    assert new.characters[cid].path == "student"
    assert new.version == state.version + 1


def test_illegal_path_rejected(pack):
    cards, cid, state = _ready(pack)
    state.stage = 3
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _path_delta(cid, "dragon_king"), cards, None, pack)


def test_path_stage_gate(pack):
    cards, cid, state = _ready(pack)  # stage = 1
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _path_delta(cid, "student"), cards, None, pack)


def test_path_requires_previous_path(pack):
    cards, cid, state = _ready(pack)
    state.stage = 3  # 幕次够，但 elite 要求先 student
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _path_delta(cid, "elite"), cards, None, pack)


def test_path_stats_gate(pack):
    cards, cid, state = _ready(pack)
    state.stage = 3
    state.characters[cid].path = "student"
    # 把 int 压到 13，低于 elite 的 14
    cards[cid].ext.stats["int"] = 13
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _path_delta(cid, "elite"), cards, None, pack)


def test_path_only_set(pack):
    cards, cid, state = _ready(pack)
    state.stage = 2
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _path_delta(cid, "student", DeltaOp.add), cards, None, pack)


def test_empty_path_rejected(pack):
    cards, cid, state = _ready(pack)
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _path_delta(cid, ""), cards, None, pack)


def test_without_pack_path_is_lenient():
    """没有 paths.yaml 的世界不设闸门，但空值仍然拒绝。"""
    from support.builders import make_card, make_state

    card = make_card()
    state = make_state(card)
    cards = {"chr_test01": card}
    delta = StateDelta(
        turn=1,
        changes=[DeltaChange(path="characters.chr_test01.path", op=DeltaOp.set, value="whatever")],
    )
    new = apply_delta(state, delta, cards)
    assert new.characters["chr_test01"].path == "whatever"


# ── 授予与同步 ──────────────────────────────────────────────
def test_apply_grants(pack):
    card = new_card_by_origin(name="丙", pack=pack, origin_id="low", rng=random.Random(2))
    rt = CharacterRuntime(hp=11, sanity=10, path="elite")
    applied = identity.apply_grants(pack, card, rt, "elite")
    assert applied["skills"] == {"威吓": 2}
    assert card.ext.skills["威吓"] == 2
    assert "精英" in card.data.tags
    assert "student_id" not in rt.flags  # elite 没有这条 flag


def test_loop_syncs_path_once(pack):
    cards, cid, state = _ready(pack)
    loop = GameLoop(Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack)
    loop.state.characters[cid].path = "student"

    rec = TurnRecord(turn=1, scene="")
    loop._sync_paths(rec)
    assert cards[cid].ext.path == "student"
    assert cards[cid].ext.skills.get("学识") == 1
    assert "student_id" in loop.state.characters[cid].flags
    assert rec.path_changes and rec.path_changes[0]["path"] == "student"

    loop._sync_paths(rec)  # 再同步一次不应二次加成
    assert cards[cid].ext.skills.get("学识") == 1
