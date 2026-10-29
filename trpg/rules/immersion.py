"""介入规则：什么时候"该有人来"（纯函数，不调 LLM）。

设计边界：
- **判定归代码**：谁该出现、多久到场，由这里算；GM 只负责把到场写成场面。
- 不制造"凭空变化"：触发条件是**已发生的事实**（近几回合有战斗 / 张力够高）。
- 可以被玩家不利（记过、被带走问话），但**不直接致死**——致死是战斗结算的事。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

from typing import Any


def turns_window(order: str, cfg: dict[str, Any] | None = None) -> int:
    """该秩序等级允许多晚到场；0 = 永不强制。"""
    cfg = cfg or {}
    table = cfg.get("turns_by_order") or {}
    try:
        return int(table.get(str(order or ""), 0) or 0)
    except (TypeError, ValueError):
        return 0


def responders(
    turn: int,
    combat_turns: list[int],
    tension: int,
    order: str,
    authority: list[str] | None,
    cfg: dict[str, Any] | None = None,
    last_response_turn: int = 0,
) -> list[str]:
    """返回本回合应强制到场的 authority NPC id（最多一个）。

    命中任一条件即到场：
    1. 近 `window` 回合内发生过战斗；
    2. 张力 ≥ `tension_threshold`。
    `cooldown_turns` 内不重复派人——否则冲突一次会变成"每回合都来一队人"。
    """
    cfg = cfg or {}
    names = [str(a) for a in (authority or []) if str(a)]
    if not names:
        return []
    window = turns_window(order, cfg)
    if window <= 0:
        return []

    cooldown = int(cfg.get("cooldown_turns", 3) or 0)
    if last_response_turn and turn - last_response_turn <= cooldown:
        return []

    recent = [t for t in combat_turns if 0 <= turn - int(t) <= window]
    tension_hit = int(tension or 0) >= int(cfg.get("tension_threshold", 45) or 45)
    if not recent and not tension_hit:
        return []
    return names[:1]
