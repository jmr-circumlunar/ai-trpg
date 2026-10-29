"""三因子打分的算子：时衰 + 重要度 + 相关性。

**为什么是这三个**（联网核对 2026-10-28）：Generative Agents（Park et al., UIST 2023）
的经典形式——三个**归一化**分数加权求和，recency 用指数衰减；社区工程实现
（如 Belgavi 2026）常用 `w_rel=0.6 / w_imp=0.2 / w_rec=0.2`，并按记忆类型给不同半衰期。

**只抄算式与形态**，不抄任何世界语义；权重与半衰期全在 `config/memory.yaml`，
代码里不写数值。缺省仍走原 RRF 路径（行为零变化），本模块只在开启三因子模式时被调用。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from typing import Any


def recency_score(turn: int, now_turn: int, half_life_turns: float) -> float:
    """时衰：距今越久分越低（指数衰减，半衰期可配）。

    `half_life_turns <= 0` 表示「不衰减」——稳定事实不因久远而褪色。
    """
    if half_life_turns <= 0:
        return 1.0
    delta = max(0, int(now_turn) - int(turn))
    return float(0.5 ** (delta / float(half_life_turns)))


def importance_score(importance: float, scale: float = 10.0) -> float:
    """重要度：写入时打的分（1~10），线性归一化到 [0,1]。"""
    if scale <= 0:
        return 0.0
    return max(0.0, min(1.0, float(importance) / float(scale)))


def relevance_score(vector_score: float, min_score: float = 0.0) -> float:
    """相关性：向量余弦分归一化到 [0,1]。

    `min_score` 是召回阈值（低于它的候选本就进不来）；线性拉伸保证 0 分→0 分、
    满分→1 分，便于与另两个因子同量纲相加。
    """
    low = max(0.0, float(min_score))
    value = (float(vector_score) - low) / max(1e-9, 1.0 - low)
    return max(0.0, min(1.0, value))


def combine(
    relevance: float,
    importance: float,
    recency: float,
    weights: dict[str, Any] | None = None,
) -> float:
    """三因子加权求和（权重缺省三者相等；归一化到 [0,1]）。"""
    w = dict(weights or {})
    w_rel = float(w.get("relevance", 1.0))
    w_imp = float(w.get("importance", 1.0))
    w_rec = float(w.get("recency", 1.0))
    total = w_rel + w_imp + w_rec
    if total <= 0:
        return 0.0
    value = (
        w_rel * float(relevance)
        + w_imp * float(importance)
        + w_rec * float(recency)
    ) / total
    return max(0.0, min(1.0, value))


__all__ = ["combine", "importance_score", "recency_score", "relevance_score"]
