"""向量存储：SQLite 持久化 + numpy 矩阵检索。

为什么不用 sqlite-vec：Windows 官方 Python 的 sqlite3 常不带 `load_extension`，
装扩展反而增加不可控因素。为什么用 numpy 而不是纯 Python：
- 纯 Python 的 `list[float]` 每条 1536 维约占 49KB（float 对象开销），1 万条 ≈ 500MB，
  直接撞 `MemoryMax=800M`；numpy float32 是 6KB/条，1 万条 ≈ 60MB。
- 检索从「逐条 for 循环」变成一次矩阵乘，万条 < 5ms。
- **不再自研检索算法**：距离计算交给 numpy，我们只管桶、阈值、回灌与清理。

存储格式：`memory_vectors.embedding` 存 **float32 原始字节**（`np.float32.tobytes()`）。
旧库里的 JSON TEXT 依然能读（`_unpack` 兼容），用 `tools/migrate_vectors.py` 可批量转。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field

import numpy as np

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
    """纯 Python 余弦（保留给测试与调试；检索走 numpy）。"""
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


def _pack(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _unpack(raw) -> list[float]:
    """兼容三种历史格式：float32 字节 / JSON 字符串 / 已经是 list。"""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(raw), dtype=np.float32).tolist()
    if isinstance(raw, str):
        try:
            return [float(x) for x in json.loads(raw)]
        except (ValueError, TypeError):
            return []
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    return []


class VectorStore:
    """默认记忆后端：SQLite 存盘、内存里放 numpy 矩阵。"""

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
        # 桶 -> (构建时的条数, 归一化后的矩阵)；条数变了就重建
        self._cache: dict[str, tuple[int, np.ndarray]] = {}

    # ── 写入 ────────────────────────────────────────────────
    def _check_dim(self, char_id: str, vectors: list[list[float]]) -> None:
        """维度一致性：同一桶里混入不同维度会让检索静默全 0 分。"""
        bucket = self._items.get(char_id) or []
        if not bucket or not vectors:
            return
        old, new = len(bucket[0].vector), len(vectors[0])
        if old != new:
            raise ValueError(
                f"向量维度不一致（桶 {char_id} 已有 {old} 维，新写入 {new} 维）："
                "换 embedding 源后不能混用同一局的历史记忆"
            )

    def add_grouped(
        self,
        groups: dict[str, list[str]],
        turn: int = 0,
        kind: str = "event",
    ) -> int:
        """**一次** embedding 调用写入多个桶（回合末批量）。

        以前每角色每回合单独一次 HTTP（约 405ms）串在回合热路径上；
        16 条批量实测 0.66s，等价于把 N 次往返压成 1 次。
        """
        if not groups or self.embedder is None:
            return 0
        keys = [k for k, v in groups.items() if v]
        texts = [t for k in keys for t in groups[k]]
        if not texts:
            return 0
        vectors = self.embedder.embed(texts, kind="db")
        if len(vectors) != len(texts):
            raise ValueError(f"embedding 返回数量不匹配：{len(vectors)} != {len(texts)}")

        total, i = 0, 0
        for key in keys:
            bucket = self._items.setdefault(key, [])
            for text in groups[key]:
                vec = list(vectors[i])
                i += 1
                self._check_dim(key, [vec])
                item = MemoryVector(
                    char_id=key,
                    text=text,
                    vector=vec,
                    turn=turn,
                    kind=kind,
                    ref_id=f"turn{turn}",
                )
                bucket.append(item)
                if self.conn is not None:
                    self.conn.execute(
                        "INSERT INTO memory_vectors "
                        "(session_id, char_id, turn, kind, ref_id, text, embedding) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            self.session_id,
                            key,
                            turn,
                            kind,
                            item.ref_id,
                            text,
                            _pack(vec),
                        ),
                    )
                total += 1
            self._cache.pop(key, None)
        if self.conn is not None:
            self.conn.commit()
        return total

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
        self._check_dim(char_id, vectors)
        bucket = self._items.setdefault(char_id, [])
        for i, (text, vec) in enumerate(zip(texts, vectors)):
            item = MemoryVector(
                char_id=char_id,
                text=text,
                vector=list(vec),
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
                        _pack(item.vector),
                    ),
                )
        # 必须提交：不提交会让这个连接一直持有写锁，
        # 同一服务再开第二局时就会 "database is locked"
        if self.conn is not None:
            self.conn.commit()
        self._cache.pop(char_id, None)
        return len(texts)

    # ── 回灌 / 清理 ─────────────────────────────────────────
    def load(self, session_id: str | None = None) -> int:
        """从库里回灌本局记忆。返回读到的条数。

        resume 必须调：否则恢复后向量桶是空的，表现为「这个角色什么都想不起来」。
        """
        if self.conn is None:
            return 0
        sid = session_id or self.session_id
        if sid:
            self.session_id = sid
        rows = self.conn.execute(
            "SELECT char_id, turn, kind, ref_id, text, embedding FROM memory_vectors "
            "WHERE session_id = ? ORDER BY turn, id",
            (self.session_id,),
        ).fetchall()
        self._items.clear()
        self._cache.clear()
        for char_id, turn, kind, ref_id, text, embedding in rows:
            vec = _unpack(embedding)
            if not vec:
                continue
            self._items.setdefault(str(char_id), []).append(
                MemoryVector(
                    char_id=str(char_id),
                    text=str(text or ""),
                    vector=vec,
                    turn=int(turn or 0),
                    kind=str(kind or "event"),
                    ref_id=str(ref_id or ""),
                )
            )
        return len(rows)

    def delete_session_after(self, turn: int) -> int:
        """回滚：删掉第 `turn` 回合之后的记忆（内存 + 库）。"""
        keep, removed = {}, 0
        for key, items in self._items.items():
            alive = [it for it in items if it.turn <= turn]
            removed += len(items) - len(alive)
            if alive:
                keep[key] = alive
        self._items = keep
        self._cache.clear()
        if self.conn is not None and self.session_id:
            cur = self.conn.execute(
                "DELETE FROM memory_vectors WHERE session_id = ? AND turn > ?",
                (self.session_id, int(turn)),
            )
            self.conn.commit()
            removed = max(removed, cur.rowcount if cur.rowcount > 0 else 0)
        return removed

    def delete_session(self) -> int:
        n = sum(len(v) for v in self._items.values())
        self._items.clear()
        self._cache.clear()
        if self.conn is not None and self.session_id:
            cur = self.conn.execute(
                "DELETE FROM memory_vectors WHERE session_id = ?", (self.session_id,)
            )
            self.conn.commit()
            n = max(n, cur.rowcount if cur.rowcount > 0 else 0)
        return n

    # ── 检索 ────────────────────────────────────────────────
    def _matrix(self, key: str) -> np.ndarray | None:
        items = self._items.get(key) or []
        cached = self._cache.get(key)
        if cached is not None and cached[0] == len(items):
            return cached[1]
        if not items:
            return None
        dim = len(items[0].vector)
        rows = [it.vector for it in items if len(it.vector) == dim]
        if not rows:
            return None
        mat = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms
        self._cache[key] = (len(items), mat)
        return mat

    def search(
        self, char_id: str | list[str], query: str, top_k: int = 5, min_score: float = 0.0
    ) -> list[tuple[MemoryVector, float]]:
        """支持多桶（角色记忆 + 世界知识），统一打分后取 top-k。"""
        keys = list(char_id) if isinstance(char_id, (list, tuple)) else [char_id]
        if not query or self.embedder is None:
            return []
        pool: list[MemoryVector] = []
        mats: list[np.ndarray] = []
        for key in keys:
            mat = self._matrix(key)
            if mat is None:
                continue
            dim = mat.shape[1]
            # 只保留维度一致的条目，避免换 embedding 源后维度不齐
            pool.extend(it for it in (self._items.get(key) or []) if len(it.vector) == dim)
            mats.append(mat)
        if not pool or not mats:
            return []

        qvec = np.asarray(self.embedder.embed([query], kind="query")[0], dtype=np.float32)
        qmat = mats[0]
        if qvec.shape[0] != qmat.shape[1]:
            return []  # 维度不匹配：宁可召回不到，也不要给出错误的相似度
        qnorm = float(np.linalg.norm(qvec)) or 1.0
        qvec = qvec / qnorm

        scores = np.concatenate([m @ qvec for m in mats])
        pairs = list(zip(pool, scores.tolist()))
        pairs = [(item, float(score)) for item, score in pairs if score >= min_score]
        pairs.sort(key=lambda p: -p[1])
        return pairs[:top_k]

    def count(self, char_id: str | None = None) -> int:
        if char_id is None:
            return sum(len(v) for v in self._items.values())
        return len(self._items.get(char_id) or [])

    def all_for(self, char_id: str) -> list[MemoryVector]:
        return list(self._items.get(char_id) or [])

    def memory_bytes(self) -> int:
        """内存占用估算（字节）：诊断「会不会撞 MemoryMax」用。"""
        return sum(
            len(it.vector) * 4 for items in self._items.values() for it in items
        )
