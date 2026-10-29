"""世界账本：世界侧的跨局状态（与玩家侧的 meta 钱包是两回事）。

- **meta**：玩家侧、可跨世界、数值化（点数 / 痕迹）
- **ledger**：世界侧、只在同一个世界内、事件化（谁死了、什么事发生了）

一期只落库不生效；二期起：开局读账本 → 已死的人不再出现、已发生的事进入 GM 的上下文。

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


def write(
    conn: sqlite3.Connection,
    world_id: str,
    key: str,
    kind: str,
    value: Any,
    session_id: str = "",
    turn: int = 0,
    pinned: bool = False,
) -> None:
    """写一条账（**只追加，不改写**：同一 key 可以有多条，读时按 id 倒序取最新）。"""
    conn.execute(
        "INSERT INTO world_ledger "
        "(world_id, key, kind, value_json, source_session, source_turn, ts, pinned) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            world_id,
            key,
            kind,
            json.dumps(value, ensure_ascii=False),
            session_id,
            int(turn or 0),
            _now(),
            1 if pinned else 0,
        ),
    )
    conn.commit()


def read(conn: sqlite3.Connection, world_id: str, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT key, kind, value_json, source_session, source_turn, ts, pinned "
        "FROM world_ledger WHERE world_id = ? ORDER BY id DESC LIMIT ?",
        (world_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        try:
            value = json.loads(r[2])
        except Exception:  # noqa: BLE001
            value = r[2]
        out.append(
            {
                "key": r[0],
                "kind": r[1],
                "value": value,
                "session_id": r[3],
                "turn": r[4],
                "ts": r[5],
                "pinned": bool(r[6]),
            }
        )
    return out


def dead_npcs(conn: sqlite3.Connection, world_id: str) -> set[str]:
    """已经死在本世界的 NPC（开局时不再出现）。"""
    out: set[str] = set()
    for item in read(conn, world_id, limit=500):
        if item["kind"] != "npc_death":
            continue
        npc_id = str((item["value"] or {}).get("npc_id") or "")
        if npc_id:
            out.add(npc_id)
    return out


def summary(conn: sqlite3.Connection, world_id: str, limit: int = 8) -> str:
    """给 GM 看的「这个世界已经发生过的事」摘要。"""
    items = read(conn, world_id, limit=limit)
    if not items:
        return ""
    lines = []
    for item in reversed(items):
        value = item["value"] or {}
        text = str(value.get("summary") or value.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"- {text}")
    return "\n".join(lines)


def history(conn: sqlite3.Connection, world_id: str, key: str, limit: int = 50) -> list[dict]:
    """某个 key 的**全部历史**（追加式账本可回溯：旧记录一条都不改）。"""
    rows = conn.execute(
        "SELECT key, kind, value_json, source_session, source_turn, ts, pinned "
        "FROM world_ledger WHERE world_id = ? AND key = ? ORDER BY id ASC LIMIT ?",
        (world_id, key, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        try:
            value = json.loads(r[2] or "null")
        except Exception:  # noqa: BLE001
            value = None
        out.append(
            {
                "key": r[0],
                "kind": r[1],
                "value": value,
                "source_session": r[3],
                "source_turn": int(r[4] or 0),
                "ts": r[5],
                "pinned": bool(r[6]),
            }
        )
    return out


def latest(conn: sqlite3.Connection, world_id: str, key: str) -> dict | None:
    """某个 key 的**最新**一条（追加式账本的当前视图）。"""
    items = history(conn, world_id, key, limit=1)
    if not items:
        return None
    rows = conn.execute(
        "SELECT value_json FROM world_ledger WHERE world_id = ? AND key = ? ORDER BY id DESC LIMIT 1",
        (world_id, key),
    ).fetchall()
    if rows:
        try:
            items[0]["value"] = json.loads(rows[0][0] or "null")
        except Exception:  # noqa: BLE001
            pass
    return items[0]
