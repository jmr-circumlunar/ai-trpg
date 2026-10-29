"""B45 回合级回执落库：`turn_receipts` 往返读写。

为什么单独一张表：`turns` 是 per-actor 结构（PK 含 actor_id），回合级的
order/skipped/votes 放进去会重复 N 行；回执是「这一回合」的单一记录。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.engine.loop import TurnRecord  # noqa: E402
from trpg.storage import schema  # noqa: E402
from trpg.storage.turn import load_receipt, save_receipt  # noqa: E402


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema.SCHEMA)
    return conn


def test_receipt_roundtrip():
    conn = make_conn()
    rec = TurnRecord(
        turn=3,
        scene="x",
        order=["scene", "commit"],
        skipped=[{"id": "fight", "kind": "action", "reason": "when:enemies_present"}],
    )

    save_receipt(conn, "ses_1", 3, rec)

    data = load_receipt(conn, "ses_1", 3)
    assert data is not None
    assert data["order"] == ["scene", "commit"]
    assert data["skipped"][0]["id"] == "fight"
    assert data["votes"] == []
    assert data["vote_result"] == {}


def test_receipt_missing_returns_none():
    assert load_receipt(make_conn(), "ses_x", 1) is None


def test_receipt_upsert_overwrites():
    conn = make_conn()
    save_receipt(conn, "s", 1, TurnRecord(turn=1, scene="", order=["a"]))
    save_receipt(conn, "s", 1, TurnRecord(turn=1, scene="", order=["b"]))

    assert load_receipt(conn, "s", 1)["order"] == ["b"]


def test_receipt_carries_votes():
    conn = make_conn()
    rec = TurnRecord(
        turn=2,
        scene="",
        votes=[{"voter": "p1", "target": "p3", "round": 1}],
        vote_result={"p3": 2, "exiled": "p3"},
    )

    save_receipt(conn, "s", 2, rec)
    data = load_receipt(conn, "s", 2)

    assert data["votes"][0]["target"] == "p3"
    assert data["vote_result"]["exiled"] == "p3"
