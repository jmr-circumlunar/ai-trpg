"""M03 存储 · 对局可观测：状态迁移日志 + 模型调用日志读取 + 对局导出。

S3-1 把「推理轨迹」从内存态变成**可复盘资产**：

- **prompt / output**：`agent_calls` 表（已有写入），这里提供读取；
- **transition**：`state_transitions` 表（新增，append-only）——状态迁移日志与
  状态快照**同源同点**写入（引擎的 `_persist` 处），回滚 = 快照 + 日志；
- **导出**：一次对局导出**单个 JSON**（含决策理由），供复盘与评测消费。

两条纪律：
1. **不落敏感内容**：迁移日志只记公开信息（行动 / 判定 / 叙事摘要），
   私密心声与私密笔记不进日志（导出具备时才按查看者脱敏计算）。
2. **只读导出**：导出不改库、不推进回合，重复调用结果一致。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

EXPORT_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── transition（状态迁移日志）────────────────────────────────
def save_transition(
    conn: sqlite3.Connection,
    session_id: str,
    turn: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    actor_id: str = "",
    created_at: str = "",
) -> None:
    """追加一条状态迁移日志（append-only，不覆盖历史）。

    `payload` 只放**公开**信息：行动、判定摘要、叙事摘要、状态版本。
    私密内容（心声 / 私密笔记）不进这里 —— 落库即扩散，事后无法收回。
    """
    conn.execute(
        "INSERT INTO state_transitions "
        "(session_id, turn, actor_id, kind, payload_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            str(session_id),
            int(turn),
            str(actor_id or ""),
            str(kind or ""),
            json.dumps(payload or {}, ensure_ascii=False, default=str),
            created_at or _now(),
        ),
    )
    conn.commit()


def load_transitions(
    conn: sqlite3.Connection, session_id: str, limit: int = 1000
) -> list[dict]:
    """按顺序读回该会话的迁移日志（时间正序）。"""
    rows = conn.execute(
        "SELECT turn, actor_id, kind, payload_json, created_at FROM state_transitions "
        "WHERE session_id = ? ORDER BY id LIMIT ?",
        (str(session_id), int(limit)),
    ).fetchall()
    out: list[dict] = []
    for turn, actor_id, kind, payload_json, created_at in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (ValueError, TypeError):
            payload = {}
        out.append(
            {
                "turn": int(turn),
                "actor_id": str(actor_id or ""),
                "kind": str(kind or ""),
                "payload": payload,
                "created_at": str(created_at or ""),
            }
        )
    return out


# ── prompt / output（模型调用）────────────────────────────────
def load_agent_calls(
    conn: sqlite3.Connection, session_id: str, limit: int = 500
) -> list[dict]:
    """读回该会话的模型调用：prompt（request_json）/ output（response_raw）。"""
    rows = conn.execute(
        "SELECT turn, agent, task, model, tokens_in, tokens_out, latency_ms, "
        "request_json, response_raw, retry FROM agent_calls "
        "WHERE session_id = ? ORDER BY rowid LIMIT ?",
        (str(session_id), int(limit)),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        prompt_raw = row[7] or ""
        output_raw = row[8] or ""
        prompt: Any = prompt_raw
        try:
            prompt = json.loads(prompt_raw)
        except (ValueError, TypeError):
            pass
        output: Any = output_raw
        try:
            output = json.loads(output_raw)
        except (ValueError, TypeError):
            pass
        out.append(
            {
                "turn": _as_int(row[0]),
                "agent": str(row[1] or ""),
                "task": str(row[2] or ""),
                "model": str(row[3] or ""),
                "tokens_in": _as_int(row[4]),
                "tokens_out": _as_int(row[5]),
                "latency_ms": _as_int(row[6]),
                "prompt": prompt,
                "output": output,
                "retry": _as_int(row[9]),
            }
        )
    return out


def _as_int(value: Any) -> int:
    """脏数据不炸导出：能转 int 就转，转不了当 0。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ── 导出 ────────────────────────────────────────────────────
def build_session_export(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    viewer: str | None = None,
    limit_calls: int = 500,
    limit_transitions: int = 1000,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装一次对局的**单个 JSON**（含决策理由）。

    结构：

    ```
    {
      "schema_version": 1,
      "session_id": ..., "world": ..., "status": ..., "turn": N,
      "turns":        [...],   # 回合记录（行动 / 判定 / 叙事）
      "transitions":  [...],   # 状态迁移日志
      "calls":        [...],   # prompt / output（模型调用）
      "ending":       {...}    # 结局（若有）
    }
    ```

    只读：不动状态、不写库；`extra` 由调用方补（世界名、布局等**世界无关**的元信息）。
    """
    payload: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "session_id": str(session_id),
        "exported_at": _now(),
        "turns": [],
        "transitions": load_transitions(conn, session_id, limit=limit_transitions),
        "calls": load_agent_calls(conn, session_id, limit=limit_calls),
        "ending": None,
    }
    payload.update(extra or {})

    try:
        from .turn import load_turns

        payload["turns"] = load_turns(conn, session_id)
    except Exception:  # noqa: BLE001
        payload["turns"] = []

    try:
        from .turn import load_ending

        saved = load_ending(conn, session_id)
        if saved:
            payload["ending"] = {
                "kind": saved.get("kind"),
                "turn": saved.get("turn"),
                "payload": saved.get("payload"),
            }
    except Exception:  # noqa: BLE001
        payload["ending"] = None

    return payload
