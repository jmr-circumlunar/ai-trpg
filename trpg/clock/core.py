"""游戏时钟纯函数：推进、时段判定。

时间只以「分钟绝对值」参与运算，避免 hour 跨天时的边界错误。

doc: 03-模块/M06-时间/00-模块总述.md
module: M06
layer: L3
"""

from __future__ import annotations

import datetime as date_module
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


WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def date_label(clock, phases: dict[str, Any] | None = None) -> str:
    """把钟渲染成人能读的时间串：`2026年9月1日 星期二 上午 08:12`。

    真实日期是派生值：`start_date + (day - 1)`。没有 `start_date` 时退回「第 N 天」，
    这样无日历的世界（或旧档）也不会报错。
    """
    phases = phases or {}
    day = int(getattr(clock, "day", 1) or 1)
    hour = int(getattr(clock, "hour", 0) or 0)
    minute = int(getattr(clock, "minute", 0) or 0)
    hm = f"{hour:02d}:{minute:02d}"

    head = f"第 {day} 天"
    start = str(getattr(clock, "start_date", "") or "").strip()
    if start:
        try:
            base = date_module.date.fromisoformat(start)
            cur = base + date_module.timedelta(days=day - 1)
            head = f"{cur.year}年{cur.month}月{cur.day}日 {WEEKDAYS[cur.weekday()]}"
        except ValueError:
            head = f"第 {day} 天"

    label = phase_label(phase_of(hour, phases), phases) if phases else ""
    return f"{head} {label} {hm}".strip()
