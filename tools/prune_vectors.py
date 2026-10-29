"""回收旧局的向量记忆（`memory_vectors`）。

为什么要单独一个工具而不是在「结束一局」时删：
- 结局页与 trace 还要读这些记忆，删了就回看不到了；
- 删数据必须是人主动的动作，不能藏在业务流程里。

用法：
    python tools/prune_vectors.py --days 30 --dry-run
    python tools/prune_vectors.py --days 30           # 真删
    python tools/prune_vectors.py --session ses_abc   # 只删某一局

只删**已结束或长期没动**的局；正在玩的局一律跳过，避免把玩家正在用的记忆清掉。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _db_path() -> Path:
    return ROOT / "data" / "trpg.db"


def _stale_sessions(conn: sqlite3.Connection, days: int) -> list[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id FROM sessions "
        "WHERE status IN ('ended', 'paused') "
        "  AND COALESCE(NULLIF(updated_at, ''), created_at) < ?",
        (cutoff,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--days", type=int, default=30, help="超过多少天的已结束局才回收")
    ap.add_argument("--session", default="", help="只处理指定会话")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = Path(args.db) if args.db else _db_path()
    if not db.exists():
        print(f"找不到数据库：{db}")
        return 1

    conn = sqlite3.connect(str(db))
    sids = [args.session] if args.session else _stale_sessions(conn, args.days)
    if not sids:
        print("没有需要回收的会话")
        conn.close()
        return 0

    marks = ",".join("?" for _ in sids)
    total = conn.execute(
        f"SELECT COUNT(*) FROM memory_vectors WHERE session_id IN ({marks})", sids
    ).fetchone()[0]
    print(f"目标会话 {len(sids)} 个｜待删向量 {total} 条")
    for sid in sids:
        print(f"  - {sid}")
    if args.dry_run:
        print("（dry-run，未删除）")
        conn.close()
        return 0

    backup_dir = ROOT / "data" / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db.stem}-prune-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(db, target)
    print(f"已备份到 {target}")

    conn.execute(f"DELETE FROM memory_vectors WHERE session_id IN ({marks})", sids)
    conn.commit()
    conn.close()
    print(f"已删除 {total} 条向量记忆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
