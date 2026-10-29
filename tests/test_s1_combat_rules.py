"""S1-1：战斗数值进 `rules/combat.yaml` 的回归入口。

四层钉：

1. **数值出代码**：`trpg/combat/engine.py` 里不再出现战斗数值字面量（伤害骰、下限、
   暴击倍率、防御减免、夹取区间、威胁档表），也不再保留 `HIT_OUTCOMES` /
   `DEFEND_REDUCTION` 这类模块常量。
2. **逐值一致**：yaml 里的默认值必须等于搬家前的硬编码（显式写成对照表，
   数值被谁改过一眼能看出来）。
3. **行为一致**：固定输入下，伤害 / 暴击 / 防御 / 威胁折算的结果与搬家前完全相同。
4. **单一真相源**：`rules/ruleset.yaml` 不再私藏一份 `enemy_threat`。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from trpg.combat import engine as combat
from trpg.models import Enemy, EquipmentInstance, JudgeResult
from trpg.rules.dice import DiceEngine
from trpg.rules.loader import Rules
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]
COMBAT_YAML = ROOT / "rules" / "combat.yaml"
ENGINE = ROOT / "trpg" / "combat" / "engine.py"


def _cfg() -> dict:
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


def _card(atk: int = 5, defense: int = 0):
    card = make_card()
    attrs = {"atk": atk} if atk else {}
    if defense:
        attrs["def"] = defense
    if attrs:
        card.ext.equipment.append(
            EquipmentInstance(id="eq_w1", template_id="tpl_w", tier=1, attrs=attrs, tags=["blade"])
        )
    return card


def _enemy(hp: int = 10, atk: int = 0, defense: int = 0) -> Enemy:
    return Enemy(id="en_1", name="守门的东西", hp=hp, hp_max=hp, atk=atk, **{"def": defense})


# ── 1. 数值确实搬走了 ───────────────────────────────────────

def test_combat_yaml_is_the_only_source():
    data = yaml.safe_load(COMBAT_YAML.read_text(encoding="utf-8"))
    assert set(data) >= {"hit_outcomes", "attack", "defend", "enemy"}
    assert set(data["attack"]) >= {"dice", "min_damage", "crit_multiplier"}
    assert set(data["defend"]) >= {"reduction"}
    assert set(data["enemy"]) >= {"threat", "threat_stage_bonus", "clamp", "floors"}


def test_engine_has_no_hardcoded_combat_numbers():
    """引擎只读不算：战斗数值不许留在代码里。"""
    code = "\n".join(
        line
        for line in ENGINE.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("*") and not line.strip().startswith("//")
    )
    for bad in (
        "HIT_OUTCOMES",       # 命中集合常量化
        "DEFEND_REDUCTION",   # 防御减免常量化
        "d(6)",               # 伤害骰写死
        "max(1, raw",         # 伤害下限写死
        "min(30",             # 夹取上限写死
        "hp: 4",              # 威胁档表写死
        "enemy_threat",       # 旧表名残留
    ):
        assert bad not in code, f"战斗数值不该留在代码里：{bad}"


def test_ruleset_no_longer_hides_enemy_threat():
    """单一真相源：威胁档表只能在 `rules/combat.yaml` 里。"""
    ruleset = yaml.safe_load((ROOT / "rules" / "ruleset.yaml").read_text(encoding="utf-8"))
    assert "enemy_threat" not in ruleset
    assert "threat_stage_bonus" not in ruleset


# ── 2. 默认值逐值一致（搬家前的硬编码对照表）─────────────────

def test_default_values_match_pre_move_hardcoding():
    cfg = _cfg()
    assert list(cfg["hit_outcomes"]) == ["crit_success", "success", "fail_forward"]
    assert cfg["attack"]["dice"] == 6
    assert cfg["attack"]["min_damage"] == 1
    assert cfg["attack"]["crit_multiplier"] == 2
    assert cfg["defend"]["reduction"] == 2
    assert cfg["defend"]["duration_turns"] == 1
    assert cfg["enemy"]["clamp"] == {"min": 1, "max": 30}
    assert cfg["enemy"]["atk_bonus_divisor"] == 2
    assert cfg["enemy"]["def_bonus_from_stage"] == 3
    assert cfg["enemy"]["def_bonus_amount"] == 1
    assert cfg["enemy"]["tag_limit"] == 3
    # 威胁档表：数值与搬家前 `rules/ruleset.yaml` 里那张表逐值相同
    assert cfg["enemy"]["threat"] == {
        "weak": {"hp": 2, "atk": 1, "def": 0},
        "normal": {"hp": 4, "atk": 2, "def": 1},
        "strong": {"hp": 7, "atk": 3, "def": 2},
        "boss": {"hp": 12, "atk": 4, "def": 3},
    }
    assert cfg["enemy"]["threat_stage_bonus"] == {1: 0, 2: 1, 3: 2, 4: 3}


# ── 3. 行为一致（固定输入 → 固定结果）────────────────────────

def test_attack_damage_behaviour_unchanged():
    card = _card(atk=5)
    row = combat.resolve_attack(_result("success"), card, _enemy(defense=1), DiceEngine(1))
    assert row["hit"] is True
    assert row["damage"] == max(1, 5 + row["roll"] - 1)


def test_crit_damage_behaviour_unchanged():
    card = _card(atk=4)
    enemy = _enemy(hp=30, defense=0)
    normal = combat.resolve_attack(_result("success"), card, enemy, DiceEngine(3))
    crit = combat.resolve_attack(_result("crit_success"), card, enemy, DiceEngine(3))
    assert crit["damage"] == normal["damage"] * 2


def test_damage_floor_behaviour_unchanged():
    row = combat.resolve_attack(
        _result("success"), _card(atk=0), _enemy(defense=9), DiceEngine(1)
    )
    assert row["damage"] == 1


def test_defend_reduction_behaviour_unchanged():
    card = _card(atk=0, defense=1)
    enemy = _enemy(atk=4)
    plain = combat.enemy_strike(enemy, card, DiceEngine(5))
    guarded = combat.enemy_strike(enemy, card, DiceEngine(5), defending=True)
    assert guarded["damage"] == max(1, plain["damage"] - 2)
    assert combat.defend_reduction() == 2


def test_threat_folding_behaviour_unchanged():
    cfg = _cfg()
    weak = combat.enemy_from_declaration({"id": "e1", "threat": "weak"}, cfg, stage=1)
    normal = combat.enemy_from_declaration({"id": "e2", "threat": "normal"}, cfg, stage=1)
    boss = combat.enemy_from_declaration({"id": "e3", "threat": "boss"}, cfg, stage=1)
    assert (weak.hp, weak.atk, weak.defense) == (2, 1, 0)
    assert (normal.hp, normal.atk, normal.defense) == (4, 2, 1)
    assert (boss.hp, boss.atk, boss.defense) == (12, 4, 3)
    # 幕次加成：hp +bonus，atk +bonus//2，def 从第 3 幕起 +1
    late = combat.enemy_from_declaration({"id": "e4", "threat": "normal"}, cfg, stage=4)
    assert late.hp == 4 + 3
    assert late.atk == 2 + 1
    assert late.defense == 1 + 1


def test_declared_values_still_clamped():
    cfg = _cfg()
    enemy = combat.enemy_from_declaration(
        {"id": "e1", "threat": "weak", "hp": 999, "atk": 999, "def": 999}, cfg, stage=1
    )
    assert enemy.hp <= 30 and enemy.atk <= 30 and enemy.defense <= 30


def test_unknown_threat_falls_back_to_default_row():
    cfg = _cfg()
    enemy = combat.enemy_from_declaration({"id": "e1", "threat": "看不懂"}, cfg, stage=1)
    assert (enemy.hp, enemy.atk, enemy.defense) == (4, 2, 1)


def test_rules_can_be_overridden_by_caller():
    """世界包/测试可以传自己的战斗数值（这正是搬家的意义）。"""
    custom = {
        "hit_outcomes": ["success"],
        "attack": {"dice": 4, "min_damage": 3, "crit_multiplier": 3},
        "defend": {"reduction": 5},
        "enemy": {
            "threat": {"normal": {"hp": 9, "atk": 3, "def": 1}},
            "default_threat": "normal",
            "threat_stage_bonus": {1: 0},
            "clamp": {"min": 1, "max": 99},
            "floors": {"atk": 0, "def": 0},
            "tag_limit": 1,
        },
    }
    row = combat.resolve_attack(
        _result("success"), _card(atk=5), _enemy(defense=0), DiceEngine(1), custom
    )
    assert row["damage"] == 5 + row["roll"]  # 1d4
    assert row["damage"] >= 3  # min_damage
    miss = combat.resolve_attack(
        _result("crit_success"), _card(atk=5), _enemy(defense=0), DiceEngine(1), custom
    )
    assert miss["hit"] is False, "自定义命中集合里 crit_success 不算命中"


# ── 4. 读取层 ───────────────────────────────────────────────

def test_loader_exposes_combat_accessors():
    rules = Rules(ROOT)
    assert rules.combat_hit_outcomes == {"crit_success", "success", "fail_forward"}
    assert rules.combat_attack_dice == 6
    assert rules.combat_min_damage == 1
    assert rules.combat_crit_multiplier == 2
    assert rules.combat_defend_reduction == 2
    assert rules.combat_defend_duration_turns == 1
    assert rules.combat_enemy_clamp == (1, 30)
    assert rules.combat_threat_stage_bonus == {1: 0, 2: 1, 3: 2, 4: 3}
    assert rules.combat_enemy_threat["boss"]["hp"] == 12
