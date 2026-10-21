"""判定：纯代码，无 LLM。

DC 由规则表算，GM 只能给 situation_delta（±2，代码截断）。
结果为四档 outcome，GM 只能解释，不能改判。
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
    if face == rules.crit_face or margin >= rules.crit_margin:
        return Outcome.crit_success
    if margin >= 0:
        return Outcome.success
    if margin >= rules.fail_forward_floor:
        return Outcome.fail_forward
    return Outcome.failure


def judge(
    action: PlayerAction,
    card: CharacterCard,
    state: GameState,
    rules: Rules,
    dice: DiceEngine,
    situation_delta: int = 0,
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
    )

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
