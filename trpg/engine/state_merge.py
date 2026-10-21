"""StateDelta 合并与校验。

GM 只能输出增量，且只能改白名单路径；越界一律整包回滚。
这是「单一真相源」在数据层的落地，也是 S0 最该先测的东西。
"""

from __future__ import annotations

import re
from typing import Any

from ..models import CharacterCard, GameState, StateDelta
from ..models.enums import DeltaOp
from ..rules import clock as clock_mod

_MAX_TENSION_DELTA = 20
_MAX_HP_RATIO = 0.6

_ALLOWED_PATTERNS = [
    re.compile(r"^characters\.[A-Za-z0-9_]+\.(hp|sanity|conditions|inventory|position)$"),
    re.compile(r"^party\.(gold|supplies|flags|reputation)$"),
    # 时间只能通过 clock.advance（分钟数）前进，day/hour/minute 不允许直接 set
    re.compile(r"^clock\.(advance|weather)$"),
    re.compile(r"^tension$"),
    re.compile(r"^stage$"),
    re.compile(r"^scene\.(location_id|present_npcs|exits|description)$"),
    re.compile(r"^quests$"),
]

_FORBIDDEN_PATTERN = re.compile(r"^(version|session_id|world|schema_version)(\.|$)")


class DeltaValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("StateDelta 校验失败：" + "; ".join(errors))


def _path_allowed(path: str) -> bool:
    if _FORBIDDEN_PATTERN.match(path):
        return False
    return any(p.match(path) for p in _ALLOWED_PATTERNS)


def _get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part)
    return cur


def _set(obj: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part)
    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
    else:
        setattr(cur, last, value)


def apply_delta(
    state: GameState,
    delta: StateDelta,
    cards: dict[str, CharacterCard] | None = None,
    clock_cfg: dict | None = None,
) -> GameState:
    """应用增量并校验，返回新 state（version+1）。失败抛 DeltaValidationError。"""
    cards = cards or {}
    clock_cfg = clock_cfg or {}
    step_min = int(clock_cfg.get("min_step_minutes", 5))
    step_max = int(clock_cfg.get("max_step_minutes", 480))
    clock_total = 0
    errors: list[str] = []

    # 1) 路径白名单
    for ch in delta.changes:
        if not _path_allowed(ch.path):
            errors.append(f"路径不允许修改：{ch.path}")
    if errors:
        raise DeltaValidationError(errors)

    old_hp = {cid: rt.hp for cid, rt in state.characters.items()}
    old_tension = state.tension
    old_stage = state.stage

    new = state.model_copy(deep=True)

    # 2) 逐条应用
    for ch in delta.changes:
        # 时间推进是虚拟路径，需要特殊处理（跨天进位 + 档位校验）
        if ch.path == "clock.advance":
            try:
                minutes = int(ch.value or 0)
            except (TypeError, ValueError):
                errors.append("clock.advance 必须是整数分钟")
                continue
            if minutes < step_min:
                errors.append(f"clock.advance 小于最小档 {step_min} 分钟（收到 {minutes}）")
                continue
            if minutes > step_max:
                errors.append(f"clock.advance 超过单回合上限 {step_max} 分钟（收到 {minutes}）")
                continue
            clock_total += minutes
            if clock_total > step_max:
                errors.append(f"单回合累计推进超过 {step_max} 分钟")
                continue
            d, h, m = clock_mod.advance(
                new.clock.day, new.clock.hour, new.clock.minute, minutes
            )
            new.clock.day, new.clock.hour, new.clock.minute = d, h, m
            continue

        try:
            cur = _get(new, ch.path)
        except (AttributeError, KeyError):
            errors.append(f"路径不存在：{ch.path}")
            continue

        if ch.op == DeltaOp.set:
            _set(new, ch.path, ch.value)
        elif ch.op == DeltaOp.add:
            if isinstance(cur, list):
                _set(new, ch.path, [*cur, ch.value])
            else:
                _set(new, ch.path, (cur or 0) + ch.value)
        elif ch.op == DeltaOp.sub:
            if not isinstance(cur, int):
                errors.append(f"sub 只能用于数值：{ch.path}")
            else:
                _set(new, ch.path, cur - int(ch.value))

    # 3) 边界与幅度
    for cid, rt in new.characters.items():
        card = cards.get(cid)
        hp_max = card.ext.hp_max if card else None

        if rt.hp < 0:
            errors.append(f"{cid}.hp < 0")
        if hp_max is not None:
            if rt.hp > hp_max:
                errors.append(f"{cid}.hp > hp_max({hp_max})")
            prev = old_hp.get(cid)
            if prev is not None and abs(rt.hp - prev) > hp_max * _MAX_HP_RATIO:
                errors.append(f"{cid}.hp 单回合变化超过 {int(_MAX_HP_RATIO * 100)}%")
        if rt.sanity < 0:
            errors.append(f"{cid}.sanity < 0")

    if not 0 <= new.tension <= 100:
        errors.append(f"tension 越界：{new.tension}")
    if abs(new.tension - old_tension) > _MAX_TENSION_DELTA:
        errors.append(f"tension 单回合变化超过 ±{_MAX_TENSION_DELTA}")

    if new.stage not in (old_stage, old_stage + 1):
        errors.append(f"stage 只能 +1，当前 {old_stage} -> {new.stage}")

    if new.party.gold < 0:
        errors.append("party.gold 不得为负")
    if new.party.supplies < 0:
        errors.append("party.supplies 不得为负")

    # 4) 结构复校验：任何 set/add 进来的值都必须符合 schema
    #    （例如 quests 被替换成 dict 列表时，这里会转回 Quest 或直接报错）
    try:
        new = GameState.model_validate(new.model_dump())
    except Exception as exc:
        errors.append(f"state 结构非法：{str(exc)[:200]}")

    if errors:
        raise DeltaValidationError(errors)

    new.version = state.version + 1
    return new
