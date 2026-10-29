"""M03 存储 · 回合、骰子、模型调用、结局。

表族：turns / dice_rolls / agent_calls / session_summaries / endings
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


# ── 结算 ────────────────────────────────────────────────────
def save_ending(
    conn: sqlite3.Connection,
    session_id: str,
    kind: str,
    turn: int,
    payload: dict,
    created_at: str = "",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO endings (session_id, kind, turn, payload_json, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            session_id,
            kind,
            int(turn),
            json.dumps(payload, ensure_ascii=False, default=str),
            created_at,
        ),
    )
    conn.commit()


def load_ending(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT kind, turn, payload_json, created_at FROM endings WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "kind": row[0],
        "turn": row[1],
        "payload": json.loads(row[2] or "{}"),
        "created_at": row[3],
    }

def save_receipt(conn: sqlite3.Connection, session_id: str, idx: int, rec) -> None:
    """落回合级回执（B45）：`TurnRecord` 的 order / skipped / votes。

    单独一张表而不是塞进 `turns`：`turns` 是 per-actor 结构，回合级字段会重复 N 行。
    """
    import json

    conn.execute(
        "INSERT OR REPLACE INTO turn_receipts "
        "(session_id, idx, order_json, skipped_json, votes_json, vote_result_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            session_id,
            idx,
            json.dumps(list(getattr(rec, "order", []) or []), ensure_ascii=False),
            json.dumps(list(getattr(rec, "skipped", []) or []), ensure_ascii=False),
            json.dumps(list(getattr(rec, "votes", []) or []), ensure_ascii=False),
            json.dumps(dict(getattr(rec, "vote_result", {}) or {}), ensure_ascii=False),
        ),
    )
    conn.commit()


def load_receipt(conn: sqlite3.Connection, session_id: str, idx: int) -> dict | None:
    """读回执；没有则返回 None（旧局没有这张表的数据）。"""
    import json

    row = conn.execute(
        "SELECT order_json, skipped_json, votes_json, vote_result_json "
        "FROM turn_receipts WHERE session_id=? AND idx=?",
        (session_id, idx),
    ).fetchone()
    if row is None:
        return None
    return {
        "order": json.loads(row[0] or "[]"),
        "skipped": json.loads(row[1] or "[]"),
        "votes": json.loads(row[2] or "[]"),
        "vote_result": json.loads(row[3] or "{}"),
    }


def save_turn(
    conn: sqlite3.Connection,
    session_id: str,
    idx: int,
    actor_id: str,
    thought: str,
    action: str,
    narration: str,
    options_json: str = "[]",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO turns "
        "(session_id, idx, actor_id, thought, action, narration, options_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (session_id, idx, actor_id, thought, action, narration, options_json),
    )
    conn.commit()  # 独立提交：不留悬挂事务（否则会锁住其他会话的写入）


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
    conn.commit()  # 同上


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
    retry: int = 0,
    task: str = "",
) -> None:
    conn.execute(
        "INSERT INTO agent_calls (session_id, turn, agent, task, model, tokens_in, tokens_out, latency_ms, retry, request_json, response_raw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            turn,
            agent,
            task,
            model,
            tokens_in,
            tokens_out,
            latency_ms,
            retry,
            json.dumps(request, ensure_ascii=False, default=str),
            json.dumps(response, ensure_ascii=False, default=str),
        ),
    )

# ── 章节摘要（B25：读档回灌用）────────────────────────────
def save_summary(
    conn: sqlite3.Connection,
    session_id: str,
    upto_turn: int,
    text: str,
    created_at: str = "",
) -> None:
    """一条 = 一章。同一 `upto_turn` 重复生成时覆盖（幂等）。"""
    if not str(text or "").strip():
        return
    conn.execute(
        "INSERT OR REPLACE INTO session_summaries (session_id, upto_turn, text, created_at) "
        "VALUES (?,?,?,?)",
        (
            session_id,
            int(upto_turn),
            text,
            created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def load_summaries(
    conn: sqlite3.Connection, session_id: str, limit: int = 8
) -> list[str]:
    """按时间正序取最近若干章（回灌顺序必须与生成顺序一致）。"""
    rows = conn.execute(
        "SELECT text FROM session_summaries WHERE session_id = ? "
        "ORDER BY upto_turn DESC LIMIT ?",
        (session_id, int(limit)),
    ).fetchall()
    return [str(r[0]) for r in reversed(rows)]


def delete_summaries_after(
    conn: sqlite3.Connection, session_id: str, turn: int
) -> None:
    """回滚时清掉「未来」的摘要，否则读档会看到还没发生的事。"""
    conn.execute(
        "DELETE FROM session_summaries WHERE session_id = ? AND upto_turn > ?",
        (session_id, int(turn)),
    )


def recent_narrations(
    conn: sqlite3.Connection, session_id: str, limit: int = 9
) -> list[str]:
    """最近若干条叙事（按时间正序），用于重建时回灌短期上下文。"""
    rows = conn.execute(
        "SELECT narration FROM turns WHERE session_id = ? AND narration <> '' "
        "ORDER BY idx DESC, rowid DESC LIMIT ?",
        (session_id, int(limit)),
    ).fetchall()
    return [str(r[0]) for r in reversed(rows)]


def load_turns(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """从 `turns` 表重建回合记录（供 resume 后的 trace / 历史回放用）。

    `turns` 每行是「一个角色在一个回合里的一个行动」，这里按回合聚合。
    注意：`scene`（GM 本回合场景）**没有落库**，这里给空串——见待办 P8-T8。
    """
    rows = conn.execute(
        "SELECT idx, actor_id, thought, action, narration, options_json FROM turns "
        "WHERE session_id = ? ORDER BY idx, actor_id",
        (session_id,),
    ).fetchall()
    by_turn: dict[int, dict] = {}
    for idx, actor_id, thought, action, narration, options_json in rows:
        item = by_turn.setdefault(
            int(idx),
            {
                "turn": int(idx),
                "scene": "",
                "options": [],
                "actors": [],
                "thoughts": {},
                "actions": {},
                "narration": "",
            },
        )
        if not item["options"] and options_json:
            try:
                item["options"] = json.loads(options_json or "[]")
            except (ValueError, TypeError):
                item["options"] = []
        item["actors"].append(str(actor_id))
        item["thoughts"][str(actor_id)] = thought or ""
        item["actions"][str(actor_id)] = action or ""
        if not item["narration"] and narration:
            # 叙事是按角色给的，聚合成一段便于阅读
            item["narration"] = str(narration)
        elif narration and narration != item["narration"]:
            item["narration"] += "\n" + str(narration)
    return [by_turn[k] for k in sorted(by_turn)]


def last_turn_index(conn: sqlite3.Connection, session_id: str) -> int:
    """已落库的最大回合序号（没有则为 0）。用于 resume 时定位下一个回合。"""
    row = conn.execute(
        "SELECT MAX(idx) FROM turns WHERE session_id = ?", (session_id,)
    ).fetchone()
    return int(row[0] or 0) if row else 0
