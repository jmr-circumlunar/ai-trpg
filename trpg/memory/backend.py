"""记忆后端接口。

为什么要这层：现在只有「SQLite + numpy」一种实现，将来要换 Qdrant / pgvector 时
**只写一个新实现 + 改 `config/memory.yaml: backend`**，engine / llm 一行不动。

接口刻意保持最小：写入、检索、清理、回灌。检索返回 `(item, score)`，
`item` 至少要有 `text`；其它字段由实现决定（引擎只读 `text`）。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryBackend(Protocol):
    """向量记忆后端。`session_id` 决定这一份记忆属于哪一局。"""

    session_id: str

    def add_many(
        self,
        char_id: str,
        texts: list[str],
        turn: int = 0,
        kind: str = "event",
        ref_ids: list[str] | None = None,
    ) -> int:
        """批量写入一个桶（`char_id`，世界知识用 `_lore`）。返回写入条数。"""

    def search(
        self,
        char_id: str | list[str],
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[Any, float]]:
        """跨桶检索，按相似度降序。`char_id` 传列表即多桶合并。"""

    def count(self, char_id: str | None = None) -> int: ...

    def all_for(self, char_id: str) -> list[Any]: ...

    def load(self, session_id: str | None = None) -> int:
        """从持久层回灌到内存。**resume 必须调**，否则恢复后什么都想不起来。"""

    def delete_session_after(self, turn: int) -> int:
        """回滚：删掉该回合之后的记忆（`reset?to_turn=N` 用）。"""

    def delete_session(self) -> int:
        """清理整局的记忆。"""
