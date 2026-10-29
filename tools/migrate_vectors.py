"""把 `memory_vectors.embedding` 从 JSON TEXT 迁成 float32 BLOB。

为什么：TEXT 存的是 float 的 repr，每条约 25~30KB；float32 字节是 6KB，差 4~5 倍。
读取端（`VectorStore.load`）两种格式都认，所以这个工具是**可选的压缩步骤**，不做也能跑。

用法：
    python tools/migrate_vectors.py --dry-run     # 只统计
    python tools/migrate_vectors.py               # 执行（先自动备份）

幂等：已经是 BLOB 的行会被跳过；重复执行不会重复转换。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trpg.memory.vector_store import _pack, _unpack  # noqa: E402


def _db_path() -> Path:
    """与 `trpg/api/app.py: DB_PATH` 保持一致。"""
    return ROOT / "data" / "trpg.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="数据库路径（默认 data/trpg.db）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = Path(args.db) if args.db else _db_path()
    if not db.exists():
        print(f"找不到数据库：{db}")
        return 1

    if not args.dry_run:
        backup_dir = ROOT / "data" / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"{db.stem}-{stamp}.db"
        shutil.copy2(db, target)
        print(f"已备份到 {target}")

    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT id, embedding FROM memory_vectors").fetchall()
    todo = [(rid, raw) for rid, raw in rows if isinstance(raw, (str, bytearray))]

    total_bytes_before = sum(len(r.encode("utf-8")) if isinstance(r, str) else len(r) for _, r in rows)
    converted = skipped = 0
    for rid, raw in todo:
        vec = _unpack(raw)
        if not vec:
            skipped += 1
            continue
        if args.dry_run:
            converted += 1
            continue
        conn.execute("UPDATE memory_vectors SET embedding = ? WHERE id = ?", (_pack(vec), rid))
        converted += 1
    if not args.dry_run:
        conn.commit()

    after = conn.execute("SELECT embedding FROM memory_vectors").fetchall()
    total_bytes_after = sum(len(r[0]) if isinstance(r[0], (bytes, bytearray)) else len(str(r[0])) for r in after)
    conn.close()

    print(f"总行数 {len(rows)}｜需转换 {len(todo)}｜已转换 {converted}｜跳过(无法解析) {skipped}")
    print(f"体积 {total_bytes_before/1024:.0f}KB → 预计 {total_bytes_after/1024:.0f}KB")
    if args.dry_run:
        print("（dry-run，未写库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
