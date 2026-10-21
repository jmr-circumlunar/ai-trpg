"""混合召回：结构化事件 ∪ 向量检索 → RRF 融合。

为什么混合：
- 结构化（recent / important）保证「一定会想起重要的事」，这是底线
- 向量负责「字面不相关但情境相关」的联想
- 单靠任一方都不可靠：只结构化就永远是那几条；只向量则重要的可能被漏

召回日志是硬需求：要能判断「没召回到」还是「召回到了但模型没用」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models.memory import CharacterEvent
from ..storage.memory import EventStore
from .vector_store import VectorStore

RRF_K = 60

# 世界知识（archive）放在公共桶，检索时与角色记忆合并
LORE_SCOPE = "_lore"


@dataclass
class RecallItem:
    source: str  # "recent" / "important" / "vector"
    text: str
    score: float
    turn: int = 0
    ref: str = ""


@dataclass
class RecallResult:
    query: str
    items: list[RecallItem] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def texts(self, limit: int = 6) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in self.items:
            if item.text in seen:
                continue
            seen.add(item.text)
            out.append(item.text)
            if len(out) >= limit:
                break
        return out

    def render(self) -> str:
        """给 trace / 前端看的一句话。"""
        if not self.items:
            return "（本回合没有回忆起任何事）"
        parts = [f"{i + 1}. [{it.source}:{it.score:.3f}] {it.text}" for i, it in enumerate(self.items[:6])]
        return "\n".join(parts)


def hybrid_recall(
    query: str,
    char_id: str,
    events: EventStore | None,
    vectors: VectorStore | None,
    limit: int = 6,
    vector_k: int = 4,
    rrf_k: int = RRF_K,
    important_limit: int = 3,
    important_threshold: int = 7,
    recent_limit: int = 5,
) -> RecallResult:
    """按角色召回：结构化候选 + 向量候选，RRF 融合。

    所有数量参数都来自 `config/memory.yaml`，便于调参对比。
    """

    result = RecallResult(query=query)
    rank_lists: list[list[RecallItem]] = []
    structured_ids: set[str] = set()

    # 1) 结构化候选（recent + important），各自是一个排序列表
    if events is not None:
        try:
            important = events.important(
                char_id, limit=important_limit, threshold=important_threshold
            )
            recent = events.recent(char_id, limit=recent_limit)
        except Exception:  # noqa: BLE001
            important, recent = [], []

        for label, items in (("important", important), ("recent", recent)):
            ranked: list[RecallItem] = []
            for event in items:  # type: ignore[union-attr]
                structured_ids.add(event.summary)
                ranked.append(
                    RecallItem(
                        source=label,
                        text=event.render(),
                        score=float(event.importance),
                        turn=event.turn,
                        ref=f"turn{event.turn}",
                    )
                )
            if ranked:
                rank_lists.append(ranked)

    # 2) 向量候选
    if vectors is not None and query:
        try:
            hits = vectors.search([char_id, LORE_SCOPE], query, top_k=vector_k)
        except Exception:  # noqa: BLE001
            hits = []
        if hits:
            rank_lists.append(
                [
                    RecallItem(
                        source="vector",
                        text=item.text,
                        score=score,
                        turn=item.turn,
                        ref=item.ref_id,
                    )
                    for item, score in hits
                ]
            )
        result.debug["vector_hits"] = [
            {"text": item.text, "score": round(score, 4)} for item, score in hits
        ]

    # 3) RRF 融合
    fused: dict[str, RecallItem] = {}
    for ranked in rank_lists:
        for rank, item in enumerate(ranked):
            key = item.text
            contribution = 1.0 / (rrf_k + rank + 1)
            if key in fused:
                fused[key].score += contribution
            else:
                fused[key] = RecallItem(
                    source=item.source,
                    text=item.text,
                    score=contribution,
                    turn=item.turn,
                    ref=item.ref,
                )

    ordered = sorted(fused.values(), key=lambda i: -i.score)[:limit]
    result.items = ordered
    result.debug.update(
        {
            "char_id": char_id,
            "rrf_k": rrf_k,
            "structured_candidates": len(structured_ids),
            "rank_lists": len(rank_lists),
            "fused": len(fused),
            "injected": len(ordered),
            "sources": sorted({i.source for i in ordered}),
        }
    )
    return result
