"""装备实例生成：模板 / 故事 → 有数值的实例。

分工（知识库 02.2 §十四）：**GM 说「是什么」，代码算「多强」**。
- `instance_from_template`：初始配装与固定模板（不掷骰，取区间中位，保证同 seed 同配装）
- `roll_loot`：GM 申报的掉落或自造物（掷 tier / 成色 / 属性）
- `tool_dc_bonus`：装备 tag 带来的 DC 修正（撬锁工具 −2 这类），交给 judge 使用
- `roll_affixes` / `apply_affixes`：**词缀**（S6-1）—— 词缀 × 槽位 × 品阶 三维限定，
  价值在 `rules/equipment.yaml: affixes` 的区间内取值；同组词缀互斥；
  扩展字段（`extra`）的键**由世界包声明白名单**，框架不塞世界语义。
  默认 `affix_count=0` → **缺省行为与改造前一致**。

数值全部来自 `rules/equipment.yaml`，本文件不写死任何区间。

doc: 03-模块/M08-装备与物品/00-模块总述.md
module: M08
layer: L3
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from ..models import EquipmentInstance, EquipmentSource
from ..models.enums import Quality, SourceKind

# slot → 用哪一档属性区间（`tier_range` 只分了 weapon / armor / trinket）
SLOT_CLASS = {
    "weapon": "weapon",
    "armor": "armor",
    "shield": "armor",
    "trinket": "trinket",
    "tool": "trinket",
    "consumable": "trinket",
    "outfit": "trinket",
}

_ATTR_KEY = {"weapon": "atk", "armor": "def", "trinket": "ward"}


def _new_id() -> str:
    return f"eq_{uuid.uuid4().hex[:6]}"


def _mid(span: Any) -> int:
    if isinstance(span, (list, tuple)) and span:
        lo, hi = int(span[0]), int(span[-1])
        return (min(lo, hi) + max(lo, hi)) // 2
    try:
        return int(span)
    except (TypeError, ValueError):
        return 0


def _roll_span(span: Any, rng: random.Random | None) -> int:
    if isinstance(span, (list, tuple)) and span:
        lo, hi = int(span[0]), int(span[-1])
        lo, hi = min(lo, hi), max(lo, hi)
        return rng.randint(lo, hi) if rng is not None else (lo + hi) // 2
    try:
        return int(span)
    except (TypeError, ValueError):
        return 0


def _attrs(attrs: dict, rng: random.Random | None, mult: float) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, span in (attrs or {}).items():
        value = _roll_span(span, rng)
        out[str(key)] = max(0, int(round(value * mult))) if mult != 1.0 else int(value)
    return out


def instance_from_template(
    tpl: dict,
    *,
    owner_id: str | None = None,
    rng: random.Random | None = None,
    quality: Quality = Quality.common,
    kind: SourceKind = SourceKind.initial,
    session_id: str | None = None,
    turn: int | None = None,
    name_flavor: str = "",
) -> EquipmentInstance:
    """按模板造一件实例。不给 rng 时取区间中位（初始配装要稳定）。"""
    return EquipmentInstance(
        id=_new_id(),
        template_id=str(tpl.get("id") or ""),
        tier=int(tpl.get("tier") or 1),
        quality=quality,
        enhance=0,
        req_level=int(tpl.get("req_level") or 1),
        attrs=_attrs(tpl.get("attrs") or {}, rng, quality.mult),
        tags=[str(t) for t in (tpl.get("tags") or [])][:3],
        name_flavor=name_flavor or str(tpl.get("name") or ""),
        source=EquipmentSource(kind=kind, session_id=session_id, turn=turn),
        owner_id=owner_id,
    )


def affix_candidates(eqcfg: dict, slot: str, tier: int) -> list[dict]:
    """能挂到「这个槽位 + 这个品阶」上的词缀（**三维限定**的过滤结果）。

    词缀声明来自 `rules/equipment.yaml: affixes`（世界包可覆盖），
    这里不写死任何词缀名、槽位名或区间。
    """
    out: list[dict] = []
    for item in eqcfg.get("affixes") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        slots = [str(x) for x in (item.get("slots") or [])]
        tiers = [int(x) for x in (item.get("tiers") or [])]
        if slots and str(slot) not in slots:
            continue
        if tiers and int(tier) not in tiers:
            continue
        out.append(dict(item))
    return out


def roll_affixes(
    eqcfg: dict,
    slot: str,
    tier: int,
    *,
    rng: random.Random | None = None,
    count: int = 1,
    allowed_extra: tuple[str, ...] | list[str] = (),
) -> list[dict]:
    """掷出词缀（可生成范围内，价值在区间内取值）。

    三条规则：
    - **槽位 × 品阶**限定候选（`affix_candidates`）；
    - **互斥组**：同一件装备上同组词缀只能有一个；
    - **价值区间**：在 `value_range` 内取值；不给 rng 时取中位（同 seed 稳定）。
    """
    pool = affix_candidates(eqcfg, slot, tier)
    if not pool or count <= 0:
        return []
    chosen: list[dict] = []
    used_groups: set[str] = set()
    for candidate in pool:
        if len(chosen) >= int(count):
            break
        group = str(candidate.get("group") or candidate.get("id"))
        if group in used_groups:
            continue
        used_groups.add(group)
        value = _roll_span(candidate.get("value_range") or [0, 0], rng)
        entry: dict[str, Any] = {
            "id": str(candidate.get("id")),
            "label": str(candidate.get("label") or candidate.get("id")),
            "value": int(value),
            "effect": dict(candidate.get("effect") or {}),
        }
        # 扩展字段：**只在世界包声明的白名单里**才带进来（框架不塞世界语义）
        extras = {
            str(k): v
            for k, v in (candidate.get("extra_fields") or {}).items()
            if str(k) in {str(x) for x in allowed_extra}
        }
        if extras:
            entry["extra_fields"] = extras
        chosen.append(entry)
    return chosen


def apply_affixes(
    instance: EquipmentInstance,
    affixes: list[dict],
    *,
    allowed_extra: tuple[str, ...] | list[str] = (),
) -> EquipmentInstance:
    """把词缀装到实例上（属性加成 + 自定义字段）。

    `effect: {attr: atk}` = 给 `atk` 加 `value`；自定义字段只接受白名单内的键。
    """
    allow = {str(x) for x in allowed_extra}
    for affix in affixes or []:
        effect = dict((affix or {}).get("effect") or {})
        value = int((affix or {}).get("value") or 0)
        # 声明形态：`{attr: atk}` = 给 atk 加 value；也兼容 `{atk: 2}`（属性名 + 倍率）
        attr = str(effect.get("attr") or "")
        if attr:
            if attr in ("atk", "def", "ward"):
                instance.attrs[attr] = int(instance.attrs.get(attr, 0)) + value
        else:
            for key, per in effect.items():
                name = str(key)
                if name.startswith("attr:"):
                    name = name[5:]
                if name in ("atk", "def", "ward"):
                    instance.attrs[name] = int(instance.attrs.get(name, 0)) + value * int(per or 1)
        for key, val in ((affix or {}).get("extra_fields") or {}).items():
            if str(key) in allow:
                instance.extra[str(key)] = val
    instance.affixes = [
        {
            "id": str(item.get("id")),
            "label": str(item.get("label") or item.get("id")),
            "value": int(item.get("value") or 0),
            "effect": dict(item.get("effect") or {}),
        }
        for item in affixes or []
    ]
    return instance


def quality_from_roll(roll: int, eqcfg: dict, *, tension: int = 0) -> Quality:
    """2d6 + stage_quality_mod 落在哪个成色区间；传说额外要求张力够高。"""
    table = eqcfg.get("quality") or {}
    for name in ("rough", "common", "fine", "rare", "legendary"):
        span = (table.get(name) or {}).get("roll")
        if not span:
            continue
        lo, hi = int(span[0]), int(span[-1])
        if lo <= roll <= hi:
            if name == "legendary":
                need = int(((table.get(name) or {}).get("require") or {}).get("tension_gt") or 0)
                if tension <= need:
                    return Quality.rare
            return Quality(name)
    return Quality.common


def quality_by_stage(
    eqcfg: dict,
    stage: int,
    *,
    rng: random.Random,
    tension: int = 0,
) -> Quality | None:
    """按幕次的**品质权重表**抽一个品质。

    权重表形如 `quality_weights.by_stage: {1: {rough: 30, common: 50, ...}}`：
    每一幕一行，行内是各品质的相对权重（框架不解释数值大小）。

    **配置缺省 → 返回 None**（走原来的 2d6 映射，行为零变化）；
    **同 seed 同输入 → 同结果**（用同一个 rng 流抽）。
    """
    table = (eqcfg.get("quality_weights") or {}).get("by_stage") or {}
    if not table:
        return None
    weights = table.get(str(int(stage))) or table.get(int(stage)) or {}
    items = [
        (str(name), float(value))
        for name, value in (weights or {}).items()
        if float(value or 0) > 0
    ]
    if not items:
        return None
    total = sum(weight for _, weight in items)
    pick = rng.random() * total
    acc = 0.0
    chosen = items[-1][0]
    for name, weight in items:
        acc += weight
        if pick < acc:
            chosen = name
            break
    if chosen == "legendary":
        need = int(
            (((eqcfg.get("quality") or {}).get("legendary") or {}).get("require") or {}).get(
                "tension_gt"
            )
            or 0
        )
        if tension <= need:
            return Quality.rare
    return Quality(chosen)


def tier_cap(era_cfg: Any = None, stage: int = 1) -> int:
    """本期可出的最高档：常规 ≤ 3，第 3 幕起放 4；再高只能走剧情奖励。"""
    limit = 3 + (1 if int(stage) >= 3 else 0)
    era_max = int(getattr(era_cfg, "tier_max", 5) or 5)
    return max(1, min(limit, era_max))


def roll_loot(
    *,
    name: str,
    eqcfg: dict,
    rng: random.Random,
    era_cfg: Any = None,
    stage: int = 1,
    tension: int = 0,
    slot: str = "trinket",
    tags: list[str] | None = None,
    tier_hint: int | None = None,
    quality_roll: int | None = None,
    owner_id: str | None = None,
    template_id: str = "",
    session_id: str | None = None,
    turn: int | None = None,
    affix_count: int = 0,
    allowed_extra: tuple[str, ...] | list[str] = (),
) -> EquipmentInstance:
    """GM 申报「拿到什么东西」→ 代码决定它多强。"""
    slot_key = SLOT_CLASS.get(str(slot or "").strip().lower(), "trinket")
    cap = tier_cap(era_cfg, stage)
    if tier_hint is not None:
        tier = max(1, min(cap, int(tier_hint)))
    else:
        tier = rng.randint(max(1, cap - 1), cap)

    stage_mod = int((eqcfg.get("stage_quality_mod") or {}).get(int(stage), 0) or 0)
    if quality_roll is not None:
        roll = int(quality_roll)
        quality = quality_from_roll(roll, eqcfg, tension=tension)
    else:
        # S6-2：配置了按幕次的品质权重表就走权重抽；缺省仍走 2d6（行为零变化）
        quality = quality_by_stage(eqcfg, stage, rng=rng, tension=tension)
        if quality is None:
            roll = rng.randint(1, 6) + rng.randint(1, 6) + stage_mod
            quality = quality_from_roll(roll, eqcfg, tension=tension)
        else:
            roll = 0

    spans = (eqcfg.get("tier_range") or {}).get(tier) or {}
    span = spans.get(slot_key) or [0, 1]
    attrs = {_ATTR_KEY.get(slot_key, "ward"): _roll_span(span, rng)}
    for key, value in attrs.items():
        attrs[key] = int(round(value * quality.mult))

    instance = EquipmentInstance(
        id=_new_id(),
        template_id=str(template_id or ""),
        tier=tier,
        quality=quality,
        enhance=0,
        req_level=max(1, tier - 1),
        attrs=attrs,
        tags=[str(t) for t in (tags or [])][:3],
        name_flavor=str(name or "").strip(),
        source=EquipmentSource(kind=SourceKind.loot, session_id=session_id, turn=turn),
        owner_id=owner_id,
    )
    # S6-1：词缀。**默认不掷**（`affix_count=0`）→ 缺省行为与改造前逐字节一致；
    # 要带词缀的局由调用方显式开口子（词缀 × 槽位 × 品阶 三维限定）。
    if int(affix_count) > 0:
        affixes = roll_affixes(
            eqcfg,
            slot_key,
            tier,
            rng=rng,
            count=int(affix_count),
            allowed_extra=allowed_extra,
        )
        apply_affixes(instance, affixes, allowed_extra=allowed_extra)
    return instance


def tool_dc_bonus(tags: list[str] | None, eqcfg: dict, skill: str | None = None) -> int:
    """装备 tag 带来的 DC 修正（合计，负数是更容易）。

    表结构支持两种写法（见 `rules/equipment.yaml: tag_dc_bonus`）：
    - `{lockpick: -2}`：持有即生效
    - `{lockpick: {skills: [撬锁], dc: -2}}`：只在对应技能上生效
    """
    table = eqcfg.get("tag_dc_bonus") or {}
    total = 0
    for tag in tags or []:
        cfg = table.get(str(tag))
        if cfg is None:
            continue
        if isinstance(cfg, (int, float)):
            total += int(cfg)
            continue
        if not isinstance(cfg, dict):
            continue
        wanted = cfg.get("skills") or []
        if skill and wanted and skill not in wanted:
            continue
        try:
            total += int(cfg.get("dc") or 0)
        except (TypeError, ValueError):
            pass
    return total


def equipment_tags(card) -> list[str]:
    """角色身上所有装备的 tag（去重），供判定与叙事使用。"""
    tags: list[str] = []
    for inst in getattr(getattr(card, "ext", None), "equipment", None) or []:
        for tag in getattr(inst, "tags", None) or []:
            text = str(tag)
            if text and text not in tags:
                tags.append(text)
    return tags
