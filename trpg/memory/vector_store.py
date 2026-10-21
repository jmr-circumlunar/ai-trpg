"""向量存储与暴力检索。

为什么不用 sqlite-vec：Windows 官方 Python 的 sqlite3 常不带 load_extension，
装扩展反而增加不可控因素。个人单机规模（几千条 × 1536 维）暴力余弦完全够：
1000 条约 1.5M 次乘法，纯 Python 也就几十毫秒。
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field

from .embedder import Embedder


@dataclass
class MemoryVector:
    char_id: str
    text: str
    vector: list[float]
    turn: int = 0
    kind: str = "event"
    ref_id: str = ""
    meta: dict = field(default_factory=dict)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class VectorStore:
    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        session_id: str = "",
        embedder: Embedder | None = None,
    ) -> None:
        self.conn = conn
        self.session_id = session_id
        self.embedder = embedder
        self._items: dict[str, list[MemoryVector]] = {}

    # ── 写入 ────────────────────────────────────────────────
    def add_many(
        self,
        char_id: str,
        texts: list[str],
        turn: int = 0,
        kind: str = "event",
        ref_ids: list[str] | None = None,
    ) -> int:
        if not texts or self.embedder is None:
            return 0
        vectors = self.embedder.embed(texts, kind="db")
        bucket = self._items.setdefault(char_id, [])
        for i, (text, vec) in enumerate(zip(texts, vectors)):
            item = MemoryVector(
                char_id=char_id,
                text=text,
                vector=vec,
                turn=turn,
                kind=kind,
                ref_id=(ref_ids[i] if ref_ids else ""),
            )
            bucket.append(item)
            if self.conn is not None:
                self.conn.execute(
                    "INSERT INTO memory_vectors "
                    "(session_id, char_id, turn, kind, ref_id, text, embedding) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        self.session_id,
                        char_id,
                        turn,
                        kind,
                        item.ref_id,
                        text,
                        json.dumps(vec),
                    ),
                )
        return len(texts)

    # ── 检索 ────────────────────────────────────────────────
    def search(
        self, char_id: str | list[str], query: str, top_k: int = 5, min_score: float = 0.0
    ) -> list[tuple[MemoryVector, float]]:
        """支持多桶（角色记忆 + 世界知识），统一打分后取 top-k。"""
        keys = list(char_id) if isinstance(char_id, (list, tuple)) else [char_id]
        bucket: list[MemoryVector] = []
        for key in keys:
            bucket.extend(self._items.get(key) or [])
        if not bucket or not query or self.embedder is None:
            return []

        qvec = self.embedder.embed([query], kind="query")[0]
        scored = [(item, cosine(qvec, item.vector)) for item in bucket]
        scored = [pair for pair in scored if pair[1] >= min_score]
        scored.sort(key=lambda p: -p[1])
        return scored[:top_k]

    def count(self, char_id: str | None = None) -> int:
        if char_id is None:
            return sum(len(v) for v in self._items.values())
        return len(self._items.get(char_id) or [])

    def all_for(self, char_id: str) -> list[MemoryVector]:
        return list(self._items.get(char_id) or [])
