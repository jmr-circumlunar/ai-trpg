"""M03 存储 · 连接、建表、迁移、提交。

表族：全部
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

from .schema import SCHEMA


def connect(path: str | Path, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 兼容旧库：world_state 后加的 turn 列（用于 reset?to_turn=N 回滚）
    cols = {row[1] for row in conn.execute("PRAGMA table_info(world_state)")}
    if "turn" not in cols:
        conn.execute("ALTER TABLE world_state ADD COLUMN turn INTEGER DEFAULT 0")
    # 二期：会话级 timeline 与人类席位
    scols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "timeline" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN timeline TEXT DEFAULT ''")
    if "human_ids_json" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN human_ids_json TEXT DEFAULT '[]'")
    if "world_day" not in scols:
        conn.execute("ALTER TABLE sessions ADD COLUMN world_day INTEGER DEFAULT 1")
    # 角色卡归属的世界包（跨世界开局要拦住）
    ccols = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
    if "world_id" not in ccols:
        conn.execute("ALTER TABLE characters ADD COLUMN world_id TEXT DEFAULT ''")
    # P0：数据归属（旧数据一律归 'local'，可用 tools/claim_data.py 事后划归账号）
    _add_owner_columns(conn)
    conn.commit()


# P0 归属：需要加 account_id 的表（meta_progress / meta_ledger 已有 owner_id，不在此列）
OWNER_TABLES = ("sessions", "characters", "world_ledger")

# P0 会话列表所需列：列名 -> 建列语句片段
_SESSION_EXTRA_COLUMNS = {
    "updated_at": "TEXT DEFAULT ''",
    "current_turn": "INTEGER DEFAULT 0",
    "summary": "TEXT DEFAULT ''",
    "config_locked": "INTEGER DEFAULT 0",
    # P3：席位 → Agent 映射（按席位顺序存 agent_id，空串 = 该席位没绑 Agent）
    "agent_ids_json": "TEXT DEFAULT '[]'",
}


def _add_owner_columns(conn: sqlite3.Connection) -> None:
    """幂等：为玩家侧数据表补 account_id，并回填旧行。

    幂等性是硬要求——init_db 每次启动都会跑，跑两次结果必须一致。
    """
    for table in OWNER_TABLES:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue  # 表不存在（理论上不会，executescript 已建全）
        if "account_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN account_id TEXT DEFAULT 'local'")
        conn.execute(
            f"UPDATE {table} SET account_id = 'local' WHERE account_id IS NULL OR account_id = ''"
        )
    # 会话列表所需的进度与锁定列
    scols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for name, decl in _SESSION_EXTRA_COLUMNS.items():
        if name not in scols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {decl}")
    # 索引放在迁移之后建：旧库先有列才有索引
    # P4：回合的可选行动（旧库补列；新库已建好）
    tcols = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
    if tcols and "options_json" not in tcols:
        conn.execute("ALTER TABLE turns ADD COLUMN options_json TEXT DEFAULT '[]'")
    # B27：调用指标的 task 口径（旧库补列，不重建不丢数据）
    acols = {row[1] for row in conn.execute("PRAGMA table_info(agent_calls)")}
    if acols and "task" not in acols:
        conn.execute("ALTER TABLE agent_calls ADD COLUMN task TEXT DEFAULT ''")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions (account_id, world_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_characters_owner ON characters (account_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_owner ON world_ledger (account_id, world_id)"
    )

def commit(conn: sqlite3.Connection) -> None:
    conn.commit()
