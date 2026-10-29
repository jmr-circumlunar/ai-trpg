"""可复现骰子。

随机源统一走 `random.Random(seed)`，并记录 `seed:counter` 作为 rng_state，
使同一 seed 的骰子序列可以逐次重放（debug / 回归测试的前提）。

S1 可替换为 `avrae/d20` 做表达式解析；S0 自实现以保证随机源完全可控。

**骰型注册表（S1-2）**：新增 `roll_type()` 按名字取注册表里的骰型（池运算 /
成功计数 / 爆炸等）。**旧表达式路径（`roll()`）一行未改** —— 缺省行为逐字节不变，
注册表只作为「可选的新能力」并存。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

import random
import re

from .dice_types import DiceError, DiceResult, get_dice_type

_DICE_RE = re.compile(r"^\s*(?P<n>\d*)d(?P<sides>\d+)\s*(?P<mod>[+-]\s*\d+)?\s*$", re.IGNORECASE)


class DiceEngine:
    def __init__(self, seed: int | str) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        self._count = 0

    # ── 基础 ────────────────────────────────────────────────
    def d(self, sides: int) -> int:
        if sides < 2:
            raise DiceError(f"骰子面数必须 >= 2，收到 {sides}")
        self._count += 1
        return self._rng.randint(1, sides)

    @property
    def rng_state(self) -> str:
        """轻量状态标识：seed + 已掷次数（足以定位序列位置）。"""
        return f"{self.seed}:{self._count}"

    # ── 表达式 ──────────────────────────────────────────────
    def roll(self, expr: str) -> tuple[list[int], int]:
        """支持 `1d20` / `2d6` / `1d20+2` / `2d6-1`。返回 (各骰面, 含修正总值)。"""
        m = _DICE_RE.match(expr)
        if not m:
            raise DiceError(f"无法解析骰子表达式：{expr}")

        count = int(m.group("n")) if m.group("n") else 1
        sides = int(m.group("sides"))
        mod_raw = m.group("mod")
        mod = int(mod_raw.replace(" ", "")) if mod_raw else 0

        if count < 1 or count > 20:
            raise DiceError(f"骰子个数越界（1~20）：{count}")

        rolls = [self.d(sides) for _ in range(count)]
        return rolls, sum(rolls) + mod

    # ── 骰型注册表（S1-2）────────────────────────────────────
    def roll_type(self, name: str, **overrides: object) -> DiceResult:
        """按**名字**取注册表里的骰型并掷一次。

        名字与参数来自 `rules/dice.yaml`（数值真相源），机制在 `dice_types.py`；
        `overrides` 可临时覆盖参数（例如换颗数 / 换阈值），不改 yaml。

        与 `roll()` 的关系：**并存**，不是替代 —— 老表达式路径行为不变。
        """
        dice_type = get_dice_type(name)
        return dice_type.roll(self, {k: v for k, v in overrides.items() if v is not None})

    # ── 概率分析（S1-3）─────────────────────────────────────
    def analyze(self, expr: str) -> dict:
        """`NdS±M` 的**完整分布**（精确枚举，不是抽样）。

        为什么用枚举而不是蒙特卡洛：判定只有一两种骰型（`1d20` / `2d6`），
        精确解既快又可复现；蒙特卡洛会引入采样误差，玩家看到的成功率就不确定了。
        枚举量级 = 颗数 × 面数（卷积后可达总和只有 `count*sides+1` 种），不随骰数指数增长。

        返回：每个总值的概率、期望、标准差、最小/最大值。
        **必须与实际掷骰一致**（`tests/test_dice_analysis.py` 做抽样交叉验证）。
        """
        count, sides, modifier = self._parse_expr(expr)
        # 枚举规模护栏：状态数 = count*sides+1，卷积代价 ≈ count*sides²
        if count * sides * sides > 4_000_000:
            raise DiceError(f"表达式规模过大，无法精确枚举：{expr}")

        dist: dict[int, int] = {0: 1}
        for _ in range(count):
            nxt: dict[int, int] = {}
            for total, ways in dist.items():
                for face in range(1, sides + 1):
                    nxt[total + face] = nxt.get(total + face, 0) + ways
            dist = nxt
        total_ways = sides ** count
        distribution = {
            total + modifier: ways / total_ways for total, ways in sorted(dist.items())
        }
        mean = sum(value * prob for value, prob in distribution.items())
        variance = sum((value - mean) ** 2 * prob for value, prob in distribution.items())
        return {
            "expr": expr,
            "count": count,
            "sides": sides,
            "modifier": modifier,
            "distribution": distribution,
            "min": min(distribution),
            "max": max(distribution),
            "mean": mean,
            "stddev": variance ** 0.5,
        }

    def _parse_expr(self, expr: str) -> tuple[int, int, int]:
        """解析 `NdS±M`（与 `roll()` 同一套正则与同样的越界规则）。"""
        m = _DICE_RE.match(expr)
        if not m:
            raise DiceError(f"无法解析骰子表达式：{expr}")
        count = int(m.group("n")) if m.group("n") else 1
        sides = int(m.group("sides"))
        mod_raw = m.group("mod")
        modifier = int(mod_raw.replace(" ", "")) if mod_raw else 0
        if count < 1 or count > 20:
            raise DiceError(f"骰子个数越界（1~20）：{count}")
        return count, sides, modifier
