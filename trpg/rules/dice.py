"""可复现骰子。

随机源统一走 `random.Random(seed)`，并记录 `seed:counter` 作为 rng_state，
使同一 seed 的骰子序列可以逐次重放（debug / 回归测试的前提）。

S1 可替换为 `avrae/d20` 做表达式解析；S0 自实现以保证随机源完全可控。
"""

from __future__ import annotations

import random
import re

_DICE_RE = re.compile(r"^\s*(?P<n>\d*)d(?P<sides>\d+)\s*(?P<mod>[+-]\s*\d+)?\s*$", re.IGNORECASE)


class DiceError(ValueError):
    pass


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
