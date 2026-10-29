"""局外发点：局内事件 → 点数。

事件由引擎在固定节点发出（引擎侧只发事件名，**不发金额**）：
金额一律查世界包的 `meta_hooks.yaml: earn`。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

from .hooks import MetaHooks
from .wallet import Wallet


def apply_events(
    hooks: MetaHooks,
    wallet: Wallet,
    events: list[str],
    session_id: str = "",
    world_id: str = "",
) -> list[dict]:
    """按事件发点，返回明细 [{event, amount, currency}]。

    点数进**全局池**（world_id=""）：点数跨世界累计，但每个世界独立定价。
    """
    out: list[dict] = []
    if not hooks.earn:
        return out
    key = hooks.currency_key
    for event in events or []:
        amount = hooks.earn_for(event)
        if amount <= 0:
            continue
        scaled = int(round(amount * hooks.earn_scale))
        if scaled <= 0:
            continue
        wallet.add(
            key,
            scaled,
            world_id="",
            reason=f"{event}（{hooks.currency_label}）",
            session_id=session_id,
        )
        out.append({"event": event, "amount": scaled, "currency": hooks.currency_label})
    return out
