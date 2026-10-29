"""战斗（简单版）：GM 只申报威胁，数值与结果全由代码算。

覆盖：威胁档折算、伤害公式、暴击、防御减伤、逃跑、敌人反击、敌人死亡后移出 state。
"""

from __future__ import annotations

from pathlib import Path

from trpg.combat import engine as combat
from trpg.engine.loop import GameLoop, TurnRecord
from trpg.agents import MockLLM
from trpg.models import (
    CharacterCard,
    CombatMode,
    Enemy,
    EquipmentInstance,
    JudgeResult,
    PlayerAction,
)
from trpg.rules.dice import DiceEngine
from trpg.rules.loader import Rules
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]


def _eqcfg() -> dict:
    """战斗数值：`rules/combat.yaml`（S1-1 之后战斗数值只认这一份）。"""
    return Rules(ROOT).combat


def _result(outcome: str = "success") -> JudgeResult:
    return JudgeResult(
        actor_id="chr_test01",
        skill="器械",
        action_type="contest",
        attr="str",
        attr_mod=0,
        skill_bonus=0,
        dc=10,
        expr="1d20",
        rolls=[15],
        total=15,
        margin=5,
        outcome=outcome,
    )


def _enemy(hp: int = 5, atk: int = 0, defense: int = 0) -> Enemy:
    return Enemy(id="en_1", name="守门的东西", hp=hp, hp_max=hp, atk=atk, **{"def": defense})


def _armed(card: CharacterCard, atk: int = 5, defense: int = 0) -> CharacterCard:
    attrs = {"atk": atk} if atk else {}
    if defense:
        attrs["def"] = defense
    card.ext.equipment.append(
        EquipmentInstance(id="eq_w1", template_id="tpl_w", tier=1, attrs=attrs, tags=["blade"])
    )
    return card


# ── 申报折算 ────────────────────────────────────────────────
def test_enemy_from_threat_uses_table():
    ruleset = _eqcfg()
    weak = combat.enemy_from_declaration({"id": "e1", "threat": "weak"}, ruleset, stage=1)
    strong = combat.enemy_from_declaration({"id": "e2", "threat": "strong"}, ruleset, stage=1)
    assert weak.hp == ruleset["enemy"]["threat"]["weak"]["hp"]
    assert strong.hp == ruleset["enemy"]["threat"]["strong"]["hp"]
    assert strong.hp > weak.hp


def test_enemy_stage_bonus():
    ruleset = _eqcfg()
    early = combat.enemy_from_declaration({"id": "e1", "threat": "normal"}, ruleset, stage=1)
    late = combat.enemy_from_declaration({"id": "e1", "threat": "normal"}, ruleset, stage=4)
    assert late.hp > early.hp
    assert late.defense >= early.defense


def test_enemy_explicit_values_clamped():
    enemy = combat.enemy_from_declaration(
        {"id": "e1", "threat": "weak", "hp": 999, "atk": 999, "def": 999}, _eqcfg(), stage=1
    )
    assert enemy.hp <= 30 and enemy.atk <= 30 and enemy.defense <= 30


def test_enemy_needs_id():
    assert combat.enemy_from_declaration({"name": "无 id"}, _eqcfg(), 1) is None


# ── 伤害 ────────────────────────────────────────────────────
def test_attack_damage_formula():
    card = _armed(make_card(), atk=5)
    enemy = _enemy(hp=10, defense=1)
    row = combat.resolve_attack(_result("success"), card, enemy, DiceEngine(1))
    assert row["hit"] is True
    assert row["damage"] == max(1, 5 + row["roll"] - 1)


def test_attack_miss_no_damage():
    card = _armed(make_card(), atk=5)
    row = combat.resolve_attack(_result("failure"), card, _enemy(), DiceEngine(1))
    assert row["hit"] is False
    assert row["damage"] == 0


def test_crit_doubles_damage():
    card = _armed(make_card(), atk=4)
    enemy = _enemy(hp=30, defense=0)
    normal = combat.resolve_attack(_result("success"), card, enemy, DiceEngine(3))
    crit = combat.resolve_attack(_result("crit_success"), card, enemy, DiceEngine(3))
    assert crit["damage"] == normal["damage"] * 2


