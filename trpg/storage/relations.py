"""M03 存储 · NPC 关系（带时间区间，S5-1）。

表 `npc_relations`：**append-only** —— 关系变化是「闭合旧区间 + 追加新事实」，
不是覆盖写。这样「当时他们是什么关系」永远查得到。

查询有两条口径：
- `load_relations(conn, sid)`：全部历史（含已失效）；
- `load_relations_at(conn, sid, turn)`：**某个时点当时成立**的关系。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..models.relation import Relation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_relation(
    conn: sqlite3.Connection,
    session_id: str,
    relation: Relation | dict[str, Any],
    *,
    created_at: str = "",
) -> None:
    """追加一条关系事实（append-only）。"""
    item = relation if isinstance(relation, Relation) else Relation(**dict(relation))
    conn.execute(
        "INSERT INTO npc_relations (session_id, subject, object, kind, weight, "
        "valid_from, valid_to, source_event, recorded_turn, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            str(session_id),
            item.subject,
            item.object,
            item.kind,
            float(item.weight),
            int(item.valid_from),
            None if item.valid_to is None else int(item.valid_to),
            str(item.source_event or ""),
            int(item.recorded_turn or 0),
            created_at or _now(),
        ),
    )
    conn.commit()


def close_relation(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    subject: str,
    object: str,
    kind: str = "",
    at_turn: int | None = None,
) -> int:
    """把当前有效的关系**闭合**（写 `valid_to`），不删历史。

    返回被闭合的条数。`kind` 给了就只闭合同类，否则闭合两端之间的全部关系。
    """
    sql = (
        "UPDATE npc_relations SET valid_to = ? "
        "WHERE session_id = ? AND valid_to IS NULL "
        "AND ((subject = ? AND object = ?) OR (subject = ? AND object = ?))"
    )
    params: list[Any] = [int(at_turn or 0), str(session_id), subject, object, object, subject]
    if kind:
        sql += " AND kind = ?"
        params.append(str(kind))
    cur = conn.execute(sql, params)
    conn.commit()
    return int(cur.rowcount or 0)


def load_relations(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int = 500,
) -> list[Relation]:
    """读回该会话的**全部**关系历史（已失效的也在，按写入顺序）。"""
    rows = conn.execute(
        "SELECT subject, object, kind, weight, valid_from, valid_to, source_event, "
        "recorded_turn FROM npc_relations WHERE session_id = ? ORDER BY rowid LIMIT ?",
        (str(session_id), int(limit)),
    ).fetchall()
    return [
        Relation(
            subject=str(row[0]),
            object=str(row[1]),
            kind=str(row[2]),
            weight=float(row[3] or 0.0),
            valid_from=int(row[4] or 0),
            valid_to=None if row[5] is None else int(row[5]),
            source_event=str(row[6] or ""),
            recorded_turn=int(row[7] or 0),
        )
        for row in rows
    ]


def load_relations_at(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int | None = None,
    *,
    limit: int = 500,
) -> list[Relation]:
    """读回**某个时点当时成立**的关系。

    `turn=None` = 此刻（`valid_to` 仍为空的那些）。
    """
    return [item for item in load_relations(conn, session_id, limit=limit) if item.active_at(turn)]


def relations_text(relations: list[Relation]) -> str:
    """把关系列表渲染成 prompt 片段（没有就返回空串，不写「没有关系」）。"""
    lines = [item.describe() for item in relations or []]
    return "\n".join(lines)
