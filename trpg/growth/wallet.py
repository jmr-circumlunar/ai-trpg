"""局外钱包：跨世界通用的抽象点数与元成长。

设计（知识库 `过程记录/方案/04-三期-局外成长插件化.md`）：
- **数据层只存抽象 `point`**；本地名字（龙血点 / 铁印）由世界包 `meta_hooks.yaml` 给。
- 点数可跨世界累计，但**每个世界独立定价、独立消耗**（不能套现）。
- 任何扣点都先校验，失败不扣（事务性）。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Wallet:
    """玩家档案的钱包。

    `owner_id` 现在固定为 `"local"`（单机阶段没有账号），但字段先留出来——
    将来接账号系统时不用改表。
    """

    def __init__(self, conn: sqlite3.Connection, owner_id: str = "local") -> None:
        self.conn = conn
        self.owner_id = owner_id

    # ── 读 ────────────────────────────────────────────────────
    def get(self, key: str, world_id: str = "") -> Any:
        """取一个键的值（默认 0；非数值键返回原值）。"""
        row = self.conn.execute(
            "SELECT value FROM meta_progress "
            "WHERE owner_id = ? AND world_id = ? AND key = ?",
            (self.owner_id, world_id, key),
        ).fetchone()
        if not row:
            return 0
        raw = row[0]
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return raw

    def balance(self, key: str = "point", *, world_id: str = "") -> int:
        """余额。**注意参数顺序是 (key, world_id)**——全局点数用 `balance("point")`。"""
        value = self.get(key, world_id)
        return int(value) if isinstance(value, (int, float)) else 0

    def entries(self, world_id: str | None = None) -> list[dict]:
        if world_id is None:
            rows = self.conn.execute(
                "SELECT world_id, key, value, updated_at FROM meta_progress "
                "WHERE owner_id = ? ORDER BY updated_at DESC",
                (self.owner_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT world_id, key, value, updated_at FROM meta_progress "
                "WHERE owner_id = ? AND world_id = ? ORDER BY updated_at DESC",
                (self.owner_id, world_id),
            ).fetchall()
        out = []
        for r in rows:
            try:
                value = json.loads(r[2])
            except Exception:  # noqa: BLE001
                value = r[2]
            out.append(
                {
                    "world_id": r[0],
                    "key": r[1],
                    "value": value,
                    "updated_at": r[3],
                }
            )
        return out

    def ledger(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT world_id, key, delta, reason, session_id, ts FROM meta_ledger "
            "WHERE owner_id = ? ORDER BY id DESC LIMIT ?",
            (self.owner_id, limit),
        ).fetchall()
        return [
            {
                "world_id": r[0],
                "key": r[1],
                "delta": r[2],
                "reason": r[3],
                "session_id": r[4],
                "ts": r[5],
            }
            for r in rows
        ]

    # ── 写 ────────────────────────────────────────────────────
    def add(
        self,
        key: str,
        amount: int,
        world_id: str = "",
        reason: str = "",
        session_id: str = "",
    ) -> int:
        """加值（可为负，但余额不得为负）。返回新值。"""
        current = self.get(key, world_id)
        if not isinstance(current, (int, float)):
            current = 0
        new = int(current) + int(amount)
        if new < 0:
            return int(current)
        self._write(key, new, world_id)
        self._ledger(key, int(amount), reason, session_id, world_id)
        return new

    def spend(
        self,
        key: str,
        amount: int,
        world_id: str = "",
        reason: str = "",
        session_id: str = "",
    ) -> bool:
        """扣值。余额不足返回 False 且**不扣**。"""
        if self.balance(key, world_id=world_id) < int(amount):
            return False
        self.add(key, -int(amount), world_id=world_id, reason=reason, session_id=session_id)
        return True

    def set(self, key: str, value: Any, world_id: str = "") -> None:
        """直接设值（用于 carry 注入等非增减场景，不记流水）。"""
        self._write(key, value, world_id)

    # ── 内部 ──────────────────────────────────────────────────
    def _write(self, key: str, value: Any, world_id: str) -> None:
        self.conn.execute(
            "INSERT INTO meta_progress (owner_id, world_id, key, value, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(owner_id, world_id, key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at",
            (self.owner_id, world_id or "", key, json.dumps(value, ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def _ledger(
        self,
        key: str,
        delta: int,
        reason: str,
        session_id: str,
        world_id: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO meta_ledger "
            "(owner_id, world_id, key, delta, reason, session_id, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (self.owner_id, world_id or "", key, int(delta), reason, session_id, _now()),
        )
        self.conn.commit()
