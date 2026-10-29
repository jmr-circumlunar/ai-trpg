"""可复现随机的**唯一派生入口**。

## 为什么需要它

全仓有 12 处 `random.Random(f"{seed}:...")`，各写各的格式，其中地图生成还是
`{world}:{region}:{seed}`（**seed 在后**）。格式散落的风险是：

> 改任意一处都会让「同一个 seed 重放同一局」失效，而这种失效**不报错**——
> 结果只是悄悄变了，golden 与复盘全部对不上。

## 两条规则

1. **新代码一律用 `derive(seed, *parts)`** → 格式固定为 `seed:part1:part2`；
2. **历史格式用 `derive_parts(*parts)`** 显式保留原本顺序（`worldgen` 就是 seed 在后）。

`tests/test_rng.py` 把这两条格式钉死。统一本身不产生新行为，它买的是
「以后要改格式时只改一处」——所以测试比代码更重要。

doc: 03-模块/M01-数据模型/00-模块总述.md
module: M01
layer: L0
"""

from __future__ import annotations

import random
from typing import Any


def derive(seed: Any, *parts: Any) -> random.Random:
    """按 `seed:part1:part2:...` 派生一个独立 RNG。

    与 `random.Random(f"{seed}:{p1}:{p2}")` **逐字节等价**——这是替换 12 处调用点时
    唯一的硬约束。没有 `parts` 时按 `str(seed)` 派生（等价于 `f"{seed}"`，
    注意**不是** `random.Random(seed)`：int 与 str 的种子在 Python 里不等价）。
    """
    if not parts:
        return random.Random(str(seed))
    return random.Random(":".join([str(seed), *(str(part) for part in parts)]))


def derive_parts(*parts: Any) -> random.Random:
    """按给定顺序直接拼接派生（给「seed 不在最前」的历史格式用）。

    只应该出现在**保持兼容**的场景；新代码用 `derive()`。
    """
    return random.Random(":".join(str(part) for part in parts))


__all__ = ["derive", "derive_parts"]
