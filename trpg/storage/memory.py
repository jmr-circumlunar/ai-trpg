"""角色经历存储。

双写策略：
- 进程内缓存（本局立即能查到，不依赖 IO）
- 可选 SQLite 持久化（供 trace / 跨局 / 重启后回忆）

**隔离靠物理过滤**：所有查询都带 `char_id`，不是靠 prompt 说"只用自己的记忆"。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import json
import sqlite3

from ..models.memory import CharacterEvent


class EventStore:
    def __init__(self, conn: sqlite3.Connection | None = None, session_id: str = "") -> None:
        self.conn = conn
        self.session_id = session_id
        self._cache: list[CharacterEvent] = []

    # ── 写入 ────────────────────────────────────────────────
    def add(self, event: CharacterEvent) -> None:
        self._cache.append(event)
        if self.conn is not None:
            self.conn.execute(
                "INSERT INTO character_events "
                "(session_id, char_id, turn, world_day, world_hour, kind, summary, entities, importance) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    event.session_id,
                    event.char_id,
                    event.turn,
                    event.day,
                    event.hour,
                    event.kind,
                    event.summary,
                    json.dumps(event.entities, ensure_ascii=False),
                    event.importance,
                ),
            )
            # 必须提交：不提交会让这个连接一直持有写锁，
            # 同一服务再开第二局时就会 "database is locked"
            self.conn.commit()

    # ── 读取（全部带 char_id 过滤）──────────────────────────
    def _own(self, char_id: str) -> list[CharacterEvent]:
        return [e for e in self._cache if e.char_id == char_id]

    def recent(self, char_id: str, limit: int = 5) -> list[CharacterEvent]:
        """最近发生的事（按时间正序返回，便于阅读）。"""
        items = self._own(char_id)
        return items[-limit:]

    def search(self, char_id: str, keyword: str, limit: int = 3) -> list[CharacterEvent]:
        """按关键词回忆（S1 用子串匹配，S2 换向量 + FTS 混合）。"""
        if not keyword:
            return []
        hits = [
            e for e in self._own(char_id)
            if keyword in e.summary or any(keyword in x for x in e.entities)
        ]
        hits.sort(key=lambda e: (-e.importance, -e.turn))
        return hits[:limit]

    def important(self, char_id: str, limit: int = 3, threshold: int = 7) -> list[CharacterEvent]:
        hits = [e for e in self._own(char_id) if e.importance >= threshold]
        hits.sort(key=lambda e: (-e.importance, -e.turn))
        return hits[:limit]

    def all_for(self, char_id: str) -> list[CharacterEvent]:
        return list(self._own(char_id))

    def count(self) -> int:
        return len(self._cache)
