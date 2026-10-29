"""局外成长（meta）：跨世界通用的钱包 + 世界包声明的价目表。

分工：
- `wallet.py`：点数与元成长的存取（不含任何世界知识）
- `hooks.py`：读世界包的 `meta_hooks.yaml`（价目表在世界里）
- `earn.py` / `shop.py` / `carry.py`：事件发点、购买、带入

见知识库 `过程记录/方案/04-三期-局外成长插件化.md`。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from .hooks import MetaHookError, MetaHooks, load_hooks, resolve_carry_targets, resolve_spend_targets
from .wallet import Wallet

__all__ = [
    "MetaHookError",
    "MetaHooks",
    "Wallet",
    "load_hooks",
    "resolve_carry_targets",
    "resolve_spend_targets",
]
