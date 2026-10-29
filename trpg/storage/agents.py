"""M03 存储 · 席位 Agent 配置（人机混合）。

表族：agents
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


# ── P3：Agent（人）──────────────────────────────────────────
# Agent = 跨局的人格与见闻容器；Character = 世界内的一张皮。1 Agent : N 卡。
def save_agent(
    conn: sqlite3.Connection,
    name: str,
    persona: str = "",
    voice: str = "",
    default_model_json: str = "",
    agent_id: str | None = None,
    owner: str = "local",
) -> str:
    aid = agent_id or f"agt_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO agents (id, account_id, name, persona, voice, default_model_json, created_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, persona=excluded.persona, "
        "voice=excluded.voice, default_model_json=excluded.default_model_json",
        (aid, str(owner or "local"), name, persona, voice, default_model_json, now),
    )
    conn.commit()
    return aid


def get_agent(conn: sqlite3.Connection, agent_id: str, owner: str | None = None) -> dict | None:
    row = conn.execute(
        "SELECT id, account_id, name, persona, voice, default_model_json, created_at "
        "FROM agents WHERE id = ?",
        (agent_id,),
    ).fetchone()
    if not row or (owner and str(row[1] or "local") != owner):
        return None
    return {
        "id": row[0],
        "name": row[2] or "",
        "persona": row[3] or "",
        "voice": row[4] or "",
        "default_model_json": row[5] or "",
        "created_at": row[6] or "",
    }


def list_agents(conn: sqlite3.Connection, owner: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, persona, voice, created_at FROM agents "
        "WHERE account_id = ? ORDER BY created_at DESC",
        (owner,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1] or "",
            "persona": r[2] or "",
            "voice": r[3] or "",
            "created_at": r[4] or "",
        }
        for r in rows
    ]


def delete_agent(conn: sqlite3.Connection, agent_id: str, owner: str | None = None) -> bool:
    sql = "DELETE FROM agents WHERE id = ?"
    args: list[Any] = [agent_id]
    if owner:
        sql += " AND account_id = ?"
        args.append(owner)
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount > 0


# ── 本世界参战名单（方案 38）────────────────────────────────
# 「谁在这个世界上场」是**世界级**配置（世界首页只读展示，配置页维护）；
# agent 的人格本身仍在全局 Agent 库编辑（唯一来源）。


def set_world_agents(
    conn: sqlite3.Connection, world_id: str, owner: str, agent_ids: list[str]
) -> None:
    """设置某个世界的参战 agent 名单（整体替换，幂等）。"""
    conn.execute(
        "DELETE FROM world_agents WHERE world_id = ? AND account_id = ?", (world_id, owner)
    )
    now = datetime.now(timezone.utc).isoformat()
    for agent_id in agent_ids:
        agent_id = str(agent_id).strip()
        if not agent_id:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO world_agents (world_id, agent_id, account_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (world_id, agent_id, owner, now),
        )
    conn.commit()


def list_world_agents(conn: sqlite3.Connection, world_id: str, owner: str) -> list[dict]:
    """某个世界的参战 agent 名单（按加入顺序）。"""
    rows = conn.execute(
        "SELECT agent_id, created_at FROM world_agents "
        "WHERE world_id = ? AND account_id = ? ORDER BY created_at",
        (world_id, owner),
    ).fetchall()
    return [{"agent_id": row[0], "created_at": row[1]} for row in rows]
