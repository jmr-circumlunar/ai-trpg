"""M16 · 校准与评测指标（**纯函数**：无 DB、无网络、无随机副作用）。

抄的是 deepwolf `copilot/calibration.py` 的方法（C-5）：

- **Brier score**：概率的均方误差，0 = 完美；
- **Brier skill score**：相对「永远猜基准率」的技巧分，1 完美 / 0 无技巧 / 负数更差；
- **Murphy 分解**：`brier ≈ reliability − resolution + uncertainty`；
- **可靠性表**：按预测概率分箱，列出「预测概率 vs 实际发生率 + gap」。

另外是阶段一出口判据的**机器近似**（B-3）：
首日存活率、投票命中率、投票分布熵，以及**阵营视角**的三条——
敌对阵营伪装成功率、己方阵营指认命中率、己方阵营把敌对成员投票淘汰的对局占比
（口径见知识库 `过程记录/方案/42-出口判据代理指标-W2`）。

**零世界词**：这里只处理「概率 + 真值」的二元数据，阵营含义由调用方给（框架不解释）。

doc: 03-模块/M16-可观测与评测/00-模块总述.md
module: M16
layer: L7
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass
class Pair:
    """一条校准样本：`p` = 预测概率（0..1），`y` = 实际是否成立（0/1）。"""

    p: float
    y: int
    group: str = ""


def _pairs(samples: Iterable[Pair | tuple[float, int]]) -> list[Pair]:
    out: list[Pair] = []
    for item in samples or ():
        if isinstance(item, Pair):
            out.append(item)
        else:
            p, y = item
            out.append(Pair(p=float(p), y=int(y)))
    return out


def brier_score(samples: Iterable[Pair | tuple[float, int]]) -> float:
    """Brier score：`(1/N) · Σ (p − y)²`，0 = 完美。"""
    items = _pairs(samples)
    if not items:
        return 0.0
    return sum((item.p - item.y) ** 2 for item in items) / len(items)


def base_rate(samples: Iterable[Pair | tuple[float, int]]) -> float:
    """基准率：实际成立的比例（`always base rate` 的参照）。"""
    items = _pairs(samples)
    if not items:
        return 0.0
    return sum(item.y for item in items) / len(items)


def brier_skill_score(
    samples: Iterable[Pair | tuple[float, int]], baseline: float | None = None
) -> float:
    """Brier skill score：`1 − brier / brier_ref`，1 完美 / 0 无技巧 / 负数更差。

    `baseline` 不给 = 用**基准率**（永远猜平均）作参照。
    """
    items = _pairs(samples)
    if not items:
        return 0.0
    base = base_rate(items) if baseline is None else float(baseline)
    reference = base_rate(items) - (base - base) ** 2
    # 参考值：始终预测 base 时的 brier
    ref = sum((base - item.y) ** 2 for item in items) / len(items)
    if ref <= 0:
        return 0.0
    return 1.0 - (brier_score(items) / ref)


def murphy_decomposition(
    samples: Iterable[Pair | tuple[float, int]], bins: int = 10
) -> dict[str, float]:
    """Murphy 分解：`brier ≈ reliability − resolution + uncertainty`。

    - `reliability`：校准误差（越小越好）；
    - `resolution`：区分度（越大越好）；
    - `uncertainty`：基准率方差（超越「只猜基准率」能拿到的部分）。
    """
    items = _pairs(samples)
    if not items:
        return {
            "brier": 0.0,
            "reliability": 0.0,
            "resolution": 0.0,
            "uncertainty": 0.0,
        }
    base = base_rate(items)
    uncertainty = base * (1.0 - base)
    groups: dict[int, list[Pair]] = {}
    for item in items:
        idx = min(bins - 1, max(0, int(item.p * bins)))
        groups.setdefault(idx, []).append(item)

    reliability = 0.0
    resolution = 0.0
    total = len(items)
    for group in groups.values():
        n = len(group)
        mean_p = sum(item.p for item in group) / n
        obs = sum(item.y for item in group) / n
        reliability += (n / total) * (mean_p - obs) ** 2
        resolution += (n / total) * (obs - base) ** 2

    brier = brier_score(items)
    return {
        "brier": brier,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
    }


def reliability_table(
    samples: Iterable[Pair | tuple[float, int]], bins: int = 10
) -> list[dict]:
    """可靠性表：每 10% 一箱的「预测概率 vs 实际发生率 + gap」。"""
    items = _pairs(samples)
    groups: dict[int, list[Pair]] = {}
    for item in items:
        idx = min(bins - 1, max(0, int(item.p * bins)))
        groups.setdefault(idx, []).append(item)
    rows: list[dict] = []
    for idx in sorted(groups):
        group = groups[idx]
        n = len(group)
        mean_p = sum(item.p for item in group) / n
        obs = sum(item.y for item in group) / n
        rows.append(
            {
                "bin": idx,
                "count": n,
                "predicted": mean_p,
                "observed": obs,
                "gap": mean_p - obs,
            }
        )
    return rows


# ── 阶段一出口判据的机器近似（B-3）─────────────────────────


@dataclass
class ExitProxies:
    """阶段一出口判据的机器近似（B-3）：6 条。

    | 指标 | 层级 | 含义 |
    |---|---|---|
    | `first_day_survival` | 对局 | 首日之后还活着的比例（不分阵营） |
    | `vote_hit_rate` | 投票 | 票投给「后来出局的人」的比例（不分阵营） |
    | `vote_entropy` | 投票 | 投票分布熵（越低越集中） |
    | `deception_success` | 投票 | **敌对阵营的伪装成功率**：己方阵营的票里投给己方目标的比例 |
    | `ally_effective_identification` | 投票 | **己方阵营的指认命中率**：己方阵营的票里投给敌对目标的比例 |
    | `ally_collective_success` | 对局 | 己方阵营**至少一次把敌对成员投票淘汰**的对局占比 |

    后三条是**阵营视角**的（需要调用方给敌对阵营词表），前三条是不分阵营的。
    `deception_success` 与 `ally_effective_identification` 互补（和为 1）：
    同一批票从双方视角各看一次——**没有随机基线对照时这两个数没有意义**。
    """

    first_day_survival: float = 0.0
    vote_hit_rate: float = 0.0
    vote_entropy: float = 0.0
    deception_success: float = 0.0
    ally_effective_identification: float = 0.0
    ally_collective_success: float = 0.0
    extras: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "first_day_survival": self.first_day_survival,
            "vote_hit_rate": self.vote_hit_rate,
            "vote_entropy": self.vote_entropy,
            "deception_success": self.deception_success,
            "ally_effective_identification": self.ally_effective_identification,
            "ally_collective_success": self.ally_collective_success,
            **self.extras,
        }


def vote_distribution_entropy(votes: Sequence[str]) -> float:
    """投票分布熵（归一化到 0..1）：越集中越低，说明「跟风/单点」。

    空投票 → 0（没有信息）。
    """
    items = [str(v) for v in votes if str(v or "").strip()]
    if not items:
        return 0.0
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    n = len(items)
    entropy = 0.0
    for count in counts.values():
        p = count / n
        entropy -= p * math.log(p)
    # 归一化：除以 log(候选数)，单点 = 0
    if len(counts) <= 1:
        return 0.0
    return entropy / math.log(len(counts))


__all__ = [
    "ExitProxies",
    "Pair",
    "base_rate",
    "brier_score",
    "brier_skill_score",
    "murphy_decomposition",
    "reliability_table",
    "vote_distribution_entropy",
]
