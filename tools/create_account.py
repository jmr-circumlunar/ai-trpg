"""建账号 / 重置密码（管理命令行）。

公网开放前的账号入口：公网**不开放自助注册**，账号由管理员在这台机器上创建。

用法：
    python tools/create_account.py --user alice --password '强口令'
    python tools/create_account.py --user alice --reset              # 重置密码（生成随机并打印）
    python tools/create_account.py --user alice --disable           # 停用
    python tools/create_account.py --user alice --enable            # 启用
    python tools/create_account.py --list                          # 列出所有账号

密码规则：至少 8 位。改密码会**清掉该账号的所有会话**（旧 cookie 立刻失效）。
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trpg.storage import auth as auth_mod  # noqa: E402
from trpg.storage import db  # noqa: E402


def _conn():
    """直接开库（不 import api 模块，避免带出 FastAPI 副作用）。"""
    import sqlite3

    from trpg.api.app import DB_PATH

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    db.init_db(conn)
    return conn


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="账号管理")
    parser.add_argument("--user", default="", help="用户名")
    parser.add_argument("--password", default="", help="密码（至少 8 位）")
    parser.add_argument("--role", default="user", help="admin | user")
    parser.add_argument("--reset", action="store_true", help="重置密码（生成随机并打印）")
    parser.add_argument("--disable", action="store_true", help="停用账号")
    parser.add_argument("--enable", action="store_true", help="启用账号")
    parser.add_argument("--list", action="store_true", help="列出账号")
    args = parser.parse_args(argv)

    conn = _conn()
    try:
        if args.list:
            for row in db.list_accounts(conn):
                mark = "（已停用）" if row["disabled"] else ""
                print(f"  {row['username']:20s} role={row['role']:6s} {mark} 最近登录={row['last_login_at'] or '—'}")
            return 0

        username = args.user.strip()
        if not username:
            print("需要 --user")
            return 1

        account = db.get_account(conn, username)
        if account is None:
            password = args.password
            if not password:
                password = secrets.token_urlsafe(12)
            if len(password) < 8:
                print("密码至少 8 位")
                return 1
            db.create_account(
                conn, username=username, password_hash=auth_mod.hash_password(password), role=args.role
            )
            print(f"已创建账号 {username}（role={args.role}）")
            if not args.password:
                print(f"随机密码：{password}  ← 登录后请立刻改掉")
            return 0

        # 已存在：改密 / 停用 / 启用
        if args.reset:
            password = args.password or secrets.token_urlsafe(12)
            if len(password) < 8:
                print("密码至少 8 位")
                return 1
            db.set_account_password(conn, account["id"], auth_mod.hash_password(password))
            db.delete_account_sessions(conn, account["id"])
            print(f"已重置 {username} 的密码，并清掉其所有会话")
            if not args.password:
                print(f"随机密码：{password}")
            return 0

        if args.disable:
            db.set_account_disabled(conn, account["id"], True)
            db.delete_account_sessions(conn, account["id"])
            print(f"已停用 {username}（其会话已失效）")
            return 0

        if args.enable:
            db.set_account_disabled(conn, account["id"], False)
            print(f"已启用 {username}")
            return 0

        print(f"账号 {username} 已存在。用 --reset / --disable / --enable 操作。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
