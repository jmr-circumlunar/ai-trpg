"""P0：数据归属（`account_id`）的建列与迁移。

只测存储层：归属过滤属于 API 层，见后续任务（P0-T3/T4/T5）。
迁移必须是**幂等**的——`init_db` 每次启动都会跑。
"""

from __future__ import annotations

import sqlite3

from trpg.storage import db as db_mod


def _old_schema_conn(tmp_path) -> sqlite3.Connection:
    """造一个「P0 之前」的库：三张表都没有 `account_id`。"""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, name TEXT, seed TEXT, world_id TEXT, era TEXT,
            status TEXT, created_at TEXT, timeline TEXT DEFAULT '',
            world_day INTEGER DEFAULT 1, human_ids_json TEXT DEFAULT '[]'
        );
        CREATE TABLE characters (
            id TEXT PRIMARY KEY, name TEXT, profession TEXT, card_json TEXT,
            created_at TEXT, updated_at TEXT, world_id TEXT DEFAULT ''
        );
        CREATE TABLE world_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, world_id TEXT NOT NULL, key TEXT NOT NULL,
            kind TEXT NOT NULL, value_json TEXT NOT NULL, source_session TEXT,
            source_turn INTEGER, ts TEXT, pinned INTEGER DEFAULT 0
        );
        INSERT INTO sessions (id, name, seed, world_id, era, status, created_at)
            VALUES ('s_old', '旧局', '42', 'longzu', 'academy', 'running', '2026-10-01');
        INSERT INTO characters (id, name, profession, card_json, created_at, updated_at)
            VALUES ('chr_old', '旧卡', '混血', '{}', '2026-10-01', '2026-10-01');
        INSERT INTO world_ledger (world_id, key, kind, value_json, ts)
            VALUES ('longzu', 'npc:xxx', 'npc', '{}', '2026-10-01');
        """
    )
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_adds_owner_columns(tmp_path):
    conn = _old_schema_conn(tmp_path)
    db_mod.init_db(conn)

    for table in db_mod.OWNER_TABLES:
        assert "account_id" in _columns(conn, table), f"{table} 缺 account_id"

    scols = _columns(conn, "sessions")
    for name in ("updated_at", "current_turn", "summary", "config_locked"):
        assert name in scols, f"sessions 缺 {name}"


def test_migration_backfills_old_rows_to_local(tmp_path):
    conn = _old_schema_conn(tmp_path)
    db_mod.init_db(conn)

    for table in db_mod.OWNER_TABLES:
        owners = {row[0] for row in conn.execute(f"SELECT account_id FROM {table}")}
        assert owners == {"local"}, f"{table} 旧行未回填到 local：{owners}"


def test_migration_is_idempotent(tmp_path):
    conn = _old_schema_conn(tmp_path)
    db_mod.init_db(conn)
    first = {t: sorted(_columns(conn, t)) for t in db_mod.OWNER_TABLES}

    db_mod.init_db(conn)  # 第二次：不得报错、不得改变结构
    second = {t: sorted(_columns(conn, t)) for t in db_mod.OWNER_TABLES}

    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1, "迁移不得复制数据"


def test_new_db_has_owner_columns(tmp_path):
    """全新库直接建表时也要有列（不能只靠迁移补）。"""
    conn = sqlite3.connect(str(tmp_path / "new.db"))
    db_mod.init_db(conn)

    for table in db_mod.OWNER_TABLES:
        assert "account_id" in _columns(conn, table), f"{table} 建表缺 account_id"

    # PRAGMA index_list 的列是 (seq, name, unique, origin, partial)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(sessions)")}
    assert "idx_sessions_owner" in indexes
