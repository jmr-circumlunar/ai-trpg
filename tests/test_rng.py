"""种子派生：格式必须与替换前**逐字节等价**。

替换 12 处 `random.Random(f"...")` 时，唯一的验收标准就是「同 seed 同结果」。
这里用「派生出同样的数列」来断言，而不是断言字符串——断言字符串会鼓励实现细节耦合，
断言数列才是真正要保住的可复现性。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.models.rng import derive, derive_parts  # noqa: E402


def _series(rng: random.Random, count: int = 5) -> list[float]:
    return [rng.random() for _ in range(count)]


def test_derive_matches_legacy_fstring():
    """`derive(seed, a, b)` 必须等价于 `random.Random(f"{seed}:{a}:{b}")`。"""
    assert _series(derive(42, "chr_1", 3)) == _series(
        random.Random("42:chr_1:3")
    )


def test_derive_single_part():
    assert _series(derive("seed", "vote")) == _series(random.Random("seed:vote"))


def test_derive_without_parts_uses_string_seed():
    """没有 parts 时按字符串派生——`Random(42)` 与 `Random("42")` 是不同数列。"""
    assert _series(derive(42)) == _series(random.Random("42"))
    assert _series(derive(42)) != _series(random.Random(42))


def test_derive_parts_keeps_original_order():
    """历史格式（seed 在最后）只能靠 `derive_parts` 保住原串。"""
    assert _series(derive_parts("w", "region", 42)) == _series(
        random.Random("w:region:42")
    )


def test_derive_is_independent_per_key():
    """不同 key 派生出的数列必须不同，否则「每角色每回合独立」就是假的。"""
    assert _series(derive(42, "chr_1", 1)) != _series(derive(42, "chr_2", 1))
    assert _series(derive(42, "chr_1", 1)) != _series(derive(42, "chr_1", 2))


def test_derive_is_stable_across_calls():
    """同 key 反复派生必须得到同一数列（重放的基础）。"""
    assert _series(derive(7, "loot", "chr_3", 2, 0)) == _series(
        derive(7, "loot", "chr_3", 2, 0)
    )
