"""回合链路追踪（M14）。

只做 HTTP 解析与序列化，玩法逻辑一律下沉到 engine/ 与各模块。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import *  # noqa: F401,F403
from ..schemas import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/sessions/{sid}/trace")
def get_trace(sid: str, request: Request) -> dict:
    session = _get(sid, request)
    conn = session.conn or _new_conn()
    try:
        rows = conn.execute(
            "SELECT turn, actor_id, expr, rolls, total, dc, margin, outcome, rng_state "
            "FROM dice_rolls WHERE session_id = ? ORDER BY turn",
            (sid,),
        ).fetchall()
        dice = [
            {
                "turn": r[0],
                "actor_id": r[1],
                "expr": r[2],
                "rolls": json.loads(r[3] or "[]"),
                "total": r[4],
                "dc": r[5],
                "margin": r[6],
                "outcome": r[7],
                "rng_state": r[8],
            }
            for r in rows
        ]
    finally:
        if session.conn is None:
            conn.close()

    llm = session.loop.llm
    director = getattr(session.loop, "director", None)
    if session.records:
        turns_out = [
            {
                "turn": r.turn,
                "scene": r.scene,
                "options": r.options,
                "actors": r.actors,
                "thoughts": r.thoughts,
                "actions": r.actions,
                "outcomes": r.outcomes,
                "narration": r.narration,
                "rejected": r.rejected,
                "loot": r.loot,
                "combat": r.combat,
                "deaths": r.deaths,
                "path_changes": r.path_changes,
                "elapsed_ms": r.elapsed_ms,
                "declare_ms": r.declare_ms,
                # 票型与计票结果（C57）：复盘页要能查「谁投了谁」
                "votes": r.votes,
                "vote_result": r.vote_result,
            }
            for r in session.records
        ]
    else:
        # resume 之后内存 records 是空的：从库里重建，别让 trace 空白
        fallback = _new_conn()
        try:
            turns_out = db.load_turns(fallback, sid)
        finally:
            fallback.close()

    # 票型与计票结果（C57）：内存 records 被清空（resume 之后）时从回执表补
    try:
        for row in conn.execute(
            "SELECT idx, votes_json, vote_result_json FROM turn_receipts "
            "WHERE session_id = ? ORDER BY idx",
            (sid,),
        ):
            turn_no = int(row[0])
            votes = json.loads(row[1] or "[]")
            vote_result = json.loads(row[2] or "{}")
            for item in turns_out:
                if int(item.get("turn") or 0) != turn_no:
                    continue
                if not item.get("votes"):
                    item["votes"] = votes
                if not item.get("vote_result"):
                    item["vote_result"] = vote_result
    except Exception:  # noqa: BLE001
        pass

    payload = {
        "session_id": sid,
        "status": session.status,
        "dice": dice,
        "recall": list(getattr(llm, "recall_log", []) or []),
        # 推理链只在 trace 露出：主页面印出来会带 DC/HP，破坏沉浸
        "reasoning": list(getattr(llm, "reasoning_log", []) or []),
        "seeds": list(getattr(llm, "seed_log", []) or []),
        "director": list(getattr(director, "log", []) or []),
        "turns": turns_out,
        "turns_source": "memory" if session.records else "db",
        # 记忆写入/召回失败留痕：静默失败看起来就像「这个角色记不住事」
        "memory_errors": list(getattr(session.loop, "memory_errors", []) or []),
        "usage": getattr(session.loop.llm, "usage", {}),
        "memories": (
            {
                cid: [e.render() for e in session.events.all_for(cid)]
                for cid in session.cards
            }
            if session.events is not None
            else {}
        ),
    }
    # **单一服务端出口**（S4-2）：trace 是观察者视角（上帝 / 复盘），
    # 裁剪函数对观察者原样返回——出口统一到一处，将来加「玩家也能看 trace」
    # 时只需改 `filter_for_viewer`，不必再逐端点改。
    from ..views import filter_for_viewer

    return filter_for_viewer(None, payload)


@router.get("/api/sessions/{sid}/export")
def export_session(sid: str, request: Request) -> dict:
    """导出一次对局的**单个 JSON**（S3-1）：prompt / output / transition 三类日志齐。

    用途：复盘、评测（S3-2 的 arena / calibrate 直接吃这份 JSON）、问题定位。

    三条边界：
    - **只读**：不改状态、不写库，重复导出结果一致；
    - **可复盘资产**：三类日志都是**落库后的持久事实**，不是内存里的临时态；
    - **不落敏感内容**：迁移日志与回合记录只含公开信息（私密心声不进日志）。
    """
    session = _get(sid, request)
    conn = session.conn or _new_conn()
    try:
        payload = db.build_session_export(
            conn,
            sid,
            extra={
                "world": session.pack.world_id,
                "world_name": session.pack.name,
                "status": session.status,
                "turn": len(session.records),
            },
        )
    finally:
        if session.conn is None:
            conn.close()
    return payload
