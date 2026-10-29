"""战斗（简单版）：命中归 judge，伤害归这里。

**数值只认 `rules/combat.yaml`**（S1-1 搬家的结果）：命中集合、伤害骰与最低伤害、
暴击倍率、防御减免、敌人威胁折算与幕次加成，全部在 yaml 里，代码只读不算。
本文件不再写任何战斗数值——改手感改 yaml，不改这里。

流程（语义与搬家前**逐值一致**）：

- 命中：按 yaml 的 `hit_outcomes` 判定；
- 伤害：武器 `atk` 合计 + 伤害骰 − 目标 `def`，不低于 `min_damage`；暴击按 `crit_multiplier` 放大；
- 防御：进入防御后本回合受到的伤害减少 `defend.reduction`；
- 逃跑：判定成功即脱战；
- 敌人反击：敌人 `atk` + 伤害骰 − 角色护甲 `def`，同样有下限。

GM 只负责写「这一下打成了什么样」；数值都由代码掷、代码算。

doc: 03-模块/M09-战斗/00-模块总述.md
module: M09
layer: L3
"""

from __future__ import annotations

from typing import Any

from ..models import CharacterCard, Enemy, GameState, JudgeResult, PlayerAction
from ..models.enums import CombatMode, Outcome
from ..rules.dice import DiceEngine
from ..rules.loader import load_combat_rules

# 默认战斗数值：惰性加载 `rules/combat.yaml`，只加载一次。
_DEFAULT_COMBAT: dict[str, Any] | None = None


