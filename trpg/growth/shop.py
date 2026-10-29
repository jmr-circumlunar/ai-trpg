"""局外消费：点数 → 角色卡上的东西。

原则（方案 04）：
- **先校验，后扣点**；应用失败就退款（点不能凭空消失）。
- 能买什么、多少钱，全看世界包的 `meta_hooks.yaml: spend`。
- 引擎不含世界知识：技能表由调用方传入，天赋/身份/装备由 pack 提供。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

from typing import Any

from .hooks import MetaHooks, resolve_spend_targets
from .wallet import Wallet


def bought_count(wallet: Wallet, hooks: MetaHooks, kind: str, target: str) -> int:
    """这一项在本世界已经买过几次（`max` 就是这个上限）。"""
    value = wallet.get(f"bought:{kind}:{target}", hooks.world_id)
    return int(value) if isinstance(value, (int, float)) else 0


def can_apply(card, pack, kind: str, target: str, rule: dict) -> tuple[bool, str]:
    """能不能把这一项落到卡上（不动数据）。上限由 bought_count 统一判定。"""
    if kind in ("skill", "talent", "trait", "item", "path"):
        return True, ""
    return False, f"暂不支持的 kind：{kind}"


def apply_effect(card, pack, kind: str, target: str, rule: dict) -> str:
    """把效果落到卡上（**唯一写卡的地方是 `trpg/meta/apply.py`**）。"""
    from . import apply as card_apply

    if kind == "skill":
        return card_apply.apply_skill(card, target, 1)
    if kind == "talent":
        return card_apply.apply_talent(card, pack, target)
    if kind == "trait":
        return card_apply.apply_trait(card, target, str(rule.get("effect") or "旧伤"), 1)
    if kind == "item":
        # 物品进局：只带 id（数值由局内掷定），不造实例
        return card_apply.apply_item(card, target, 1)
    if kind == "path":
        # 身份路径授予（路径 id 由世界包给）
        return card_apply.apply_path(card, pack, target)
    return ""


def purchase(
    hooks: MetaHooks,
    wallet: Wallet,
    pack,
    skills,
    card,
    kind: str,
    target: str,
    session_id: str = "",
) -> dict:
    """买一项。返回 `{ok, error?, cost?, applied?}`。"""
    rule = next(
        (
            r
            for r in hooks.spend_rules()
            if str(r.get("kind")) == str(kind) and str(r.get("target")) == str(target)
        ),
        None,
    )
    if rule is None:
        return {"ok": False, "error": "这个世界不提供这一项"}

    resolved = resolve_spend_targets(hooks, pack, skills)
    item = next(
        (x for x in resolved if x["kind"] == kind and x["target"] == target), None
    )
    if item is None or not item.get("ok"):
        return {"ok": False, "error": (item or {}).get("why") or "这一项在本世界无效"}

    max_times = int(rule.get("max") or 0)
    if max_times and bought_count(wallet, hooks, kind, target) >= max_times:
        return {"ok": False, "error": "这一项在本世界已达上限"}

    ok, why = can_apply(card, pack, kind, target, rule)
    if not ok:
        return {"ok": False, "error": why}

    cost = int(item["cost"])
    if not wallet.spend(
        hooks.currency_key,
        cost,
        world_id="",
        reason=f"购买 {kind}:{target}",
        session_id=session_id,
    ):
        return {"ok": False, "error": "点数不够"}

    applied = apply_effect(card, pack, kind, target, rule)
    if not applied:
        # 兜底退款：点不能凭空消失
        wallet.add(hooks.currency_key, cost, world_id="", reason="退款", session_id=session_id)
        return {"ok": False, "error": "这一项无法落到角色身上"}

    # 购买次数（本世界内计，用于 max 上限）
    wallet.add(
        f"bought:{kind}:{target}",
        1,
        world_id=hooks.world_id,
        reason=f"购买 {kind}:{target}",
        session_id=session_id,
    )
    # trait 计数是世界专有的（决定能不能带进下一个世界）
    if kind == "trait":
        key = f"trait:{target}"
        wallet.add(key, 1, world_id=hooks.world_id, reason=f"获得 {target}", session_id=session_id)

    return {"ok": True, "kind": kind, "target": target, "cost": cost, "applied": applied}


def listing(hooks: MetaHooks, pack, skills, wallet: Wallet) -> list[dict]:
    """可买项清单（前端用）：带够不够点、已达上限的置灰。"""
    balance = wallet.balance(hooks.currency_key)
    out = []
    for item in resolve_spend_targets(hooks, pack, skills):
        row = dict(item)
        row["balance"] = balance
        row["affordable"] = bool(item.get("ok")) and balance >= int(item.get("cost") or 0)
        out.append(row)
    return out
