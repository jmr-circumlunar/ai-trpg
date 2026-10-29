"""局外带入（carry）：把允许携带的元成长注入角色卡。

规则（方案 04）：
- **只带 id，不带数值实例**（装备实例仍是 session 级）。
- 能带什么由世界包 `meta_hooks.yaml: carry` 决定（世界说不行就不行）。
- 换世界时，**世界专属部分不携带**（血统/身份/天赋按新世界重建）——
  这里只注入「元成长」类（痕迹 / 遗物）。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

from .hooks import MetaHooks, resolve_carry_targets
from .wallet import Wallet


def apply_carry(hooks: MetaHooks, wallet: Wallet, card, pack) -> list[dict]:
    """把该带的元成长注入卡，返回注入明细。"""
    applied: list[dict] = []
    for rule in resolve_carry_targets(hooks, pack):
        if not rule.get("ok"):
            continue
        kind = str(rule.get("kind"))
        target = str(rule.get("target"))
        if kind not in ("trait", "item", "path"):
            continue
        count = wallet.get(f"{kind}:{target}", hooks.world_id)
        if not isinstance(count, (int, float)) or int(count) <= 0:
            continue
        n = int(count)
        # 已有同 kind 的痕迹就只补差额（避免每次开局重复注入）
        from . import apply as card_apply

        if kind == "trait":
            have = card_apply.count_traits(card, target)
            missing = max(0, n - have)
            if missing:
                card_apply.apply_trait(card, target, "旧伤", missing)
        elif kind == "item":
            # 物品只带 id（不带数值实例）
            have = card_apply.count_carried(card, target)
            missing = max(0, n - have)
            if missing:
                card_apply.apply_item(card, target, missing)
        else:  # path：身份路径授予（一次就够）
            missing = 0 if getattr(card.ext, "path", "") == target else 1
            if missing:
                card_apply.apply_path(card, pack, target)
        applied.append({"kind": kind, "target": target, "count": missing})
    return applied