def combat_rules(rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """取战斗数值表（`rules/combat.yaml`）。

    传 `rules` 用调用方那份（世界包/测试可以覆盖）；不传就用仓库默认那份。
    """
    global _DEFAULT_COMBAT
    if rules:
        return rules
    if _DEFAULT_COMBAT is None:
        _DEFAULT_COMBAT = load_combat_rules()
    return _DEFAULT_COMBAT


def hit_outcomes(rules: dict[str, Any] | None = None) -> set[str]:
    """哪些判定结果算「打中了」。"""
    return {str(x) for x in (combat_rules(rules).get("hit_outcomes") or [])}


def defend_reduction(rules: dict[str, Any] | None = None) -> int:
    """防御状态每回合减免的伤害。"""
    return int((combat_rules(rules).get("defend") or {}).get("reduction", 0))


def _damage_dice(rules: dict[str, Any] | None = None) -> int:
    return int((combat_rules(rules).get("attack") or {}).get("dice", 0) or 0)


def _min_damage(rules: dict[str, Any] | None = None) -> int:
    return int((combat_rules(rules).get("attack") or {}).get("min_damage", 0) or 0)


def _crit_multiplier(rules: dict[str, Any] | None = None) -> int:
    return int((combat_rules(rules).get("attack") or {}).get("crit_multiplier", 1) or 1)


def weapon_atk(card: CharacterCard) -> int:
    return sum(int((inst.attrs or {}).get("atk", 0) or 0) for inst in card.ext.equipment)


def armor_def(card: CharacterCard) -> int:
    return sum(int((inst.attrs or {}).get("def", 0) or 0) for inst in card.ext.equipment)


def is_hit(result: JudgeResult, rules: dict[str, Any] | None = None) -> bool:
    return result.outcome in hit_outcomes(rules)


def resolve_attack(
    result: JudgeResult,
    card: CharacterCard,
    enemy: Enemy,
    dice: DiceEngine,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """玩家攻击一次敌人：返回伤害与结算后 hp（不直接改 state）。"""
    if not is_hit(result, rules):
        return {
            "kind": "attack",
            "actor": card.name,
            "target": enemy.id,
            "hit": False,
            "damage": 0,
            "enemy_hp": enemy.hp,
            "note": "未命中",
        }

    roll = dice.d(_damage_dice(rules))
    raw = weapon_atk(card) + roll - enemy.defense
    damage = max(_min_damage(rules), raw)
    crit = result.outcome == Outcome.crit_success.value
    if crit:
        damage *= _crit_multiplier(rules)
    hp_left = max(0, enemy.hp - damage)
    return {
        "kind": "attack",
        "actor": card.name,
        "target": enemy.id,
        "hit": True,
        "crit": crit,
        "damage": damage,
        "roll": roll,
        "enemy_hp": hp_left,
        "killed": hp_left <= 0,
        "note": "击穿" if crit else "命中",
    }


def resolve_defend(
    card: CharacterCard, rules: dict[str, Any] | None = None
) -> dict[str, Any]:
    reduction = defend_reduction(rules)
    return {
        "kind": "defend",
        "actor": card.name,
        "target": "",
        "damage": 0,
        "note": f"进入防御，本回合受伤 −{reduction}",
    }


def resolve_flee(
    result: JudgeResult,
    card: CharacterCard,
    enemy: Enemy | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ok = result.outcome in {Outcome.crit_success.value, Outcome.success.value}
    return {
        "kind": "flee",
        "actor": card.name,
        "target": enemy.id if enemy else "",
        "escaped": ok,
        "costly": result.outcome == Outcome.fail_forward.value,
        "note": "脱战" if ok else "没能脱身",
    }


def enemy_strike(
    enemy: Enemy,
    card: CharacterCard,
    dice: DiceEngine,
    defending: bool = False,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """敌人反击一次：返回伤害（不直接改 state）。"""
    roll = dice.d(_damage_dice(rules))
    raw = int(enemy.atk) + roll - armor_def(card)
    if defending:
        raw -= defend_reduction(rules)
    damage = max(_min_damage(rules), raw)
    return {
        "kind": "enemy_strike",
        "enemy": enemy.id,
        "target": card.name,
        "damage": damage,
        "roll": roll,
        "note": "敌人命中",
    }


def enemy_from_declaration(
    item: dict,
    rules: dict[str, Any] | None = None,
    stage: int = 1,
) -> Enemy | None:
    """GM 申报「场上出现什么」→ 代码按威胁档折算数值。

    GM 只说 `threat`（weak / normal / strong / boss）；给具体 hp/atk/def 属于剧情特例，
    会被夹到 yaml 里的合理区间，防止一言不合造出离谱的怪。
    """
    eid = str((item or {}).get("id") or "").strip()
    if not eid:
        return None

    cfg = combat_rules(rules).get("enemy") or {}
    table = dict(cfg.get("threat") or {})
    default_key = str(cfg.get("default_threat") or "")
    threat = str(item.get("threat") or default_key)
    base = table.get(threat) or table.get(default_key) or next(iter(table.values()), {})
    bonus = int((cfg.get("threat_stage_bonus") or {}).get(int(stage), 0) or 0)
    divisor = max(1, int(cfg.get("atk_bonus_divisor") or 1))
    def_from = int(cfg.get("def_bonus_from_stage") or 0)
    def_amount = int(cfg.get("def_bonus_amount") or 0)
    floors = cfg.get("floors") or {}
    atk_floor = int(floors.get("atk", 0) or 0)
    def_floor = int(floors.get("def", 0) or 0)
    clamp = cfg.get("clamp") or {}
    clamp_min = int(clamp.get("min", 0) or 0)
    clamp_max = int(clamp.get("max", 0) or 0)
    tag_limit = int(cfg.get("tag_limit", 0) or 0)

    def _pick(key: str, fallback: int) -> int:
        raw = item.get(key)
        if raw is None:
            return fallback
        try:
            return max(clamp_min, min(clamp_max, int(raw)))
        except (TypeError, ValueError):
            return fallback

    hp = _pick("hp", int(base.get("hp", 0) or 0) + bonus)
    atk = _pick("atk", max(atk_floor, int(base.get("atk", 0) or 0) + bonus // divisor))
    defense = _pick(
        "def",
        max(def_floor, int(base.get("def", 0) or 0) + (def_amount if int(stage) >= def_from else 0)),
    )
    tags = [str(t) for t in (item.get("tags") or [])][:tag_limit]
    return Enemy(
        id=eid,
        name=str(item.get("name") or eid),
        hp=hp,
        hp_max=hp,
        atk=atk,
        **{"def": defense},
        tags=tags,
        condition=str(item.get("condition") or ""),
    )


def resolve(
    action: PlayerAction,
    card: CharacterCard,
    result: JudgeResult,
    state: GameState,
    dice: DiceEngine,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """把一次战斗意图落成结果。非战斗动作返回 None。"""
    if action.mode is None:
        return None
    enemy = state.enemies.get(str(action.target or ""))

    if action.mode == CombatMode.defend:
        return resolve_defend(card, rules)
    if action.mode == CombatMode.flee:
        return resolve_flee(result, card, enemy, rules)
    if enemy is None:
        return {
            "kind": "attack",
            "actor": card.name,
            "target": str(action.target or ""),
            "hit": False,
            "damage": 0,
            "note": "目标不存在",
        }
    return resolve_attack(result, card, enemy, dice, rules)
