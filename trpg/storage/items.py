"""M03 存储 · 物品使用计数与晋升候选。

表族：item_usage
只做 SQL 存取，不校验业务规则；业务规则在 engine/ 与各模块。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def bump_item_usage(
    conn: sqlite3.Connection,
    world_id: str,
    session_id: str,
    canonical: str,
    tags: list[str] | None = None,
    turn: int = 0,
    kind: str = "prop",
    agent_id: str = "",
) -> None:
    """记一次「使用」（判据：check_self 列出 / recall 命中 / 被用作判定目标或工具）。

    `agent_id` 非空时**同时**记进 `item_usage_agent`（S6-3：道具 × Agent 维度，
    跨局稳定 agent 标识就绪前先用角色 id 占位）。
    """
    conn.execute(
        "INSERT INTO item_usage "
        "(world_id, session_id, canonical, kind, tags, uses, sessions, first_turn, last_turn) "
        "VALUES (?,?,?,?,?,1,1,?,?) "
        "ON CONFLICT(world_id, session_id, canonical) DO UPDATE SET "
        "uses = uses + 1, last_turn = excluded.last_turn",
        (
            world_id,
            session_id,
            canonical,
            kind,
            json.dumps(list(tags or []), ensure_ascii=False),
            int(turn or 0),
            int(turn or 0),
        ),
    )
    if str(agent_id or ""):
        conn.execute(
            "INSERT INTO item_usage_agent "
            "(world_id, session_id, agent_id, canonical, uses, first_turn, last_turn) "
            "VALUES (?,?,?,?,1,?,?) "
            "ON CONFLICT(world_id, session_id, agent_id, canonical) DO UPDATE SET "
            "uses = uses + 1, last_turn = excluded.last_turn",
            (
                world_id,
                session_id,
                str(agent_id),
                canonical,
                int(turn or 0),
                int(turn or 0),
            ),
        )
    conn.commit()


def item_usage_candidates_by_agent(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    agent_id: str = "",
) -> list[dict]:
    """按 **道具 × Agent** 聚合的使用统计（S6-3：晋升候选的新口径）。

    `agent_id` 为空时返回所有 agent 的合计；否则只统计该 agent。
    """
    sql = (
        "SELECT canonical, agent_id, SUM(uses) AS uses, "
        "COUNT(DISTINCT session_id) AS sessions, MAX(last_turn) AS last_turn "
        "FROM item_usage_agent WHERE world_id = ?"
    )
    params: list[Any] = [world_id]
    if str(agent_id or ""):
        sql += " AND agent_id = ?"
        params.append(str(agent_id))
    sql += " GROUP BY canonical, agent_id ORDER BY SUM(uses) DESC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "canonical": str(r[0]),
            "agent_id": str(r[1]),
            "uses": int(r[2] or 0),
            "sessions": int(r[3] or 0),
            "last_turn": int(r[4] or 0),
        }
        for r in rows
    ]


def item_usage_candidates(conn: sqlite3.Connection, world_id: str) -> list[dict]:
    """按规范化名聚合的使用统计（uses = 同局累计，sessions = 跨局局数）。"""
    rows = conn.execute(
        "SELECT canonical, SUM(uses) AS uses, COUNT(DISTINCT session_id) AS sessions, "
        "MAX(tags) AS tags, MAX(last_turn) AS last_turn "
        "FROM item_usage WHERE world_id = ? GROUP BY canonical ORDER BY SUM(uses) DESC",
        (world_id,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            tags = json.loads(r[3] or "[]")
        except Exception:  # noqa: BLE001
            tags = []
        out.append(
            {
                "canonical": r[0],
                "uses": int(r[1] or 0),
                "sessions": int(r[2] or 0),
                "tags": tags,
                "last_turn": int(r[4] or 0),
            }
        )
    return out
