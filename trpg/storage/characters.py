"""角色卡仓库（全局角色库，不隶属于某个 session）。

一张卡 = 一个 CharacterCard（SillyTavern V2 超集）+ 一个稳定 id。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from ..models import CharacterCard


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CharacterRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── 读 ──────────────────────────────────────────────────
    def list(self, owner: str | None = None) -> list[dict]:
        """`owner` 非空时只返回该账号的卡（账号隔离）。"""
        sql = "SELECT id, name, profession, updated_at, world_id FROM characters"
        args: tuple = ()
        if owner:
            sql += " WHERE account_id = ?"
            args = (owner,)
        rows = self.conn.execute(sql + " ORDER BY updated_at DESC", args).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "profession": r[2],
                "updated_at": r[3],
                "world_id": r[4] or "",
            }
            for r in rows
        ]

    def world_of(self, cid: str, owner: str | None = None) -> str:
        """这张卡属于哪个世界包（空 = 通用卡，任何世界都能用）。"""
        row = self.conn.execute(
            "SELECT world_id, account_id FROM characters WHERE id = ?", (cid,)
        ).fetchone()
        if not row or (owner and str(row[1] or "") != owner):
            return ""
        return str(row[0] or "")

    def get(self, cid: str, owner: str | None = None) -> CharacterCard | None:
        """取卡。`owner` 非空时，不属于该账号的卡视为不存在（不泄露存在性）。"""
        row = self.conn.execute(
            "SELECT card_json, account_id FROM characters WHERE id = ?", (cid,)
        ).fetchone()
        if not row or (owner and str(row[1] or "") != owner):
            return None
        return CharacterCard.model_validate_json(row[0])

    def get_many(self, ids: list[str], owner: str | None = None) -> dict[str, CharacterCard]:
        out: dict[str, CharacterCard] = {}
        for cid in ids:
            card = self.get(cid, owner)
            if card is not None:
                out[cid] = card
        return out

    # ── 写 ──────────────────────────────────────────────────
    def save(
        self,
        card: CharacterCard,
        cid: str | None = None,
        world_id: str = "",
        owner: str = "local",
    ) -> str:
        """存卡。`world_id` 记这张卡属于哪个世界包（空 = 通用卡）。

        `owner` 只在**新建**时写入；更新不夺走原归属（防止越权改卡顺手换主人）。
        """
        cid = cid or f"chr_{uuid.uuid4().hex[:8]}"
        now = _now()
        self.conn.execute(
            "INSERT INTO characters (id, name, profession, card_json, created_at, updated_at, "
            "world_id, account_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, profession=excluded.profession, "
            "card_json=excluded.card_json, updated_at=excluded.updated_at, world_id=excluded.world_id",
            (
                cid,
                card.data.name,
                card.ext.profession,
                card.model_dump_json(),
                now,
                now,
                str(world_id or ""),
                str(owner or "local"),
            ),
        )
        self.conn.commit()
        return cid

    def delete(self, cid: str, owner: str | None = None) -> bool:
        sql = "DELETE FROM characters WHERE id = ?"
        args: tuple = (cid,)
        if owner:
            sql += " AND account_id = ?"
            args = (cid, owner)
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.rowcount > 0
