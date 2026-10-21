"""角色卡仓库（全局角色库，不隶属于某个 session）。

一张卡 = 一个 CharacterCard（SillyTavern V2 超集）+ 一个稳定 id。
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
    def list(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, profession, updated_at FROM characters ORDER BY updated_at DESC"
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "profession": r[2], "updated_at": r[3]}
            for r in rows
        ]

    def get(self, cid: str) -> CharacterCard | None:
        row = self.conn.execute(
            "SELECT card_json FROM characters WHERE id = ?", (cid,)
        ).fetchone()
        if not row:
            return None
        return CharacterCard.model_validate_json(row[0])

    def get_many(self, ids: list[str]) -> dict[str, CharacterCard]:
        out: dict[str, CharacterCard] = {}
        for cid in ids:
            card = self.get(cid)
            if card is not None:
                out[cid] = card
        return out

    # ── 写 ──────────────────────────────────────────────────
    def save(self, card: CharacterCard, cid: str | None = None) -> str:
        cid = cid or f"chr_{uuid.uuid4().hex[:8]}"
        now = _now()
        self.conn.execute(
            "INSERT INTO characters (id, name, profession, card_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, profession=excluded.profession, "
            "card_json=excluded.card_json, updated_at=excluded.updated_at",
            (cid, card.data.name, card.ext.profession, card.model_dump_json(), now, now),
        )
        self.conn.commit()
        return cid

    def delete(self, cid: str) -> bool:
        cur = self.conn.execute("DELETE FROM characters WHERE id = ?", (cid,))
        self.conn.commit()
        return cur.rowcount > 0
