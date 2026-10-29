"""天气演化钩子：框架只给钩子，词表与过渡在 yaml / 世界包。

I-1 / I-2：生态里的天气系统全是渲染层，没有一个是「游戏规则层的天气」。
所以边界很清楚——词表与影响值是内容（rules/clock.yaml），演化规则是机制（本模块），
框架绝不能有天气表（check_generic 守）：本文件里没有任何天气名。

形态：先做离散标签加过渡声明（不做连续量）。过渡表为空 = 不演化（缺省零变化）。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from typing import Any

from ..models.rng import derive


def transitions_of(rules_clock: dict[str, Any] | None) -> dict[str, Any]:
    """从时钟配置里取天气过渡表（没有就是空 = 不演化）。"""
    return dict((rules_clock or {}).get("weather_transitions") or {})


def next_weather(
    current: str,
    transitions: dict[str, Any],
    *,
    turn: int,
    seed: Any = 0,
) -> str | None:
    """按过渡声明推出下一档天气；不可转或未声明返回 None（保持不变）。

    transitions 形如 {当前标签: [候选标签]} 或 {当前标签: {to: [候选标签]}}。
    同 seed 同 turn 得同结果（用 seed 派生，与事件调度器同一套确定性做法）。
    """
    if not current or not transitions:
        return None
    item = transitions.get(current)
    if item is None:
        return None
    options = item.get("to") if isinstance(item, dict) else item
    choices = [str(x) for x in (options or []) if str(x)]
    if not choices:
        return None
    rng = derive(seed, "weather", int(turn))
    return choices[int(rng.random() * len(choices)) % len(choices)]


def advance_weather(
    current: str,
    transitions: dict[str, Any],
    *,
    turn: int,
    seed: Any = 0,
) -> tuple[str, bool]:
    """推进一格天气，返回 (新天气, 是否变化)。"""
    nxt = next_weather(current, transitions, turn=turn, seed=seed)
    if not nxt or nxt == current:
        return current, False
    return nxt, True


__all__ = ["advance_weather", "next_weather", "transitions_of"]