def test_damage_floor_is_one():
    card = make_card()  # 空手
    row = combat.resolve_attack(_result("success"), card, _enemy(defense=9), DiceEngine(1))
    assert row["damage"] == 1


def test_enemy_strike_and_defend_reduction():
    card = _armed(make_card(), atk=0, defense=1)
    enemy = _enemy(atk=4)
    plain = combat.enemy_strike(enemy, card, DiceEngine(5))
    guarded = combat.enemy_strike(enemy, card, DiceEngine(5), defending=True)
    assert guarded["damage"] == max(1, plain["damage"] - combat.defend_reduction())


def test_flee_success():
    card = make_card()
    row = combat.resolve_flee(_result("success"), card, _enemy())
    assert row["escaped"] is True


# ── 回合集成 ────────────────────────────────────────────────
def _loop_with_enemy(hp: int = 3, atk: int = 0):
    card = _armed(make_card(), atk=6)
    state = make_state(card)
    state.enemies = {"en_1": _enemy(hp=hp, atk=atk)}
    loop = GameLoop(Rules(ROOT), {"chr_test01": card}, state, MockLLM(42), seed=42)
    return loop, card, state


def test_combat_kills_enemy_and_removes_it():
    loop, card, state = _loop_with_enemy(hp=1)
    action = PlayerAction(
        actor_id="chr_test01", action="砍", skill="器械", target="en_1", mode=CombatMode.attack
    )
    rec = TurnRecord(turn=1, scene="")
    loop._resolve_combat(rec, 1, [("chr_test01", card)], [action], [_result("success")])

    assert "en_1" not in loop.state.enemies, "hp 归 0 的敌人必须移出 state"
    assert any(row["kind"] == "attack" and row["killed"] for row in rec.combat)
    assert not any(row["kind"] == "enemy_strike" for row in rec.combat), "死掉的敌人不反击"


def test_enemy_strikes_back_when_alive():
    loop, card, state = _loop_with_enemy(hp=20, atk=3)
    action = PlayerAction(
        actor_id="chr_test01", action="砍", skill="器械", target="en_1", mode=CombatMode.attack
    )
    rec = TurnRecord(turn=1, scene="")
    hp_before = loop.state.characters["chr_test01"].hp
    loop._resolve_combat(rec, 1, [("chr_test01", card)], [action], [_result("success")])

    strikes = [r for r in rec.combat if r["kind"] == "enemy_strike"]
    assert strikes, "敌人活着就要反击"
    assert loop.state.characters["chr_test01"].hp < hp_before


def test_flee_avoids_counterattack():
    loop, card, state = _loop_with_enemy(hp=20, atk=5)
    action = PlayerAction(
        actor_id="chr_test01", action="转身就跑", skill="潜行", target="en_1", mode=CombatMode.flee
    )
    rec = TurnRecord(turn=1, scene="")
    hp_before = loop.state.characters["chr_test01"].hp
    loop._resolve_combat(rec, 1, [("chr_test01", card)], [action], [_result("success")])

    assert not [r for r in rec.combat if r["kind"] == "enemy_strike"]
    assert loop.state.characters["chr_test01"].hp == hp_before


def test_spawn_from_gm_declaration():
    card = make_card()
    state = make_state(card)
    loop = GameLoop(Rules(ROOT), {"chr_test01": card}, state, MockLLM(42), seed=42)
    rec = TurnRecord(turn=1, scene="")
    loop._apply_enemies([{"id": "en_9", "name": "东西", "threat": "strong"}], rec, 1)
    assert "en_9" in loop.state.enemies
    assert loop.state.enemies["en_9"].name == "东西"
    assert any(row["kind"] == "spawn" for row in rec.combat)


def test_respawn_does_not_reset_hp():
    card = make_card()
    state = make_state(card)
    state.enemies = {"en_1": _enemy(hp=2, atk=1)}
    loop = GameLoop(Rules(ROOT), {"chr_test01": card}, state, MockLLM(42), seed=42)
    rec = TurnRecord(turn=1, scene="")
    loop._apply_enemies([{"id": "en_1", "name": "改了名字", "threat": "boss"}], rec, 1)
    assert loop.state.enemies["en_1"].name == "改了名字"
    assert loop.state.enemies["en_1"].hp == 2, "重复申报不能回血"
