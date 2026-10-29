"""M03 存储 · 会话、世界状态快照、会话地图、角色卡快照。

表族：sessions / world_state / session_cards / session_map
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


def save_session(
    conn: sqlite3.Connection,
    session_id: str,
    seed: Any,
    world_id: str,
    era: str,
    created_at: str,
    name: str = "",
    status: str = "running",
    timeline: str = "",
    human_ids: list[str] | None = None,
    world_day: int = 1,
    current_turn: int = 0,
    summary: str = "",
    updated_at: str = "",
    account_id: str = "local",
    agent_ids: list[str] | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sessions "
        "(id, name, seed, world_id, era, status, created_at, timeline, human_ids_json, world_day, "
        "current_turn, summary, updated_at, account_id, agent_ids_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            name,
            str(seed),
            world_id,
            era,
            status,
            created_at,
            timeline,
            json.dumps([str(x) for x in (human_ids or [])], ensure_ascii=False),
            int(world_day or 1),
            int(current_turn or 0),
            str(summary or ""),
            updated_at or created_at,
            str(account_id or "local"),
            json.dumps([str(x) for x in (agent_ids or [])], ensure_ascii=False),
        ),
    )
    conn.commit()


def session_owner(conn: sqlite3.Connection, session_id: str) -> str:
    """这一局属于谁（用于越权拦截）。找不到返回空串。"""
    row = conn.execute("SELECT account_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return str(row[0] or "") if row else ""


def touch_session(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int = 0,
    summary: str = "",
    status: str | None = None,
) -> None:
    """更新会话进度（列表/世界选择页展示用）。**不碰状态快照**——快照走 `save_state`。

    单独一个函数是为了让写入口唯一：回合结束后只调这里一次。
    """
    sets = ["updated_at = ?", "current_turn = ?"]
    args: list[Any] = [datetime.now(timezone.utc).isoformat(timespec="seconds"), int(turn or 0)]
    if summary:
        sets.append("summary = ?")
        args.append(str(summary).replace("\n", " ")[:200])
    if status:
        sets.append("status = ?")
        args.append(str(status))
    args.append(session_id)
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()


def open_sessions(
    conn: sqlite3.Connection,
    owner: str,
    world_id: str = "",
    statuses: tuple[str, ...] = ("running", "paused"),
) -> list[dict]:
    """该账号还没结束的局。

    ⚠️ 两个用途要区分：
    - 「世界选择页」要 `running + paused`（暂停的局还能继续）
    - 「一账号一世界单局」规则只数 `running`（保存退出后就该能开新局）
    """
    marks = ",".join("?" for _ in statuses)
    sql = (
        "SELECT id, name, world_id, current_turn, updated_at, status, created_at "
        f"FROM sessions WHERE account_id = ? AND status IN ({marks})"
    )
    args: list[Any] = [owner, *statuses]
    if world_id:
        sql += " AND world_id = ?"
        args.append(world_id)
    sql += " ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC"
    return [
        {
            "session_id": r[0],
            "name": r[1],
            "world_id": r[2],
            "current_turn": r[3] or 0,
            "updated_at": r[4] or r[6],
            "status": r[5] or "running",
        }
        for r in conn.execute(sql, args).fetchall()
    ]


def world_session_stats(conn: sqlite3.Connection, owner: str) -> dict[str, dict]:
    """按世界聚合：未结束局 + 历史局数 + 最近游玩时间。"""
    out: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT world_id, status, COUNT(*), MAX(COALESCE(NULLIF(updated_at, ''), created_at)) "
        "FROM sessions WHERE account_id = ? GROUP BY world_id, status",
        (owner,),
    ).fetchall()
    for world_id, status, count, last in rows:
        item = out.setdefault(
            str(world_id or ""), {"open": 0, "ended": 0, "last_played": ""}
        )
        if status in ("running", "paused"):
            item["open"] += int(count)
        else:
            item["ended"] += int(count)
        if last and last > item["last_played"]:
            item["last_played"] = str(last)
    return out


def session_meta(conn: sqlite3.Connection, session_id: str) -> dict:
    """局的配置与归属元信息（含 `config_locked`）。"""
    row = conn.execute(
        "SELECT account_id, world_id, status, config_locked, current_turn, updated_at "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        "account_id": row[0] or "local",
        "world_id": row[1] or "",
        "status": row[2] or "running",
        "config_locked": int(row[3] or 0),
        "current_turn": int(row[4] or 0),
        "updated_at": row[5] or "",
    }


def lock_config(conn: sqlite3.Connection, session_id: str) -> None:
    """开局即锁配置：进局后世界/席位/模型都不许再改。"""
    conn.execute("UPDATE sessions SET config_locked = 1 WHERE id = ?", (session_id,))
    conn.commit()


def save_map(
    conn: sqlite3.Connection, session_id: str, region: str, seed: Any, map_json: str
) -> None:
    """落一张区域地图（开局一次）。"""
    conn.execute(
        "INSERT OR REPLACE INTO session_map (session_id, region, seed, map_json, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            session_id,
            region,
            str(seed),
            map_json,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def load_map(conn: sqlite3.Connection, session_id: str, region: str) -> dict | None:
    """读这一局的地图。**resume 只读不重生成**。"""
    row = conn.execute(
        "SELECT map_json FROM session_map WHERE session_id = ? AND region = ?",
        (session_id, region),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0] or "{}")
    except (ValueError, TypeError):
        return None

def next_world_day(conn: sqlite3.Connection, world_id: str) -> int:
    """同一世界连续多局：世界第几天（同世界累加，第一次是 1）。"""
    row = conn.execute(
        "SELECT MAX(world_day) FROM sessions WHERE world_id = ?", (world_id,)
    ).fetchone()
    return int(row[0] or 0) + 1

def load_session_meta(conn: sqlite3.Connection, session_id: str) -> dict:
    """会话级配置（二期）：时间点、人类席位与席位→Agent 映射，resume 时用来还原。

    `agent_ids` 是「这一局坐了哪些 agent」（席位顺序）。以前这一列**写了从不读**，
    于是「本世界用过哪些 agent」永远查不出来——补上（只加字段，不改既有键）。
    """
    try:
        row = conn.execute(
            "SELECT world_id, era, seed, timeline, human_ids_json, world_day, agent_ids_json "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 老库还没补列：退回旧读法，不让这一处把整页打挂
        row = conn.execute(
            "SELECT world_id, era, seed, timeline, human_ids_json, world_day "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return {}
    try:
        human_ids = json.loads(row[4] or "[]")
    except Exception:  # noqa: BLE001
        human_ids = []
    try:
        agent_ids = json.loads(row[6] or "[]") if len(row) > 6 else []
    except Exception:  # noqa: BLE001
        agent_ids = []
    return {
        "world_id": row[0] or "",
        "era": row[1] or "",
        "seed": row[2] or "",
        "timeline": row[3] or "",
        "human_ids": [str(x) for x in human_ids],
        "world_day": int(row[5] or 1),
        "agent_ids": [str(x) for x in agent_ids if str(x)],
    }


def save_state(conn: sqlite3.Connection, state, turn: int | None = None) -> None:
    """落一份状态快照。`turn` 用于 reset?to_turn=N 回滚定位。"""
    conn.execute(
        "INSERT OR REPLACE INTO world_state (session_id, version, turn, state_json) "
        "VALUES (?,?,?,?)",
        (state.session_id, state.version, int(turn or 0), state.model_dump_json()),
    )
    conn.commit()


def state_at_turn(conn: sqlite3.Connection, session_id: str, turn: int):
    """取第 N 回合结束时的状态快照（找不到就返回 None）。"""
    row = conn.execute(
        "SELECT state_json FROM world_state WHERE session_id = ? AND turn <= ? "
        "ORDER BY turn DESC, version DESC LIMIT 1",
        (session_id, int(turn)),
    ).fetchone()
    if not row:
        return None
    from ..models import GameState

    return GameState.model_validate_json(row[0])

def set_session_status(conn: sqlite3.Connection, session_id: str, status: str) -> None:
    conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()


def set_session_name(conn: sqlite3.Connection, session_id: str, name: str) -> None:
    """给这局起个名字（存档槽的「命名存档」用）。

    `sessions.name` 列本来就有（列在 `schema.py`），只是从来没有接口能写它；
    这里是唯一写它的地方。前端不要再用 localStorage 存名字——换设备就没了。
    """
    conn.execute(
        "UPDATE sessions SET name = ? WHERE id = ?", (str(name or "")[:24], session_id)
    )
    conn.commit()

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


def list_sessions(conn: sqlite3.Connection, owner: str | None = None) -> list[dict]:
    """列出已落库的会话。`owner` 非空时只返回该账号的局（账号隔离）。"""
    where = "WHERE s.account_id = ? " if owner else ""
    args: tuple = (owner,) if owner else ()
    rows = conn.execute(
        "SELECT s.id, s.name, s.world_id, s.era, s.seed, s.created_at, s.timeline, "
        "(SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turns, "
        "s.current_turn, s.summary, s.updated_at, s.status "
        "FROM sessions s " + where +
        "ORDER BY COALESCE(NULLIF(s.updated_at, ''), s.created_at) DESC",
        args,
    ).fetchall()
    out = [
        {
            "session_id": r[0],
            "name": r[1],
            "world_id": r[2],
            "era": r[3],
            "seed": r[4],
            "created_at": r[5],
            "timeline": r[6] or "",
            "turns": r[7],
            "current_turn": r[8] or 0,
            "summary": r[9] or "",
            "updated_at": r[10] or r[5],
            "status": r[11] or "running",
            "ending": None,
        }
        for r in rows
    ]
    # 附上结局摘要（GM 配置页的「继承上一局」要看它选的是什么）
    for item in out:
        row = conn.execute(
            "SELECT kind, payload_json FROM endings WHERE session_id = ?", (item["session_id"],)
        ).fetchone()
        if not row:
            continue
        try:
            payload = json.loads(row[1] or "{}")
        except Exception:  # noqa: BLE001
            payload = {}
        item["ending"] = {
            "kind": row[0],
            "summary": str(payload.get("epilogue") or "").replace("\n", " ")[:90],
        }
    return out
