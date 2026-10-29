"""M18 谓词注册表：`phases[].when` / `phases[].break_if` 的全部表达能力。

三条硬约束：
1. **纯函数**：只读 state，不改它、不调模型、不掷骰、不写库；
2. **可复现**：同一 state + 同一参数 → 同一结果；
3. **依赖显式**：`needs` 在 `manifest.py` 声明，缺依赖系统时 `check_world` 直接报错，
   不允许「条件永远为假」的静默失效（C48）。

调用方（pipeline）负责把 state 之外的上下文填进 `PredicateContext`：
地点 tags、是否有人类席位、时段定义——谓词自己不读文件。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..clock.core import phase_of
from ..models import GameState


class PredicateError(ValueError):
    """谓词名不存在，或参数缺失/不合法。"""


@dataclass(frozen=True)
class PredicateContext:
    """谓词求值所需的全部输入（state + 由调用方补齐的上下文）。"""

    state: GameState
    turn: int
    params: Mapping[str, Any] = field(default_factory=dict)
    # rules/clock.yaml 的 phases（clock_phase 用）
    phases: Mapping[str, Any] = field(default_factory=dict)
    # 当前地点的 tags（location_tag 用，由 pipeline 从世界包查好后传入）
    location_tags: tuple[str, ...] = ()
    # 本局是否有人类席位（has_human_seat 用）
    human_seat: bool = False


def _param(ctx: PredicateContext, key: str) -> Any:
    value = ctx.params.get(key)
    if value is None or value == "":
        raise PredicateError(f"谓词缺少参数 {key}")
    return value


def _always(ctx: PredicateContext) -> bool:
    return True


def _enemies_present(ctx: PredicateContext) -> bool:
    return any(enemy.hp > 0 for enemy in ctx.state.enemies.values())


def _in_combat(ctx: PredicateContext) -> bool:
    """战斗中：场上有存活敌人，或已被标记为战斗轮（对峙/追逃）。"""
    return _enemies_present(ctx) or "in_combat" in ctx.state.party.flags


def _every_n(ctx: PredicateContext) -> bool:
    n = int(_param(ctx, "n"))
    if n <= 0:
        raise PredicateError("every_n 的 n 必须 ≥ 1")
    return ctx.turn % n == 0


def _stage_is(ctx: PredicateContext) -> bool:
    return ctx.state.stage == int(_param(ctx, "stage"))


def _quest_active(ctx: PredicateContext) -> bool:
    quest_id = str(_param(ctx, "quest"))
    return any(q.id == quest_id and q.status == "active" for q in ctx.state.quests)


def _location_tag(ctx: PredicateContext) -> bool:
    return str(_param(ctx, "tag")) in set(ctx.location_tags)


def _flag_set(ctx: PredicateContext) -> bool:
    return str(_param(ctx, "flag")) in set(ctx.state.party.flags)


def _has_human_seat(ctx: PredicateContext) -> bool:
    return bool(ctx.human_seat)


def _clock_phase(ctx: PredicateContext) -> bool:
    if not ctx.phases:
        raise PredicateError("clock_phase 需要 rules/clock.yaml 的 phases")
    current = phase_of(ctx.state.clock.hour, dict(ctx.phases))
    return current == str(_param(ctx, "phase"))


def _win_decided(ctx: PredicateContext) -> bool:
    """胜负已定：由 `evaluate_win()` 的调用方写进 `state.winner`（C58）。

    没有阵营的世界 `winner` 恒为空 → 恒为假，等于「永不提前结束」，行为不变。
    """
    return bool(str(getattr(ctx.state, "winner", "") or ""))


PREDICATES: dict[str, Callable[[PredicateContext], bool]] = {
    "always": _always,
    "enemies_present": _enemies_present,
    "in_combat": _in_combat,
    "every_n": _every_n,
    "stage_is": _stage_is,
    "quest_active": _quest_active,
    "location_tag": _location_tag,
    "flag_set": _flag_set,
    "has_human_seat": _has_human_seat,
    "clock_phase": _clock_phase,
    "win_decided": _win_decided,
}


def evaluate(name: str, ctx: PredicateContext) -> bool:
    """求值一个谓词；名字不在注册表或参数非法时抛 `PredicateError`。"""
    fn = PREDICATES.get(name)
    if fn is None:
        raise PredicateError(
            f"未知谓词：{name}（可用：{', '.join(sorted(PREDICATES))}）"
        )
    return bool(fn(ctx))
