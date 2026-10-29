"""势力态度影响交涉 / 威吓 DC（B2）：只读合成，不新增状态字段。"""

from pathlib import Path

from trpg.agents import MockLLM
from trpg.engine.loop import GameLoop
from trpg.models import CharacterRuntime, GameState, Party, PlayerAction, Scene, WorldRef
from trpg.models.enums import Intent
from trpg.rules import DiceEngine, Rules
from trpg.rules.judge import judge
from trpg.world import load_world
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]
RULES = Rules(ROOT)
PACK = load_world("ironvale", ROOT)


def _state() -> GameState:
    card = make_card()
    return GameState(
        session_id="ses_attitude",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_a": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )


# ── 世界包能力查询 ────────────────────────────────────────


def test_pack_exposes_faction_queries():
    assert PACK.faction_of_npc("npc_bellringer") == "faction_church"
    assert PACK.faction_attitude("faction_church") == -1
    assert PACK.faction_label("faction_church") == "灰钟教会"
    assert PACK.faction_attitude("faction_不存在") == 0


def test_social_faction_falls_back_to_region_authority():
    assert PACK.social_faction("npc_bellringer", "north_port") == "faction_church"
    # 没盯具体 NPC → 用本区 authority（北港是夜巡队）
    assert PACK.social_faction("", "north_port") == "faction_watch"
    assert PACK.social_faction("", "不存在的区域") == ""


# ── 档位方向 ──────────────────────────────────────────────


def test_attitude_mod_direction_and_inversion():
    assert RULES.attitude_mod("交涉", -3) > 0, "敌对时交涉更难"
    assert RULES.attitude_mod("交涉", 3) < 0, "友好时交涉更容易"
    assert RULES.attitude_mod("威吓", -3) < 0, "敌对时威吓更容易"
    assert RULES.attitude_mod("威吓", 3) > 0, "友好时威吓更难"


def test_non_social_skill_is_untouched():
    assert RULES.attitude_mod("潜行", -3) == 0
    assert RULES.attitude_mod("撬锁", 3) == 0
    assert RULES.attitude_mod(None, -3) == 0


def test_attitude_mod_is_clamped_outside_bands():
    assert RULES.attitude_mod("交涉", -99) == RULES.attitude_mod("交涉", -3)
    assert RULES.attitude_mod("交涉", 99) == RULES.attitude_mod("交涉", 3)


# ── judge 实际生效 ────────────────────────────────────────


def _judge(skill: str, score: int, target: str = ""):
    card = make_card()
    action = PlayerAction(
        actor_id="chr_a", action=f"用{skill}试探", skill=skill, intent=Intent.normal, target=target
    )
    return judge(
        action, card, _state(), RULES, DiceEngine("same-seed"), attitude_score=score
    )


def test_judge_applies_attitude_only_to_social_skills():
    calm, hostile = _judge("交涉", 0), _judge("交涉", -3)
    assert hostile.rolls == calm.rolls
    assert hostile.dc - calm.dc == RULES.attitude_mod("交涉", -3)

    sneak_a, sneak_b = _judge("潜行", 0), _judge("潜行", -3)
    assert sneak_a.dc == sneak_b.dc, "非社交技能不受势力态度影响"


def test_judge_inverts_intimidation():
    friendly, hostile = _judge("威吓", 3), _judge("威吓", -3)
    assert hostile.dc < friendly.dc, "对敌对势力威吓应该更容易"


# ── 编排层合成 ────────────────────────────────────────────


def _loop(pack=PACK, reputation: dict | None = None) -> GameLoop:
    state = _state()
    state.party.reputation = dict(reputation or {})
    return GameLoop(Rules(ROOT), {"chr_a": make_card()}, state, MockLLM(1), seed=1, pack=pack)


def _action(target: str = "") -> PlayerAction:
    return PlayerAction(
        actor_id="chr_a", action="跟他攀谈", skill="交涉", intent=Intent.normal, target=target
    )


def test_attitude_score_uses_target_npc_faction():
    loop = _loop()
    assert loop._attitude_score(_action("npc_bellringer")) == PACK.faction_attitude("faction_church")


def test_reputation_lifts_hostile_faction_toward_neutral():
    baseline = _loop()._attitude_score(_action("npc_bellringer"))
    trusted = _loop(reputation={"灰钟教会": 5})._attitude_score(_action("npc_bellringer"))

    assert trusted == baseline + 1, "声望每 step 点折算一档态度"


def test_no_pack_means_zero_score():
    loop = _loop(pack=None)
    assert loop._attitude_score(_action("npc_bellringer")) == 0
