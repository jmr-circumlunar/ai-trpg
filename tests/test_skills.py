"""A2 声明式技能系统：四类效果原语 + 资源 / 互斥 / 禁用条件 + 参与者过滤。

设计口径（方案 33）：**框架只提供效果原语，世界包声明谁有什么技能**。
所以这里的测试用一套与具体世界无关的身份名（wolf / seer / medic / hunter）——
如果这套测试必须改成「预言家」「女巫」才能跑，说明框架又把世界藏进代码了。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.loop import GameLoop, TurnRecord  # noqa: E402
from trpg.models import (  # noqa: E402
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.models.enums import Position  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402
from trpg.rulespack.validate import validate_declarations  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402

BLANK = ROOT / "worlds" / "_blank"

PROFESSIONS = """
schema_version: 1
professions:
  - { id: wolf, camp: wolf }
  - { id: seer, camp: village }
  - { id: medic, camp: village }
  - { id: hunter, camp: village }
  - { id: plain, camp: village }
"""

SKILLS = """
schema_version: 1
skills:
  - { id: peek, profession: seer, trigger: turn, window: night, effect: reveal_camp, target: other_alive }

  - id: antidote
    profession: medic
    trigger: turn
    window: night
    effect: heal
    target: any_alive
    resource: { key: antidote, charges: 1 }
    exclusive_group: potions

  - id: venom
    profession: medic
    trigger: turn
    window: night
    effect: poison
    target: other_alive
    resource: { key: venom, charges: 1 }
    exclusive_group: potions

  - id: shot
    profession: hunter
    trigger: death
    effect: shoot
    target: other_alive
    forbidden_if: [poison]

  - id: burst
    profession: wolf
    trigger: turn
    window: day
    effect: self_eliminate
    target: self
    flag: burst
"""

FLOW = """
phases:
  - id: night
    kind: action
    visibility: group
    actors: groups
    groups:
      - { by: camp, value: wolf, name: pack, mode: consensus, targets: outside }
  - id: night_skill
    kind: skill
  - id: night_kill
    kind: apply
    target: consensus_result
    effect: eliminate
    last_words: true
  - id: day_skill
    kind: skill
    window: day
  - id: check_burst
    kind: check
    break_if: flag_set
    with: { flag: burst }
  - id: vote
    kind: collective
    rule: plurality
    tie: none
    eligible: alive
  - id: exile
    kind: apply
    target: collective_result
    effect: eliminate
    last_words: true
