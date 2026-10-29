"""M03 存储 · 事件历史（append-only，S5-4）。

事件一旦发生就**留痕**：冷却、复盘、导出都从这张表算。

为什么不在 state 里：事件由**代码**调度（不经 GM），所以
- 不该进 StateDelta（模型不得写）；
- 也不该进 state（那是玩法状态，事件是「发生过的事」）。
落库 = 天然与状态分离，回滚/重放以落库为准。

冷却怎么算：不另开表 —— 同一事件的**最后一次**出现回合 + 声明里的
`cooldown_turns` 就是冷却窗口。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_event_record(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int,
    event_id: str,
    kind: str = "",
    payload: dict[str, Any] | None = None,
    created_at: str = "",
) -> None:
    """追加一条事件记录（append-only）。"""
    conn.execute(
        "INSERT INTO event_log (session_id, turn, event_id, kind, payload_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            str(session_id),
            int(turn),
            str(event_id),
            str(kind or ""),
            json.dumps(payload or {}, ensure_ascii=False, default=str),
            created_at or _now(),
        ),
    )
    conn.commit()


def load_event_records(
    conn: sqlite3.Connection, session_id: str, *, limit: int = 500
) -> list[dict[str, Any]]:
    """读回该会话的事件历史（按写入顺序）。"""
    rows = conn.execute(
        "SELECT turn, event_id, kind, payload_json, created_at FROM event_log "
        "WHERE session_id = ? ORDER BY rowid LIMIT ?",
        (str(session_id), int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for turn, event_id, kind, payload_json, created_at in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        out.append(
            {
                "turn": int(turn or 0),
                "event_id": str(event_id or ""),
                "kind": str(kind or ""),
                "payload": payload,
                "created_at": str(created_at or ""),
            }
        )
    return out


def event_cooldowns(conn: sqlite3.Connection, session_id: str) -> dict[str, int]:
    """每个事件**最后一次**出现的回合（冷却窗口的起点）。"""
    out: dict[str, int] = {}
    for row in load_event_records(conn, session_id, limit=2000):
        out[str(row["event_id"])] = int(row["turn"])
    return out
