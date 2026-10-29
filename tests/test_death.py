"""濒死与死亡：hp 归 0 → DYING（宽限可救）→ DEAD（不再入场）。

宽限回合数在 `rules/ruleset.yaml: death_grace_turns`，不在代码里写死。
"""

from __future__ import annotations

from pathlib import Path

from trpg.engine.loop import GameLoop, TurnRecord
from trpg.agents import MockLLM
from trpg.models.enums import Position
from trpg.models import EquipmentInstance
from trpg.rules.loader import Rules
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]


def _loop():
    card = make_card()
    state = make_state(card)
    loop = GameLoop(Rules(ROOT), {"chr_test01": card}, state, MockLLM(42), seed=42)
    return loop, card


def test_grace_comes_from_rules():
    assert Rules(ROOT).death_grace_turns >= 1


def test_zero_hp_marks_dying_not_dead():
    loop, _ = _loop()
    rt = loop.state.characters["chr_test01"]
    rt.hp = 0
    rec = TurnRecord(turn=1, scene="")
    loop._resolve_death("chr_test01", 1, rec)

    assert "dying" in rt.conditions
    assert rt.dying_since == 1
    assert rt.position == Position.scene, "濒死的人还在场上，只是不能行动"
    assert rec.deaths == []


def test_dying_becomes_dead_after_grace():
    loop, _ = _loop()
    rt = loop.state.characters["chr_test01"]
    grace = Rules(ROOT).death_grace_turns
    rt.hp = 0
    rec = TurnRecord(turn=1, scene="")

    loop._resolve_death("chr_test01", 1, rec)
    loop._resolve_death("chr_test01", 1 + grace - 1, rec)
    assert rt.position == Position.scene, "宽限期内不能判死"

    loop._resolve_death("chr_test01", 1 + grace, rec)
    assert rt.position == Position.dead
    assert "dying" not in rt.conditions
    assert rec.deaths and rec.deaths[0]["char_id"] == "chr_test01"


def test_rescue_clears_dying():
    loop, _ = _loop()
    rt = loop.state.characters["chr_test01"]
    rt.hp = 0
    rec = TurnRecord(turn=1, scene="")
    loop._resolve_death("chr_test01", 1, rec)

    rt.hp = 4  # 被救回来
    loop._resolve_death("chr_test01", 2, rec)
    assert "dying" not in rt.conditions
    assert rt.dying_since == 0
    assert rt.position == Position.scene


def test_dead_never_acts_again():
    loop, card = _loop()
    rt = loop.state.characters["chr_test01"]
    rt.hp = 0
    rec = TurnRecord(turn=1, scene="")
    grace = Rules(ROOT).death_grace_turns
    loop._resolve_death("chr_test01", 1, rec)
    loop._resolve_death("chr_test01", 1 + grace, rec)

    assert loop._active_actors() == [], "死人不入场"
    rt.hp = 5  # 一期不做复活：位置已经是 dead
    assert loop._active_actors() == []


def test_dying_character_cannot_act():
    loop, _ = _loop()
    rt = loop.state.characters["chr_test01"]
    rt.hp = 0
    rec = TurnRecord(turn=1, scene="")
    loop._resolve_death("chr_test01", 1, rec)
    assert loop._active_actors() == [], "濒死的人不能行动"


def test_combat_damage_can_drive_into_dying():
    """战斗扣血到 0 后，同一回合的死亡结算要把它标成 dying。"""
    card = make_card()
    card.ext.equipment.append(
        EquipmentInstance(id="eq_w1", template_id="tpl_w", tier=1, attrs={"atk": 0}, tags=["blade"])
    )
    state = make_state(card)
    state.characters["chr_test01"].hp = 1

    from trpg.models import Enemy

    state.enemies = {"en_1": Enemy(id="en_1", hp=99, hp_max=99, atk=9, **{"def": 0})}
    loop = GameLoop(Rules(ROOT), {"chr_test01": card}, state, MockLLM(42), seed=42)

    from trpg.models import CombatMode, JudgeResult, PlayerAction

    action = PlayerAction(
        actor_id="chr_test01", action="砍", skill="器械", target="en_1", mode=CombatMode.attack
    )
    result = JudgeResult(
        actor_id="chr_test01",
        skill="器械",
        action_type="contest",
        attr="str",
        attr_mod=0,
        skill_bonus=0,
        dc=10,
        expr="1d20",
        rolls=[1],
        total=1,
        margin=-9,
        outcome="failure",
    )
    rec = TurnRecord(turn=1, scene="")
    loop._resolve_combat(rec, 1, [("chr_test01", card)], [action], [result])
    loop._resolve_death("chr_test01", 1, rec)

    rt = loop.state.characters["chr_test01"]
    assert rt.hp == 0
    assert "dying" in rt.conditions
