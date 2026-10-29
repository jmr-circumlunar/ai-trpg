"""出身（origin）/ 天赋（talents）/ 身份（path）的纯计算。

三层职业约定（知识库 02.1 §二）：
- `origin` 开局抽定，之后不可变
- `talents` 开局抽定（出身自带 + 天赋池）
- `path` 由剧情授予，只能经 `StateDelta` 的 `characters.<id>.path` 变更，
  且必须过四道闸门：`granted_by`（叙事来源）/ `requires_path` / 门槛（stage / stats / 血统）

本模块不调 LLM、不写库、不改 `GameState`——只回答「抽到谁」「能不能换」「换完给什么」。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

import random
from typing import Any

from ..models import CharacterCard, CharacterRuntime, GameState

MIN_STAT = 3
MAX_STAT = 18


class PathChangeError(ValueError):
    """身份变更被拒（一次性带出全部原因，便于回给 GM 重出）。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("身份变更被拒：" + "; ".join(errors))


# ── 抽取 ────────────────────────────────────────────────────
def _weight(item: dict) -> int:
    try:
        return max(0, int(item.get("weight") or 0))
    except (TypeError, ValueError):
        return 0


def roll_origin(pack, rng: random.Random) -> dict | None:
    """按权重抽一个出身。用传入 rng，保证同 seed 同结果。"""
    if pack is None:
        return None
    pool = pack.origins_pool() if hasattr(pack, "origins_pool") else []
    if not pool:
        return None
    total = sum(_weight(o) for o in pool)
    if total <= 0:
        return pool[0]
    point = rng.randrange(total)
    acc = 0
    for origin in pool:
        acc += _weight(origin)
        if point < acc:
            return origin
    return pool[-1]


def pick_talent_count(pack, rng: random.Random) -> int:
    """天赋抽取数量：世界包声明 `talent_count` 区间，代码在区间内掷。"""
    lo, hi = getattr(pack, "talent_count", (1, 2)) or (1, 2)
    lo, hi = int(lo), int(hi)
    if hi <= lo:
        return max(0, lo)
    return rng.randint(lo, hi)


def roll_talents(pack, rng: random.Random, count: int | None = None) -> list[dict]:
    """从天赋池抽 count 个（不重复，保序）。"""
    pool = [t for t in (getattr(pack, "talent_pool", None) or []) if t.get("id")]
    if not pool:
        return []
    n = pick_talent_count(pack, rng) if count is None else int(count)
    n = max(0, min(n, len(pool)))
    return rng.sample(pool, n) if n else []


def talent_labels(talents: list[dict]) -> list[str]:
    out: list[str] = []
    for t in talents:
        label = str(t.get("label") or t.get("id") or "").strip()
        if label and label not in out:
            out.append(label)
    return out


def talent_skill_bonus(talents: list[dict]) -> dict[str, int]:
    """天赋的结构化加成；没有 `skills` 的天赋只影响叙事。"""
    out: dict[str, int] = {}
    for t in talents:
        for skill, bonus in (t.get("skills") or {}).items():
            out[str(skill)] = out.get(str(skill), 0) + int(bonus)
    return out


def origin_stats(origin: dict | None, base: int = 10) -> dict[str, int]:
    """基础属性 + 出身 `stats_mod`，夹在 3~18。"""
    stats = {k: base for k in ("str", "dex", "int", "wis", "cha")}
    for attr, mod in ((origin or {}).get("stats_mod") or {}).items():
        stats[str(attr)] = min(MAX_STAT, max(MIN_STAT, stats.get(str(attr), base) + int(mod)))
    return stats


def origin_rank(pack, origin_id: str) -> int | None:
    """血统阶梯：以 `origins.yaml` 的声明顺序为准（越靠后越高）。"""
    if pack is None or not origin_id:
        return None
    for idx, item in enumerate(getattr(pack, "origins", None) or []):
        if str(item.get("id")) == str(origin_id):
            return idx
    return None


