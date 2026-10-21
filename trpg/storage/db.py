"""SQLite 落库（S0 只建实际用到的表）。

设计文档里还规划了 memories / world_ledger / legends / canon 等表，
它们属于 S2，S0 不建，避免建了不用。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    seed         TEXT,
    world_id     TEXT,
    era          TEXT,
    status       TEXT,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS world_state (
    session_id   TEXT,
    version      INTEGER,
    state_json   TEXT,
    PRIMARY KEY (session_id, version)
);

CREATE TABLE IF NOT EXISTS turns (
    session_id   TEXT,
    idx          INTEGER,
    actor_id     TEXT,
    thought      TEXT,
    action       TEXT,
    narration    TEXT,
    PRIMARY KEY (session_id, idx, actor_id)
);

CREATE TABLE IF NOT EXISTS dice_rolls (
    session_id   TEXT,
    turn         INTEGER,
    actor_id     TEXT,
    expr         TEXT,
    rolls        TEXT,
    total        INTEGER,
    dc           INTEGER,
    margin       INTEGER,
    outcome      TEXT,
    rng_state    TEXT
);

CREATE TABLE IF NOT EXISTS character_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    char_id      TEXT,
    turn         INTEGER,
    world_day    INTEGER,
    world_hour   INTEGER,
    kind         TEXT,
    summary      TEXT,
    entities     TEXT,
    importance   INTEGER DEFAULT 5
);

CREATE INDEX IF NOT EXISTS idx_char_events
    ON character_events (session_id, char_id, turn);

CREATE TABLE IF NOT EXISTS session_cards (
    session_id   TEXT,
    char_id      TEXT,
    card_json    TEXT,
    PRIMARY KEY (session_id, char_id)
);

CREATE TABLE IF NOT EXISTS memory_vectors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    char_id      TEXT,
    turn         INTEGER,
    kind         TEXT,
    ref_id       TEXT,
    text         TEXT,
    embedding    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_vec ON memory_vectors (session_id, char_id);

CREATE TABLE IF NOT EXISTS agent_calls (
    session_id   TEXT,
    turn         INTEGER,
    agent        TEXT,
    model        TEXT,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    latency_ms   INTEGER,
    request_json TEXT,
    response_raw TEXT,
    retry        INTEGER DEFAULT 0
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def save_session(
    conn: sqlite3.Connection,
    session_id: str,
    seed: Any,
    world_id: str,
    era: str,
    created_at: str,
    name: str = "",
    status: str = "running",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, name, seed, world_id, era, status, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (session_id, name, str(seed), world_id, era, status, created_at),
    )
    conn.commit()


def save_state(conn: sqlite3.Connection, state) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO world_state (session_id, version, state_json) VALUES (?,?,?)",
        (state.session_id, state.version, state.model_dump_json()),
    )
    conn.commit()


def save_turn(
    conn: sqlite3.Connection,
    session_id: str,
    idx: int,
    actor_id: str,
    thought: str,
    action: str,
    narration: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO turns (session_id, idx, actor_id, thought, action, narration) "
        "VALUES (?,?,?,?,?,?)",
        (session_id, idx, actor_id, thought, action, narration),
    )


def save_dice(conn: sqlite3.Connection, session_id: str, turn: int, result) -> None:
    conn.execute(
        "INSERT INTO dice_rolls (session_id, turn, actor_id, expr, rolls, total, dc, margin, outcome, rng_state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            turn,
            result.actor_id,
            result.expr,
            json.dumps(result.rolls),
            result.total,
            result.dc,
            result.margin,
            result.outcome,
            result.rng_state,
        ),
    )


def save_agent_call(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int,
    agent: str,
    model: str,
    request: Any,
    response: Any,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO agent_calls (session_id, turn, agent, model, tokens_in, tokens_out, latency_ms, request_json, response_raw) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            turn,
            agent,
            model,
            tokens_in,
            tokens_out,
            latency_ms,
            json.dumps(request, ensure_ascii=False, default=str),
            json.dumps(response, ensure_ascii=False, default=str),
        ),
    )


def commit(conn: sqlite3.Connection) -> None:
    conn.commit()


# ── 会话恢复 ────────────────────────────────────────────────
def save_cards(conn: sqlite3.Connection, session_id: str, cards: dict) -> None:
    for cid, card in cards.items():
        conn.execute(
            "INSERT OR REPLACE INTO session_cards (session_id, char_id, card_json) VALUES (?,?,?)",
            (session_id, cid, card.model_dump_json()),
        )
    conn.commit()


def load_cards(conn: sqlite3.Connection, session_id: str) -> dict:
    rows = conn.execute(
        "SELECT char_id, card_json FROM session_cards WHERE session_id = ?", (session_id,)
    ).fetchall()
    from ..models import CharacterCard

    return {cid: CharacterCard.model_validate_json(js) for cid, js in rows}


def latest_state(conn: sqlite3.Connection, session_id: str):
    row = conn.execute(
        "SELECT state_json FROM world_state WHERE session_id = ? ORDER BY version DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    from ..models import GameState

    return GameState.model_validate_json(row[0])


def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT s.id, s.name, s.world_id, s.era, s.seed, s.created_at, "
        "(SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turns "
        "FROM sessions s ORDER BY s.created_at DESC"
    ).fetchall()
    return [
        {
            "session_id": r[0],
            "name": r[1],
            "world_id": r[2],
            "era": r[3],
            "seed": r[4],
            "created_at": r[5],
            "turns": r[6],
        }
        for r in rows
    ]


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
