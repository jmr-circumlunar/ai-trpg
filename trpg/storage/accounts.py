"""M03 存储 · 账号与登录会话。

表族：accounts / auth_sessions
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


# ── 账号与会话 ──────────────────────────────────────────────
def count_accounts(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()
    return int(row[0] or 0)


def create_account(
    conn: sqlite3.Connection,
    username: str,
    password_hash: str,
    role: str = "user",
) -> str:
    """建账号。返回 account id；重名会抛 sqlite3.IntegrityError。"""
    aid = f"acc_{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO accounts (id, username, password_hash, role, disabled, created_at) "
        "VALUES (?,?,?,?,0,?)",
        (aid, username, password_hash, role, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    return aid


def get_account(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute(
        "SELECT id, username, password_hash, role, disabled FROM accounts WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "disabled": bool(row[4]),
    }


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, username, role, disabled, created_at, last_login_at FROM accounts ORDER BY created_at"
    ).fetchall()
    return [
        {
            "id": r[0],
            "username": r[1],
            "role": r[2],
            "disabled": bool(r[3]),
            "created_at": r[4],
            "last_login_at": r[5],
        }
        for r in rows
    ]


def set_account_password(conn: sqlite3.Connection, account_id: str, password_hash: str) -> bool:
    cur = conn.execute(
        "UPDATE accounts SET password_hash = ? WHERE id = ?", (password_hash, account_id)
    )
    conn.commit()
    return cur.rowcount > 0


def set_account_disabled(conn: sqlite3.Connection, account_id: str, disabled: bool) -> bool:
    cur = conn.execute(
        "UPDATE accounts SET disabled = ? WHERE id = ?", (1 if disabled else 0, account_id)
    )
    conn.commit()
    return cur.rowcount > 0


def touch_login(conn: sqlite3.Connection, account_id: str) -> None:
    conn.execute(
        "UPDATE accounts SET last_login_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), account_id),
    )
    conn.commit()


def insert_auth_session(
    conn: sqlite3.Connection,
    token_hash: str,
    account_id: str,
    expires_at: str,
    user_agent: str = "",
) -> None:
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, account_id, created_at, expires_at, user_agent) "
        "VALUES (?,?,?,?,?)",
        (
            token_hash,
            account_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            expires_at,
            user_agent[:200],
        ),
    )
    conn.commit()


def get_auth_session(conn: sqlite3.Connection, token_hash: str) -> dict | None:
    row = conn.execute(
        "SELECT token_hash, account_id, expires_at FROM auth_sessions WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    if not row:
        return None
    return {"token_hash": row[0], "account_id": row[1], "expires_at": row[2]}


def delete_auth_session(conn: sqlite3.Connection, token_hash: str) -> bool:
    cur = conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    return cur.rowcount > 0


def delete_account_sessions(conn: sqlite3.Connection, account_id: str) -> int:
    """改密码 / 停用账号时清掉它的所有会话（让旧 cookie 立刻失效）。"""
    cur = conn.execute("DELETE FROM auth_sessions WHERE account_id = ?", (account_id,))
    conn.commit()
    return cur.rowcount