# ── 身份 ────────────────────────────────────────────────────
def current_path(card: CharacterCard | None, runtime: CharacterRuntime | None = None) -> str:
    """当前身份：运行时优先，其次卡上的初始值。"""
    if runtime is not None and runtime.path:
        return runtime.path
    if card is not None:
        return card.ext.path or ""
    return ""


def check_path_change(
    pack,
    state: GameState,
    card: CharacterCard | None,
    new_path: str,
    cid: str = "",
    current: str | None = None,
) -> list[str]:
    """身份变更闸门。返回空列表 = 允许；否则为拒绝原因。

    `current` 是变更前的身份：校验 `requires_path` 时必须看旧值，
    不能看已经写进 state 的新值（否则升级链形同虚设）。

    世界没有 `paths.yaml` 时不设闸门（只要求非空），保持旧世界兼容。
    """
    prefix = f"{cid}." if cid else ""
    text = str(new_path or "").strip()
    if not text:
        return [f"{prefix}path 不能为空"]

    paths = getattr(pack, "paths", None) if pack is not None else None
    if not paths:
        return []

    cfg = pack.path(text)
    if cfg is None:
        return [f"{prefix}非法身份 '{text}'（可用：{', '.join(pack.path_ids())}）"]

    errors: list[str] = []
    req = cfg.get("requirements") or {}

    stage_min = int(req.get("stage_min") or 1)
    if state.stage < stage_min:
        errors.append(f"{prefix}身份 {text} 需要幕次 ≥ {stage_min}（当前 {state.stage}）")

    stats = (card.ext.stats if card is not None else {}) or {}
    for attr, need in (req.get("stats_min") or {}).items():
        have = int(stats.get(str(attr), 0) or 0)
        if have < int(need):
            errors.append(f"{prefix}身份 {text} 需要 {attr} ≥ {need}（当前 {have}）")

    min_grade = req.get("min_grade")
    if min_grade:
        have_rank = origin_rank(pack, card.ext.origin if card is not None else "")
        need_rank = origin_rank(pack, str(min_grade))
        if have_rank is not None and need_rank is not None and have_rank < need_rank:
            errors.append(
                f"{prefix}身份 {text} 需要血统 ≥ {min_grade}（当前 {card.ext.origin}）"
            )

    requires = list(req.get("requires_path") or [])
    if requires:
        if current is not None:
            now = current
        else:
            now = current_path(card, state.characters.get(cid) if cid else None)
        if now not in requires:
            errors.append(
                f"{prefix}身份 {text} 只能从 {'/'.join(requires)} 转入（当前 {now or '未设定'}）"
            )
    return errors


def apply_grants(
    pack,
    card: CharacterCard,
    runtime: CharacterRuntime,
    path_id: str,
) -> dict[str, Any]:
    """把身份授予的东西加进角色卡与运行时。同一身份重复授予会自动跳过。"""
    cfg = pack.path(path_id) if pack is not None else None
    grants = (cfg or {}).get("grants") or {}
    applied: dict[str, Any] = {"skills": {}, "stats": {}, "tags": [], "flags": []}

    for skill, bonus in (grants.get("skills") or {}).items():
        key = str(skill)
        step = int(bonus)
        card.ext.skills[key] = card.ext.skills.get(key, 0) + step
        applied["skills"][key] = step

    for attr, bonus in (grants.get("stats") or {}).items():
        key = str(attr)
        step = int(bonus)
        card.ext.stats[key] = min(MAX_STAT, max(MIN_STAT, card.ext.stats.get(key, 10) + step))
        applied["stats"][key] = step

    for tag in grants.get("tags") or []:
        text = str(tag)
        if text and text not in card.data.tags:
            card.data.tags.append(text)
            applied["tags"].append(text)

    for flag in grants.get("flags") or []:
        text = str(flag)
        if text and text not in runtime.flags:
            runtime.flags.append(text)
            applied["flags"].append(text)

    return applied
