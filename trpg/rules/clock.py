"""游戏时钟纯函数：推进、时段判定。

时间只以「分钟绝对值」参与运算，避免 hour 跨天时的边界错误。
"""

from __future__ import annotations

from typing import Any

MINUTES_PER_DAY = 24 * 60


def to_minutes(day: int, hour: int, minute: int = 0) -> int:
    return day * MINUTES_PER_DAY + hour * 60 + minute


def from_minutes(total: int) -> tuple[int, int, int]:
    day, rem = divmod(int(total), MINUTES_PER_DAY)
    hour, minute = divmod(rem, 60)
    return max(1, day), hour, minute


def advance(day: int, hour: int, minute: int, delta_minutes: int) -> tuple[int, int, int]:
    """推进若干分钟，返回 (day, hour, minute)。只允许前进。"""
    if delta_minutes < 0:
        raise ValueError("时间只能前进，不能倒退")
    return from_minutes(to_minutes(day, hour, minute) + int(delta_minutes))


def phase_of(hour: int, phases: dict[str, Any]) -> str:
    """当前小时落在哪个时段。支持跨午夜区间（如 23~4）。"""
    for name, cfg in phases.items():
        lo = int(cfg.get("from", 0))
        hi = int(cfg.get("to", 23))
        if lo <= hi:
            if lo <= hour <= hi:
                return name
        else:  # 跨午夜
            if hour >= lo or hour <= hi:
                return name
    return next(iter(phases), "unknown")


def phase_label(phase: str, phases: dict[str, Any]) -> str:
    return str((phases.get(phase) or {}).get("label", phase))


def skill_mod(phase: str, skill: str | None, phases: dict[str, Any]) -> int:
    if not skill:
        return 0
    mods = (phases.get(phase) or {}).get("mod") or {}
    return int(mods.get(skill, 0) or 0)
