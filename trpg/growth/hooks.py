"""世界包的「局外成长声明」。

世界包用 `meta_hooks.yaml` 声明：
- `currency`：本地怎么称呼这些点、怎么换算；
- `earn`：局内哪些事件能赚点；
- `spend`：点能买什么（**target 必须引用本世界已有数据**）；
- `carry`：允许把哪些元成长带进本世界。

引擎（`trpg/meta/*`）不含任何世界知识：它只认 `kind` 与字段。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 引擎认识的 kind（世界包只能用这些）
SPEND_KINDS = {"skill", "talent", "trait", "path", "item"}
CARRY_KINDS = {"trait", "item"}
EARN_EVENTS = {
    "ending",
    "combat_won",
    "identity_granted",
    "quest_done",
    "death",
    "story_finished",
}


class MetaHookError(Exception):
    """世界包的 meta_hooks 不合法。"""


@dataclass
class MetaHooks:
    world_id: str = ""
    currency: dict = field(default_factory=dict)
    earn: list[dict] = field(default_factory=list)
    spend: list[dict] = field(default_factory=list)
    carry: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    # ── 展示 ──────────────────────────────────────────────────
    @property
    def currency_key(self) -> str:
        return str(self.currency.get("key") or "point")

    @property
    def currency_label(self) -> str:
        return str(self.currency.get("label") or "点数")

    @property
    def earn_scale(self) -> float:
        try:
            return float(self.currency.get("earn_scale") or 1.0)
        except (TypeError, ValueError):
            return 1.0

    # ── earn ──────────────────────────────────────────────────
    def earn_for(self, event: str) -> int:
        """某事件在本世界值多少点（没配 = 0）。"""
        total = 0
        for item in self.earn:
            if str(item.get("event")) != str(event):
                continue
            try:
                total += int(item.get("amount") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def earn_rules(self) -> list[dict]:
        return [dict(x) for x in self.earn]

    # ── spend ─────────────────────────────────────────────────
    def spend_rules(self) -> list[dict]:
        return [dict(x) for x in self.spend]

    def carry_rules(self) -> list[dict]:
        return [dict(x) for x in self.carry]


def load_hooks(base: Path, world_id: str = "") -> MetaHooks:
    """读世界包的 meta_hooks.yaml（没有就返回空声明 = 这个世界不参与局外成长）。"""
    path = Path(base) / "meta_hooks.yaml"
    if not path.exists():
        return MetaHooks(world_id=world_id)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    meta = raw.get("meta") or {}
    hooks = MetaHooks(
        world_id=world_id,
        currency=dict(meta.get("currency") or {}),
        earn=list(meta.get("earn") or []),
        spend=list(meta.get("spend") or []),
        carry=list(meta.get("carry") or []),
        raw=raw,
    )
    validate(hooks)
    return hooks


def validate(hooks: MetaHooks) -> None:
    """结构校验（target 是否存在由 `check_contract` / `check_world` 负责）。"""
    if not hooks.currency_key:
        raise MetaHookError("meta_hooks: currency.key 不能为空")
    for item in hooks.earn:
        event = str(item.get("event") or "")
        if event not in EARN_EVENTS:
            raise MetaHookError(f"meta_hooks: 未知 earn 事件 {event}")
        if int(item.get("amount") or 0) <= 0:
            raise MetaHookError(f"meta_hooks: earn[{event}].amount 必须为正")
    for item in hooks.spend:
        kind = str(item.get("kind") or "")
        if kind not in SPEND_KINDS:
            raise MetaHookError(f"meta_hooks: 未知 spend kind {kind}")
        if not str(item.get("target") or ""):
            raise MetaHookError(f"meta_hooks: spend[{kind}] 缺 target")
        if int(item.get("cost") or 0) <= 0:
            raise MetaHookError(f"meta_hooks: spend[{kind}].cost 必须为正")
    for item in hooks.carry:
        kind = str(item.get("kind") or "")
        if kind not in CARRY_KINDS:
            raise MetaHookError(f"meta_hooks: 未知 carry kind {kind}")
        if not str(item.get("target") or ""):
            raise MetaHookError(f"meta_hooks: carry[{kind}] 缺 target")


def resolve_spend_targets(hooks: MetaHooks, pack, skills=None) -> list[dict]:
    """列出可买项，并标出 target 在本世界是否真实存在。

    `ok=False` 的项前端要置灰（配置写错或目标已不存在）。
    """
    out = []
    for item in hooks.spend_rules():
        kind = str(item.get("kind") or "")
        target = str(item.get("target") or "")
        ok = True
        why = ""
        if kind == "skill":
            # 技能表在 rules（不是世界包），由调用方传入
            ok = target in set(skills or [])
        elif kind == "talent":
            ok = any(str(t.get("id")) == target for t in getattr(pack, "talent_pool", []) or [])
        elif kind == "path":
            ok = target in set(getattr(pack, "path_ids", lambda: [])())
        elif kind == "item":
            ok = getattr(pack, "item_template", lambda _t: None)(target) is not None
        elif kind == "trait":
            ok = True  # 元成长是引擎级的标签，不属于任何世界
        else:
            ok = False
            why = f"未知 kind {kind}"
        if not ok and not why:
            why = f"本世界没有 {kind}:{target}"
        out.append(
            {
                "kind": kind,
                "target": target,
                "cost": int(item.get("cost") or 0),
                "max": int(item.get("max") or 0),
                "effect": str(item.get("effect") or ""),
                "note": str(item.get("note") or ""),
                "ok": ok,
                "why": why,
            }
        )
    return out


def resolve_carry_targets(hooks: MetaHooks, pack) -> list[dict]:
    """列出可带入本世界的元成长项（同样校验 target 存在）。"""
    out = []
    for item in hooks.carry_rules():
        kind = str(item.get("kind") or "")
        target = str(item.get("target") or "")
        ok = True
        if kind == "item":
            ok = getattr(pack, "item_template", lambda _t: None)(target) is not None
        out.append({"kind": kind, "target": target, "ok": ok})
    return out
