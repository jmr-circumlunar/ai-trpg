"""M03 存储 · 角色结构化事件（召回用）。

表族：character_events
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


def load_events(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT char_id, turn, world_day, world_hour, kind, summary, entities, importance "
        "FROM character_events WHERE session_id = ? ORDER BY turn",
        (session_id,),
    ).fetchall()
    return [
        {
            "char_id": r[0],
            "turn": r[1],
            "day": r[2],
            "hour": r[3],
            "kind": r[4],
            "summary": r[5],
            "entities": json.loads(r[6] or "[]"),
            "importance": r[7],
        }
        for r in rows
    ]
