"""M03 存储 · 信念历史（`agent_beliefs`）。

**只做 SQL 存取，不校验业务规则**：业务规则在 `trpg/engine/beliefs.py`。
表结构见 `schema.py`（为什么单开一张表，理由写在那里）。

写入粒度：**每个角色每回合一条**（`session_id, turn, actor_id` 为主键），
读的时候取「每个角色最新的一条」= 该角色此刻的旧信念。

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


def save_beliefs(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int,
    beliefs: dict[str, Any],
) -> int:
    """写入一批信念快照（`{角色 id: mindset}`）。

    `mindset` 可以是 `Mindset` 对象或可 JSON 化的 dict；写不进去的条目**跳过**
    （信念是辅助信息，写失败不该拖垮整回合）。
    返回实际写入条数。
    """
    written = 0
    for actor_id, mindset in (beliefs or {}).items():
        if mindset is None:
            continue
        try:
            if hasattr(mindset, "model_dump"):
                payload = json.dumps(mindset.model_dump(), ensure_ascii=False)
            else:
                payload = json.dumps(mindset, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO agent_beliefs "
            "(session_id, turn, actor_id, mindset_json, created_at) VALUES (?,?,?,?,?)",
            (str(session_id), int(turn), str(actor_id), payload, _now()),
        )
        written += 1
    if written:
        conn.commit()
    return written


def load_beliefs(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int | None = None,
) -> dict[str, dict[str, Any]]:
    """读旧信念：`{角色 id: mindset dict}`。

    `turn` 给了 = 只看第 N 回合及之前（回滚场景）；
    不给 = 每个角色取**最新**的一条（即该角色此刻的旧信念）。
    """
    if turn is None:
        rows = conn.execute(
            "SELECT actor_id, mindset_json FROM agent_beliefs "
            "WHERE session_id = ? AND turn = ("
            "  SELECT MAX(turn) FROM agent_beliefs b2"
            "  WHERE b2.session_id = agent_beliefs.session_id"
            "    AND b2.actor_id = agent_beliefs.actor_id)",
            (str(session_id),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT actor_id, mindset_json FROM agent_beliefs "
            "WHERE session_id = ? AND turn = ("
            "  SELECT MAX(turn) FROM agent_beliefs b2"
            "  WHERE b2.session_id = agent_beliefs.session_id"
            "    AND b2.actor_id = agent_beliefs.actor_id AND b2.turn <= ?)",
            (str(session_id), int(turn)),
        ).fetchall()

    out: dict[str, dict[str, Any]] = {}
    for actor_id, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data:
            out[str(actor_id)] = data
    return out


def load_belief_history(
    conn: sqlite3.Connection, session_id: str, actor_id: str
) -> list[tuple[int, dict[str, Any]]]:
    """某个角色的信念历史（按回合升序）——评测与复盘用。"""
    rows = conn.execute(
        "SELECT turn, mindset_json FROM agent_beliefs "
        "WHERE session_id = ? AND actor_id = ? ORDER BY turn ASC",
        (str(session_id), str(actor_id)),
    ).fetchall()
    out: list[tuple[int, dict[str, Any]]] = []
    for turn, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data:
            out.append((int(turn), data))
    return out


__all__ = ["load_belief_history", "load_beliefs", "save_beliefs"]
