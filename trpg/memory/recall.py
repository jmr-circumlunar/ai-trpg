"""混合召回：结构化事件 ∪ 向量检索 → RRF 融合。

为什么混合：
- 结构化（recent / important）保证「一定会想起重要的事」，这是底线
- 向量负责「字面不相关但情境相关」的联想
- 单靠任一方都不可靠：只结构化就永远是那几条；只向量则重要的可能被漏

召回日志是硬需求：要能判断「没召回到」还是「召回到了但模型没用」。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models.memory import CharacterEvent
from ..storage.memory import EventStore
from .contradiction import detect_conflicts
from .decay import combine, importance_score, recency_score, relevance_score
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
    # S5-2：矛盾标记与三因子明细（缺省为空 → 旧路径行为零变化）
    conflict: bool = False
    conflicts_with: list[str] = field(default_factory=list)
    factors: dict[str, float] = field(default_factory=dict)


def _fuse_key(item: RecallItem) -> str:
    """RRF 融合键：稳定 ref 优先，没有 ref 才退化为文本（B24）。

    同一件事在结构化路与向量路口径不同（前者带「第N回合」前缀，后者只有正文），
    用文本当键必然永不相等；`ref` 是两路共有的稳定标识（`turn{turn}` / 文档路径）。
    没有 ref 的历史数据不能全塌进一个空键，所以退化时把正文拼进去。
    """
    return item.ref or f"text:{item.text}"


@dataclass
class RecallResult:
    query: str
    items: list[RecallItem] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def texts(self, limit: int = 6) -> list[str]:
        """给 prompt 用的文本。

        **允许偏差**：向量命中是「模糊印象」，加标记让角色知道它未必准确；
        结构化命中（有回合/时间锚点）是「记得清楚的事」。角色可以按不准的记忆行动，
        但不能把它当铁证——这是设计目标，不是缺陷。
        """
        seen: set[str] = set()
        out: list[str] = []
        for item in self.items:
            if item.text in seen:
                continue
            seen.add(item.text)
            if item.conflict:
                # 矛盾显式化：两条都在，只是标出「这两条对不上」，不替角色决定信谁
                out.append(f"（与另一条记忆不一致）{item.text}")
            elif item.source == "vector":
                out.append(f"（隐约记得）{item.text}")
            else:
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
    min_score: float = 0.0,
    rel_margin: float = 0.0,
    *,
    scoring_mode: str = "rrf",
    score_weights: dict[str, float] | None = None,
    half_life_turns: float = 20.0,
    importance_scale: float = 10.0,
    structured_relevance: float = 0.5,
    conflict_enabled: bool = False,
    conflict_kinds: list[str] | None = None,
    conflict_min_diff_chars: int = 2,
    conflict_min_shared_prefix: int = 2,
    now_turn: int | None = None,
) -> RecallResult:
    """按角色召回：结构化候选 + 向量候选，RRF 融合。

    所有数量参数都来自 `config/memory.yaml`，便于调参对比。

    **S5-2 三因子模式**：`scoring_mode="three_factor"` 时改用
    「时衰 + 重要度 + 相关性」加权排序（算式见 `memory/decay.py`），
    并可开启**矛盾标记**（`memory/contradiction.py`：标冲突，不覆盖）。
    **缺省 `scoring_mode="rrf"` → 原 RRF 路径逐字节不变（行为零变化）。**
    """

    result = RecallResult(query=query)
    rank_lists: list[list[RecallItem]] = []
    structured_ids: set[str] = set()
    # 候选 + 元数据（S5-2 三因子与矛盾检测用；旧路径不读它）
    candidates: list[tuple[RecallItem, dict[str, Any]]] = []

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
                item = RecallItem(
                    source=label,
                    text=event.render(),
                    score=float(event.importance),
                    turn=event.turn,
                    ref=f"turn{event.turn}",
                )
                ranked.append(item)
                candidates.append(
                    (
                        item,
                        {
                            "kind": str(getattr(event, "kind", "") or ""),
                            "entities": list(getattr(event, "entities", []) or []),
                            "summary": str(getattr(event, "summary", "") or ""),
                            "importance": float(getattr(event, "importance", 0) or 0),
                            "turn": int(getattr(event, "turn", 0) or 0),
                            "ref": item.ref,
                        },
                    )
                )
            if ranked:
                rank_lists.append(ranked)

    # 2) 向量候选
    if vectors is not None and query:
        try:
            hits = vectors.search(
                [char_id, LORE_SCOPE], query, top_k=vector_k, min_score=min_score
            )
        except Exception as exc:  # noqa: BLE001
            # 以前这里静默吞掉，表现为「想不起来」而不是报错，极难发现
            result.debug["vector_error"] = f"{type(exc).__name__}: {exc}"[:160]
            hits = []
        if hits and rel_margin > 0:
            top = hits[0][1]
            hits = [(item, score) for item, score in hits if score >= top - rel_margin]
        if hits:
            vector_ranked: list[RecallItem] = []
            for item, score in hits:
                vector_item = RecallItem(
                    source="vector",
                    text=item.text,
                    score=score,
                    turn=item.turn,
                    ref=item.ref_id,
                )
                vector_ranked.append(vector_item)
                candidates.append(
                    (
                        vector_item,
                        {
                            "kind": "",
                            "entities": [],
                            "summary": str(item.text),
                            "importance": 0.0,
                            "turn": int(item.turn or 0),
                            "ref": item.ref_id,
                        },
                    )
                )
            rank_lists.append(vector_ranked)
        result.debug["vector_hits"] = [
            {"text": item.text, "score": round(score, 4)} for item, score in hits
        ]

    # 3) 三因子模式（S5-2）：时衰 + 重要度 + 相关性；可选矛盾标记
    if scoring_mode == "three_factor":
        return _three_factor_recall(
            result,
            candidates,
            limit=limit,
            weights=score_weights,
            half_life_turns=half_life_turns,
            importance_scale=importance_scale,
            structured_relevance=structured_relevance,
            min_score=min_score,
            conflict_enabled=conflict_enabled,
            conflict_kinds=conflict_kinds,
            conflict_min_diff_chars=conflict_min_diff_chars,
            conflict_min_shared_prefix=conflict_min_shared_prefix,
            now_turn=now_turn,
            char_id=char_id,
            rrf_k=rrf_k,
            structured_candidates=len(structured_ids),
            rank_lists=len(rank_lists),
        )

    # 4) RRF 融合（键 = 稳定 ref，见 `_fuse_key`）
    fused: dict[str, RecallItem] = {}
    for ranked in rank_lists:
        for rank, item in enumerate(ranked):
            key = _fuse_key(item)
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


def _three_factor_recall(
    result: RecallResult,
    candidates: list[tuple[RecallItem, dict[str, Any]]],
    *,
    limit: int,
    weights: dict[str, float] | None,
    half_life_turns: float,
    importance_scale: float,
    structured_relevance: float,
    min_score: float,
    conflict_enabled: bool,
    conflict_kinds: list[str] | None,
    conflict_min_diff_chars: int,
    conflict_min_shared_prefix: int,
    now_turn: int | None,
    char_id: str,
    rrf_k: int,
    structured_candidates: int,
    rank_lists: int,
) -> RecallResult:
    """三因子排序 + 矛盾标记（S5-2）。

    三因子：**时衰**（越近越高）+ **重要度**（写入时打的分）+ **相关性**
    （向量余弦；结构化候选按「主动挑出来的」给一个可配的兜底相关度）。
    同一 `ref` 多路命中时保留分数高者（结构化带重要度，通常胜出）。

    矛盾：**标记而非覆盖** —— 冲突的旧条目仍然在结果里，只是打上
    `conflict` 并记下与谁冲突；由角色自己判断信哪条。
    """
    if not candidates:
        result.debug.update(
            {
                "char_id": char_id,
                "scoring_mode": "three_factor",
                "structured_candidates": structured_candidates,
                "rank_lists": rank_lists,
                "fused": 0,
                "injected": 0,
                "sources": [],
                "conflicts": {},
            }
        )
        return result

    turns = [int(meta.get("turn") or 0) for _, meta in candidates if int(meta.get("turn") or 0) > 0]
    current = int(now_turn) if now_turn is not None else (max(turns) if turns else 0)

    scored: dict[str, RecallItem] = {}
    metas: dict[str, dict[str, Any]] = {}
    for item, meta in candidates:
        is_vector = item.source == "vector"
        relevance = (
            relevance_score(item.score, min_score) if is_vector else float(structured_relevance)
        )
        importance = (
            0.0 if is_vector else importance_score(meta.get("importance", 0.0), importance_scale)
        )
        turn = int(meta.get("turn") or 0)
        # 年龄未知（turn=0，如向量条目）不因「查不到时间」而受罚
        recency = 1.0 if turn <= 0 else recency_score(turn, current, half_life_turns)
        total = combine(relevance, importance, recency, weights)
        item.factors = {
            "relevance": round(relevance, 6),
            "importance": round(importance, 6),
            "recency": round(recency, 6),
            "total": round(total, 6),
        }
        key = _fuse_key(item)
        previous = scored.get(key)
        if previous is None or total > float(previous.factors.get("total", 0.0)):
            item.score = total
            scored[key] = item
            metas[key] = meta

    ordered = sorted(scored.values(), key=lambda i: (-i.score, i.turn))[:limit]

    conflicts: dict[str, list[str]] = {}
    if conflict_enabled:
        entries: list[dict[str, Any]] = []
        for item in ordered:
            meta = metas.get(_fuse_key(item)) or {}
            entries.append(
                {
                    "ref": item.ref,
                    "kind": str(meta.get("kind") or ""),
                    "entities": list(meta.get("entities") or []),
                    "summary": str(meta.get("summary") or ""),
                    "importance": float(meta.get("importance") or 0.0),
                    "turn": int(meta.get("turn") or 0),
                }
            )
        conflicts = detect_conflicts(
            entries,
            kinds=conflict_kinds,
            min_diff_chars=conflict_min_diff_chars,
            min_shared_prefix=conflict_min_shared_prefix,
        )
        for item in ordered:
            others = conflicts.get(item.ref) or []
            if others:
                item.conflict = True
                item.conflicts_with = list(others)

    result.items = ordered
    result.debug.update(
        {
            "char_id": char_id,
            "rrf_k": rrf_k,
            "scoring_mode": "three_factor",
            "structured_candidates": structured_candidates,
            "rank_lists": rank_lists,
            "fused": len(scored),
            "injected": len(ordered),
            "sources": sorted({i.source for i in ordered}),
            "conflicts": {k: list(v) for k, v in conflicts.items()},
        }
    )
    return result