"""

ROSTER = [
    ("chr_1", "wolf", "wolf"),
    ("chr_2", "wolf", "wolf"),
    ("chr_3", "village", "seer"),
    ("chr_4", "village", "medic"),
    ("chr_5", "village", "hunter"),
    ("chr_6", "village", "plain"),
]


class PlanLLM(MockLLM):
    """按计划发动技能 / 投票；没计划就「不发、弃权」，避免测试互相污染。"""

    def __init__(self, seed, skill_plan=None, vote_plan=None) -> None:
        super().__init__(seed)
        self.skill_plan = skill_plan or {}
        self.vote_plan = vote_plan or {}

    def skill_choice(self, actor_id, card, state, turn, skills, candidates=None):
        return self.skill_plan.get((actor_id, turn))

    def vote(self, actor_id, card, state, turn, candidates=None):
        return self.vote_plan.get((actor_id, turn))


def build(tmp_path: Path, skill_plan=None, vote_plan=None, skills: str = SKILLS):
    base = tmp_path / "worlds" / "wtest"
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(BLANK, base)
    (base / "professions.yaml").write_text(PROFESSIONS, encoding="utf-8")
    (base / "skills.yaml").write_text(skills, encoding="utf-8")
    (base / "turn_flow.yaml").write_text(FLOW, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    cards, characters = {}, {}
    for cid, camp, profession in ROSTER:
        cards[cid] = make_card(cid=cid, profession=profession)
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id="ses_skill",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(),
        characters=characters,
    )
    llm = PlanLLM(42, skill_plan, vote_plan)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, pack=pack), llm


def stub_ctx(turn: int = 1, healed=None):
    rec = TurnRecord(turn=turn, scene="")
    return types.SimpleNamespace(
        turn=turn,
        rec=rec,
        consensus={},
        briefs=[],
        # 「谁被救了」是本回合私密状态，挂在 ctx 上**不进回执**（见 C21 / 方案 33）
        healed=list(healed or []),
    )


def skills_of(loop: GameLoop, cid: str, trigger: str = "turn"):
    return loop._available_skills(cid, loop.cards[cid], trigger)


# ── 资源与可用性 ────────────────────────────────────────────


def test_charges_are_initialized_from_world(tmp_path):
    loop, _ = build(tmp_path)

    assert loop.state.characters["chr_4"].skill_charges == {"antidote": 1, "venom": 1}
    # 没有资源的技能不占 charges
    assert loop.state.characters["chr_3"].skill_charges == {}


def test_skill_unavailable_after_resource_runs_out(tmp_path):
    loop, _ = build(tmp_path)
    ctx = stub_ctx()
    before = skills_of(loop, "chr_4")

    payload = asyncio.run(
        loop._apply_skill(ctx, "chr_4", loop.cards["chr_4"], before, {"skill": "antidote", "target": "chr_6"})
    )

    assert payload is not None
    assert loop.state.characters["chr_4"].skill_charges["antidote"] == 0
    assert "antidote" not in [item["id"] for item in skills_of(loop, "chr_4")]


# ── 四类效果原语 ────────────────────────────────────────────


def test_reveal_camp_writes_private_note(tmp_path):
    loop, _ = build(tmp_path)
    ctx = stub_ctx()

    payload = asyncio.run(
        loop._apply_skill(ctx, "chr_3", loop.cards["chr_3"], skills_of(loop, "chr_3"), {"skill": "peek", "target": "chr_1"})
    )

    assert payload is not None and payload["effect"] == "reveal_camp"
    notes = loop.state.characters["chr_3"].notes
    assert notes and "wolf" in notes[-1]
    # 只有本人拿到结果：别人身上没有笔记，公共 briefs 也没写
    assert loop.state.characters["chr_6"].notes == []
    assert ctx.briefs == []


def test_heal_marks_target_as_saved(tmp_path):
    loop, _ = build(tmp_path)
    ctx = stub_ctx()

    payload = asyncio.run(
        loop._apply_skill(ctx, "chr_4", loop.cards["chr_4"], skills_of(loop, "chr_4"), {"skill": "antidote", "target": "chr_6"})
    )

    assert payload is not None and payload["effect"] == "heal"
    assert ctx.healed == ["chr_6"]
    # 回执里不能留痕：知道「谁被救」就等于公开刀口与女巫行动
    assert "healed" not in ctx.rec.vote_result
    assert "saved" not in ctx.rec.vote_result
    # 私密技能的 SSE 也只该发给施术者本人
    assert payload["audience"] == ["chr_4"]


def test_poison_kills_without_last_words(tmp_path):
    loop, _ = build(tmp_path)
    ctx = stub_ctx()

    payload = asyncio.run(
        loop._apply_skill(ctx, "chr_4", loop.cards["chr_4"], skills_of(loop, "chr_4"), {"skill": "venom", "target": "chr_6"})
    )

    assert payload is not None and payload["effect"] == "poison"
    assert loop.state.characters["chr_6"].position == Position.dead
    dead = ctx.rec.deaths[-1]
    # 回执里多带一个 `cause_label`（世界包给的显示文案，可能为空）
    assert dead["id"] == "chr_6" and dead["cause"] == "poison", "被毒不留遗言"
    assert "last_words" not in dead, "被毒不留遗言"


def test_shoot_kills_and_leaves_last_words(tmp_path):
    loop, _ = build(tmp_path)
    ctx = stub_ctx()
    death_skills = loop._available_skills("chr_5", loop.cards["chr_5"], "death")

    payload = asyncio.run(
        loop._apply_skill(ctx, "chr_5", loop.cards["chr_5"], death_skills, {"skill": "shot", "target": "chr_1"})
    )

    assert payload is not None and payload["effect"] == "shoot"
    assert loop.state.characters["chr_1"].position == Position.dead
    dead = ctx.rec.deaths[-1]
    assert dead["cause"] == "shoot"
    assert dead.get("last_words"), "被带走的人按常规留遗言"


def test_forbidden_if_blocks_shot_after_poison(tmp_path):
    loop, _ = build(tmp_path, skill_plan={("chr_5", 1): {"skill": "shot", "target": "chr_1"}})
    ctx = stub_ctx()

    # 死因是 poison → 开枪技能被禁用，谁也不会再死
    asyncio.run(loop._run_death_skills(ctx, "chr_5", "poison"))
    assert ctx.rec.deaths == []

    # 正常出局 → 能开枪
    asyncio.run(loop._run_death_skills(ctx, "chr_5", "collective_result"))
    assert len(ctx.rec.deaths) == 1
    assert ctx.rec.deaths[0]["cause"] == "shoot"


# ── 参与者过滤（D6）─────────────────────────────────────────


def test_actors_groups_only_lets_the_group_act(tmp_path):
    loop, _ = build(tmp_path)

    wolves = loop._active_actors("groups", groups=[{"by": "camp", "value": "wolf"}])
    everyone = loop._active_actors()

    assert {cid for cid, _ in wolves} == {"chr_1", "chr_2"}
    assert len(everyone) == len(ROSTER)


def test_actors_skill_holders_only(tmp_path):
    loop, _ = build(tmp_path)

    night = loop._active_actors("skill_holders", "turn", window="night")
    day = loop._active_actors("skill_holders", "turn", window="day")

    # 窗口把「谁在什么时候能动手」说清楚：夜里是预言家 + 女巫，白天是狼（自爆）。
    # 猎人的技能是 death 触发，两个窗口都不进。
    assert {cid for cid, _ in night} == {"chr_3", "chr_4"}
    assert {cid for cid, _ in day} == {"chr_1", "chr_2"}


# ── 整回合：技能与淘汰的接线 ────────────────────────────────


def test_heal_prevents_night_kill(tmp_path):
    """狼选 chr_6，女巫救 chr_6 → 当夜无人出局，且回执**不透露**谁被救。"""
    loop, _ = build(
        tmp_path,
        skill_plan={("chr_4", 1): {"skill": "antidote", "target": "chr_6"}},
        vote_plan={("chr_1", 1): "chr_6", ("chr_2", 1): "chr_6"},
    )

    rec = asyncio.run(loop.run(1))[0]

    assert [item for item in rec.deaths if item.get("cause") == "consensus_result"] == []
    assert loop.state.characters["chr_6"].position == Position.scene
    # 「刀口被救」只留私密痕迹（ctx.healed），回执里不得出现任何能反推的字段
    assert "saved" not in rec.vote_result and "healed" not in rec.vote_result


def test_hunter_shoots_when_exiled(tmp_path):
    """猎人被投出局 → 当场开枪带走一人；开枪的受害者记 `cause=shoot`。"""
    loop, _ = build(
        tmp_path,
        skill_plan={("chr_5", 1): {"skill": "shot", "target": "chr_1"}},
        vote_plan={
            # 夜里狼选 chr_6（普通村民）落刀
            ("chr_1", 1): "chr_6",
            ("chr_2", 1): "chr_6",
            # 白天放逐猎人 chr_5（两票足够：其他人弃票）
            ("chr_3", 1): "chr_5",
            ("chr_4", 1): "chr_5",
            ("chr_5", 1): "chr_1",
        },
    )

    rec = asyncio.run(loop.run(1))[0]

    causes = [item.get("cause") for item in rec.deaths]
    assert "collective_result" in causes, "猎人应当被投出局"
    assert "shoot" in causes, "猎人出局应当开枪"
    shot = next(item for item in rec.deaths if item.get("cause") == "shoot")
    assert shot["id"] == "chr_1"


def test_medic_can_only_use_one_potion_per_turn(tmp_path):
    """互斥组：同一晚只能用一瓶（世界包声明，框架执行）。"""
    loop, _ = build(
        tmp_path,
        skill_plan={("chr_4", 1): {"skill": "venom", "target": "chr_6"}},
    )

    asyncio.run(loop.run(1))

    charges = loop.state.characters["chr_4"].skill_charges
    assert charges["venom"] == 0
    assert charges["antidote"] == 1, "同一晚用毒就不该再动解药"


# ── 声明校验 ────────────────────────────────────────────────


def test_declarations_accept_skill_file(tmp_path):
    build(tmp_path)

    assert validate_declarations(tmp_path / "worlds" / "wtest") == []


def test_unknown_effect_is_rejected(tmp_path):
    broken = SKILLS.replace("effect: reveal_camp", "effect: mind_control")
    build(tmp_path, skills=broken)

    errors = validate_declarations(tmp_path / "worlds" / "wtest")

    assert any("mind_control" in item for item in errors), errors


def test_skill_profession_must_exist(tmp_path):
    broken = SKILLS.replace("profession: seer", "profession: ghost")
    build(tmp_path, skills=broken)

    errors = validate_declarations(tmp_path / "worlds" / "wtest")

    assert any("ghost" in item for item in errors), errors



def test_self_destruct_skips_rest_of_day(tmp_path):
    """自爆（A5）：自己出局且不留遗言，当天的投票与放逐都不再发生。"""
    loop, _ = build(
        tmp_path, skill_plan={("chr_1", 1): {"skill": "burst", "target": "chr_1"}}
    )

    rec = asyncio.run(loop.run(1))[0]

    burst = [item for item in rec.deaths if item.get("cause") == "self_destruct"]
    assert burst and burst[0]["id"] == "chr_1"
    assert "last_words" not in burst[0], "自爆不留遗言"
    assert "vote" not in rec.order and "exile" not in rec.order, "自爆后不该再投票/放逐"
    assert any(item.get("id") == "vote" for item in rec.skipped), rec.skipped
    # 回合标记只对本回合有效，不该留到下一回合
    assert "burst" not in loop.state.party.flags
