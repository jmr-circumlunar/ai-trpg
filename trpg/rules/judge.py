"""判定：纯代码，无 LLM。

DC 由规则表算，GM 只能给 situation_delta（±2，代码截断）。
结果为四档 outcome，GM 只能解释，不能改判。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

from ..models import CharacterCard, GameState, JudgeResult, Outcome, PlayerAction
from ..rules.dice import DiceEngine
from ..rules.loader import Rules


def _best_attr(stats: dict[str, int]) -> str:
    if not stats:
        return "dex"
    return max(stats.items(), key=lambda kv: kv[1])[0]


def _outcome(face: int, margin: int, rules: Rules) -> Outcome:
    """四档判定走 `rules/ruleset.yaml: outcomes`（S1-3）。

    档位 id、阈值与判定顺序都在 yaml（数值真相源）；这里只把结果转回枚举。
    **行为与老实现逐档等价** —— `tests/test_dice_analysis.py` 穷举 face×margin 对比证明。
    """
    return Outcome(rules.outcome_for(face, margin))


# 「不判定」的结果标记。它不是成功也不是失败，是「这一局不掷骰」（C55）。
AUTO_OUTCOME = "auto"


def auto_result(action: PlayerAction, rules: Rules) -> JudgeResult:
    """无判定时的占位结果。

    为什么还要一个 result：时间兜底要 `action_type`、战斗结算要 `outcome` 字段存在，
    直接让 `results` 缺项会在下游炸。但 `outcome` 是 `auto`，调用方不会把它写进
    `TurnRecord.outcomes`（方案 28 §七 要求关判定时 `outcomes` 为空）。
    """
    skill = action.skill if rules.valid_skill(action.skill) else None
    return JudgeResult(
        actor_id=action.actor_id,
        skill=skill,
        action_type=rules.action_type_for_skill(skill),
        attr="",
        attr_mod=0,
        skill_bonus=0,
        dc=0,
        expr="",
        rolls=[],
        total=0,
        margin=0,
        outcome=AUTO_OUTCOME,
    )


def judge(
    action: PlayerAction,
    card: CharacterCard,
    state: GameState,
    rules: Rules,
    dice: DiceEngine,
    situation_delta: int = 0,
    tool_bonus: int = 0,
    attitude_score: int = 0,
) -> JudgeResult:
    ext = card.ext

    # 技能不在表内 -> 降级为通用检定（不报错，这是设计）
    skill = action.skill if rules.valid_skill(action.skill) else None
    attr = rules.attr_for_skill(skill) or _best_attr(ext.stats)

    attr_value = ext.stats.get(attr, 10)
    attr_mod = (attr_value - 10) // 2
    skill_bonus = ext.skills.get(skill, 0) if skill else 0

    action_type = rules.action_type_for_skill(skill)
    phase = rules.phase_of(state.clock.hour)
    dc = rules.dc(
        action_type,
        state.stage,
        action.intent,
        situation_delta,
        skill=skill,
        phase=phase,
        # B1：张力归代码判定，模型不得自己加难度
        tension=state.tension,
        # B2：势力态度分由 M13 合成后传入（M02 不认识世界包）
        attitude_score=attitude_score,
    )
    # 装备 tag 修正（撬锁工具 −2 这类）：由调用方（M13）从 M08 算好传进来，
    # 规则层不认识装备模块——这是 L1 与 L3 的单向分界。
    dc += int(tool_bonus or 0)
    # 天气修正（雾里观察更难这类）：与时段同一套符号，正向 = 更难
    dc += rules.weather_mod(state.clock.weather, skill)

    expr = f"1d{rules.dice_sides}"
    rolls, raw = dice.roll(expr)
    total = raw + attr_mod + skill_bonus
    margin = total - dc
    outcome = _outcome(rolls[0], margin, rules)

    return JudgeResult(
        actor_id=action.actor_id,
        skill=skill,
        action_type=action_type,
        phase=phase,
        attr=attr,
        attr_mod=attr_mod,
        skill_bonus=skill_bonus,
        dc=dc,
        expr=f"{expr}{attr_mod:+d}" if attr_mod else expr,
        rolls=rolls,
        total=total,
        margin=margin,
        outcome=outcome.value,
        rng_state=dice.rng_state,
    )
