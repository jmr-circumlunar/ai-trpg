"""局内推进（M14）：人机席位、行动提交、回合推进、结算与结局。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from fastapi import APIRouter

from ...engine.beliefs import beliefs_of, restore_beliefs
from ...map import exits_for, generate_region_map
from ..views import filter_for_viewer
from ..deps import *  # noqa: F401,F403
from ..schemas import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/sessions/{sid}/human/mode")
def set_human_mode(sid: str, payload: dict, request: Request) -> dict:
    """人类席位两态（P4-T10）：wait = 一直等你，不抢；handoff = 超时代打。"""
    session = _get(sid, request)
    mode = str(payload.get("mode") or "").strip()
    if mode not in ("wait", "handoff"):
        raise HTTPException(400, "mode 只能是 wait 或 handoff")
    if session.human is None:
        raise HTTPException(400, "本局没有人类席位")
    session.human.mode = mode
    return {"session_id": sid, "mode": mode}

@router.get("/api/sessions/{sid}/pending")
def get_pending(sid: str, request: Request) -> dict:
    """当前是否在等人行动（前端用它决定是否弹输入框）。

    S4-3：pending 里额外带两块真人面板要用的东西 ——
    ① `visible`：该角色**当前可见**的私有信息（自己的身份 / 私有笔记 / 状态）；
    ② `suggestions`：**带理由**的建议（来自信念快照里的怀疑度，含 reason）。

    纪律：**建议不代替人决策** —— 后端只给建议与依据，采不采纳由人决定。
    """
    session = _get(sid, request)
    seat = session.human
    if seat is None:
        return {"pending": None, "human_ids": []}
    pending = seat.pending()
    if not pending:
        return {"pending": None, "human_ids": list(seat.human_ids)}

    cid = str(pending.get("char_id") or "")
    visible: dict[str, object] = {}
    suggestions: list[dict[str, object]] = []
    try:
        runtime = session.state.characters.get(cid)
        if runtime is not None:
            visible = {
                "notes": list(runtime.notes or []),
                "hp": runtime.hp,
                "sanity": runtime.sanity,
                "conditions": list(runtime.conditions or []),
            }
        card = session.cards.get(cid)
        if card is not None:
            visible["identity"] = {
                "name": card.data.name,
                "profession": card.ext.profession,
                "path": card.ext.path,
            }
        if session.conn is not None and cid:
            beliefs = db.load_beliefs(session.conn, sid)
            mindset = dict(beliefs.get(cid) or {})
            suspicions = dict(mindset.get("suspicions") or {})
            for target, item in suspicions.items():
                item = dict(item or {})
                suggestions.append(
                    {
                        "target": str(target),
                        "label": str(item.get("label") or ""),
                        "confidence": float(item.get("confidence") or 0.0),
                        "reason": str(item.get("reason") or ""),
                    }
                )
            suggestions.sort(key=lambda row: -float(row["confidence"]))
        pending = {
            **pending,
            "visible": visible,
            "suggestions": suggestions[:3],
        }
    except Exception:  # noqa: BLE001
        pass
    return {"pending": pending, "human_ids": list(seat.human_ids)}

@router.get("/api/sessions/{sid}/history")
def get_history(sid: str, request: Request, turns: int = 0) -> dict:
    """本局最近 N 回合的事件回填（只读）。G9 bug 3：刷新后主时间线清零。

    前端时间线只累积「本次挂载后收到的 SSE 事件」，一刷新就归零——角色状态还在、
    发生过的事全没了，上帝视角等于失忆。这里从**已落库**的 `turns` / `turn_receipts`
    重建公开事件序列（谁做了什么 + GM 那一段叙事 + 票型），不让页面去猜。

    三条边界：
    - **只读**：不动 state、不写库、不推进回合，重复调用结果一致（可重入）；
    - **不越权**：私密心声仍按 `visibility.thoughts` 逐人裁剪，回填不是绕可见性的后门；
    - **窗口在规则里**：默认/上限取 `rules/ruleset.yaml: ui.history_turns*`，代码不写死。
    """
    session = _get(sid, request)
    viewer = request.query_params.get("viewer")

    config = dict(getattr(session.loop.rules, "ruleset", {}).get("ui") or {})
    default_n = max(1, int(config.get("history_turns") or 3))
    max_n = max(default_n, int(config.get("history_turns_max") or 10))
    try:
        wanted = int(turns or 0)
    except (TypeError, ValueError):
        wanted = 0
    limit = min(max(wanted or default_n, 1), max_n)

    conn = session.conn or _new_conn()
    try:
        rows = db.load_turns(conn, sid)
        receipts: dict[int, dict] = {}
        for row in rows:
            turn_no = int(row.get("turn") or 0)
            saved = db.load_receipt(conn, sid, turn_no)
            if saved:
                receipts[turn_no] = saved
    finally:
        if session.conn is None:
            conn.close()

    picked = rows[-limit:] if rows else []
    timeline: list[dict] = []
    for row in picked:
        turn_no = int(row.get("turn") or 0)
        timeline.append(
            {"kind": "banner", "turn": turn_no, "who": "", "text": f"第 {turn_no} 回合",
             "extra": "", "thought": ""}
        )
        thoughts = dict(row.get("thoughts") or {})
        actions = dict(row.get("actions") or {})
        for cid in row.get("actors") or []:
            cid = str(cid)
            thought = str(thoughts.get(cid) or "")
            if thought:
                # **单一服务端出口**（S4-2）：思维过程可见性统一在这里判
                filtered = filter_for_viewer(
                    viewer,
                    {"id": cid, "thought": thought},
                    kind="log",
                    observer=_is_observer(session, viewer),
                    thoughts_visible=lambda who: _thoughts_visible(session, viewer, who),
                )
                thought = str(filtered.get("thought") or "")
            card = session.cards.get(cid)
            timeline.append(
                {
                    "kind": "actor",
                    "turn": turn_no,
                    "who": card.data.name if card is not None else cid,
                    "text": str(actions.get(cid) or ""),
                    "extra": "",
                    "thought": thought,
                }
            )
        vote_result = dict((receipts.get(turn_no) or {}).get("vote_result") or {})
        counts = {k: v for k, v in (vote_result.get("counts") or {}).items() if k != "__abstain__"}
        if counts:
            text = "、".join(
                f"{session.cards[cid].data.name if cid in session.cards else cid} {n} 票"
                for cid, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            )
            timeline.append(
                {"kind": "broadcast", "turn": turn_no, "who": "公布", "text": text,
                 "extra": "", "thought": ""}
            )
        narration = str(row.get("narration") or "")
        if narration:
            timeline.append(
                {"kind": "scene", "turn": turn_no, "who": "", "text": narration,
                 "extra": "", "thought": ""}
            )

    return {
        "session_id": sid,
        "turns": picked,
        "timeline": timeline,
        "window": limit,
        "from_db": bool(picked),
    }


@router.post("/api/sessions/{sid}/action")
def submit_action(sid: str, req: HumanActionReq, request: Request) -> dict:
    """人类提交行动。必须在 advance 正在等待时调用（否则 409）。"""
    session = _get(sid, request)
    seat = session.human
    if seat is None:
        raise HTTPException(400, "本局没有人类席位")
    pending = seat.pending()
    if pending is None:
        raise HTTPException(409, "当前没有在等人类行动（可能已超时，或还没轮到）")

    target = str(req.char_id or pending["char_id"])
    if target != str(pending["char_id"]):
        raise HTTPException(409, f"现在轮到 {pending['name']}，不是你提交的角色 {target}")
    if pending.get("kind") == "vote":
        # 投票：动作文本可以为空，但必须选一个目标
        if not req.target:
            raise HTTPException(400, "投票必须选一个目标")
    elif not req.ghost and not req.action.strip():
        raise HTTPException(400, "行动不能为空（或传 ghost=true 交还给 LLM）")

    ok = seat.submit(target, req.model_dump())
    if not ok:
        raise HTTPException(409, "提交失败：这一次等待已经结束")
    return {"ok": True, "char_id": target, "ghost": bool(req.ghost)}

@router.post("/api/sessions/{sid}/advance")
async def advance(sid: str, request: Request) -> dict:
    session = _get(sid, request)

    async with _lock_for(sid):
        rec = await session.loop.step()

    session.records.append(rec)
    if session.conn is not None:
        try:
            db.save_state(session.conn, session.loop.state, rec.turn)
            # 信念历史（S2-2）：每人每回合一条，跨回合累积的留痕
            db.save_beliefs(
                session.conn, sid, rec.turn, beliefs_of(session.loop.state)
            )
            db.touch_session(session.conn, sid, rec.turn, rec.scene)
        except Exception:  # noqa: BLE001
            pass

    # 终局（C58）：判定在 loop 内完成（`state.winner`），API 不再自己判一次
    ending_payload = None
    if session.status != "ended" and session.loop.state.winner:
        ending_payload = _finish(session, kind="wipe", turn=rec.turn, rec=rec)
    payload = _turn_payload(session, rec, request.query_params.get("viewer"))
    payload["ending"] = ending_payload
    return payload

@router.get("/api/sessions/{sid}/advance/stream")
async def advance_stream(sid: str, request: Request) -> StreamingResponse:
    """推进一回合，用 SSE 逐阶段推送（场景 → 每个角色 → 叙事 → 提交 → 结果）。

    为什么不做 token 级：本项目的 LLM 输出是 JSON，只有完整 JSON 才能解析；
    把半个 JSON 推给玩家没有意义。阶段级事件已经能消掉「30 秒没有任何反馈」。
    """
    session = _get(sid, request)
    viewer = request.query_params.get("viewer")
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        """把 loop 的进度回调转成队列写入，并做**出站裁剪**（C21）。

        loop 只负责如实申报「发生了什么、这段话说给谁听」（`audience`），
        谁该收到由这里决定：查看者不是观察者、又不在名单内 → 直接丢弃。
        注意是**不入队**而不是「入队后不显示」——前端拿不到就不存在泄密面。

        逐事件判定而不在进函数时算一次：本回合里查看者自己可能刚好出局，
        出局后按观察者处理（复盘语义）才是对的。
        """
        audience = data.get("audience") if isinstance(data, dict) else None
        if (
            isinstance(audience, list)
            and audience
            and viewer not in audience
            and not _is_observer(session, viewer)
        ):
            return
        # 心理活动（心声流）按 `visibility.thoughts` 逐人裁剪：
        # 上帝视角 / 本人 / `all` 世界才保留，其余删字段——事件本身（谁做了什么）仍然公开。
        # **单一服务端出口**（S4-2）：裁剪逻辑只有 `filter_for_viewer` 这一处。
        data = filter_for_viewer(
            viewer,
            data,
            kind="log",
            observer=_is_observer(session, viewer),
            thoughts_visible=lambda cid: _thoughts_visible(session, viewer, cid),
        )
        queue.put_nowait((event, data))

    async def runner() -> None:
        try:
            async with _lock_for(sid):
                rec = await session.loop.step(progress=emit)
            session.records.append(rec)
            if session.conn is not None:
                try:
                    db.save_state(session.conn, session.loop.state, rec.turn)
                    db.touch_session(session.conn, sid, rec.turn, rec.scene)
                except Exception:  # noqa: BLE001
                    pass
            payload = _turn_payload(session, rec, viewer)
            # 终局（C58）：只消费 loop 判好的结果，不再自己调 is_wipe
            if session.status != "ended" and session.loop.state.winner:
                payload["ending"] = _finish(session, kind="wipe", turn=rec.turn)
            queue.put_nowait(("done", payload))
        except Exception as exc:  # noqa: BLE001
            queue.put_nowait(("error", {"message": str(exc)}))

    async def gen():
        task = asyncio.create_task(runner())
        try:
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if task.done() and queue.empty():
                        break
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(event, data)
                if event in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.get("/api/sessions/{sid}/characters/{cid}/profile")
def character_profile(sid: str, cid: str, request: Request) -> dict:
    """角色档案（P4）：基本 / 现状 / 行装 / 经历年表 / 成长 / 关系。

    数据全部来自已有结构：卡（身份与装备）+ 运行时（血与钱）+ `character_events`（年表）。
    **不新增存储**——以前只是没人把它们读出来。
    """
    session = _get(sid, request)
    card = session.cards.get(cid)
    rt = session.state.characters.get(cid)
    if card is None or rt is None:
        raise HTTPException(404, f"本局没有这个角色：{cid}")

    ext = card.ext
    pack = session.pack

    gear = []
    for item in ext.equipment or []:
        gear.append(
            {
                "name": str(getattr(item, "name_flavor", "") or getattr(item, "template_id", "")),
                "tier": int(getattr(item, "tier", 0) or 0),
                "quality": str(getattr(item, "quality", "") or ""),
                "tags": list(getattr(item, "tags", []) or []),
                "wear": int(getattr(item, "wear", 0) or 0),
            }
        )

    # 经历年表：重要的事标星，最多 30 条（够看清"他这一局经历了什么"）
    timeline = []
    if session.conn is not None:
        for ev in db.load_events(session.conn, sid):
            if str(ev.get("char_id")) != cid:
                continue
            timeline.append(
                {
                    "turn": ev.get("turn"),
                    "day": ev.get("day"),
                    "hour": ev.get("hour"),
                    "kind": ev.get("kind"),
                    "summary": ev.get("summary"),
                    "importance": ev.get("importance"),
                    "star": int(ev.get("importance") or 0) >= 7,
                }
            )
    timeline.sort(key=lambda x: (x.get("turn") or 0), reverse=True)

    return {
        "id": cid,
        "basic": {
            "name": card.data.name,
            "profession": ext.profession,
            "origin": ext.origin,
            "origin_label": pack.origin_label(ext.origin),
            "path": rt.path or ext.path,
            "path_label": pack.path_label(rt.path or ext.path),
            "affiliation": ext.affiliation,
            "level": ext.level,
            "xp": ext.xp,
        },
        "now": {
            "hp": rt.hp,
            "hp_max": ext.hp_max,
            "sanity": rt.sanity,
            "sanity_max": ext.sanity_max,
            "conditions": list(rt.conditions),
            "position": rt.position.value,
            "gold": rt.gold,
            "supplies": rt.supplies,
            "controller": "human" if (session.human and session.human.is_human(cid)) else "llm",
        },
        "gear": gear,
        "timeline": timeline[:30],
        "growth": {
            "skills": dict(ext.skills or {}),
            "scars": list(ext.scars or []),
            "echo": list(ext.echo or []),
            # 三期落地后这里再补「遗物」；现在先留空字段，前端不用改
            "relics": [],
        },
        "relations": {
            "reputation": dict(session.state.party.reputation or {}),
            # S5-1：NPC 关系（带时间区间的事实）—— 现在有表了，给出**此刻仍成立**的那些
            "npc": _npc_relations_of(session, cid),
        },
    }

def _npc_relations_of(session, cid: str) -> list[dict]:
    """该角色**此刻**成立的 NPC 关系（S5-1）。

    只给「仍成立」的：已失效的（valid_to 已写）是历史事实，不是现在的关系。
    没有表 / 没有关系 → 空列表（不是报错：关系是增强，不是必需）。
    """
    conn = session.conn or _new_conn()
    try:
        rows = db.load_relations_at(conn, session.session_id)
    except Exception:  # noqa: BLE001
        rows = []
    finally:
        if session.conn is None:
            conn.close()
    out: list[dict] = []
    for item in rows:
        if cid and cid not in (item.subject, item.object):
            continue
        out.append(
            {
                "subject": item.subject,
                "object": item.object,
                "kind": item.kind,
                "weight": item.weight,
                "valid_from": item.valid_from,
                "valid_to": item.valid_to,
                "source_event": item.source_event,
            }
        )
    return out


@router.post("/api/sessions/{sid}/pause")
def pause_session(sid: str, request: Request) -> dict:
    """保存并退出：把局置为 paused，之后还能从世界选择页继续。"""
    session = _get(sid, request)
    conn = session.conn or _new_conn()
    try:
        meta = db.session_meta(conn, sid)
        db.set_session_status(conn, sid, "paused")
        db.touch_session(conn, sid, meta.get("current_turn", 0), status="paused")
    finally:
        if session.conn is None:
            conn.close()
    session.status = "paused"
    return {"session_id": sid, "status": "paused", "turn": meta.get("current_turn", 0)}

@router.post("/api/sessions/{sid}/end")
def end_session(sid: str, request: Request) -> dict:
    """手动结束一局：生成结局并落库。"""
    session = _get(sid, request)
    rec = session.records[-1] if session.records else None
    turn = rec.turn if rec else 0
    payload = _finish(session, kind="ended", turn=turn, rec=rec)
    return {"session_id": sid, "kind": payload["kind"], "turn": turn, "ending": payload}

@router.get("/api/sessions/{sid}/ending")
def get_ending(sid: str, request: Request) -> dict:
    session = app.state.sessions.get(sid)
    owner = owner_of(request)
    if session is not None and session.owner != owner:
        raise HTTPException(404, f"会话不存在：{sid}")
    if session is not None and session.ending is not None:
        return {"ending": session.ending}
    conn = _new_conn()
    try:
        saved = db.load_ending(conn, sid)
    finally:
        conn.close()
    if saved is None:
        raise HTTPException(404, f"该会话还没有结算：{sid}")
    return {"ending": saved["payload"], "kind": saved["kind"], "turn": saved["turn"]}


@router.post("/api/sessions/{sid}/resume")
def resume_session(sid: str, request: Request, provider: str = "auto") -> dict:
    """从 SQLite 恢复一个旧会话（服务重启后继续玩）。"""
    owner = owner_of(request)
    existing = app.state.sessions.get(sid)
    if existing is not None:
        if existing.owner != owner:
            raise HTTPException(404, f"会话不存在：{sid}")
        return existing.snapshot()

    conn = _new_conn()
    stored_owner = db.session_owner(conn, sid)
    if stored_owner and stored_owner != owner:
        conn.close()
        raise HTTPException(404, f"会话不存在：{sid}")
    state = db.latest_state(conn, sid)
    cards = db.load_cards(conn, sid)
    if state is None or not cards:
        conn.close()
        raise HTTPException(404, f"无法恢复会话：{sid}")
    # 信念回填（S2-2）：重启后旧信念不能丢（state 快照之外还有一张历史表兜底）
    restore_beliefs(state, db.load_beliefs(conn, sid))

    meta = db.load_session_meta(conn, sid)
    world_id = meta.get("world_id") or state.world.world_id
    seed = meta.get("seed") or "42"

    pack = load_world(world_id, ROOT)
    rules = Rules(ROOT)

    # 地图：**只读已落库的那张**；没有（旧局）才补生成一次并落库
    from ...models import Exit as _Exit

    region_map = db.load_map(conn, sid, state.scene.region)
    if region_map is None:
        region_map = generate_region_map(pack, state.scene.region, seed, rules.map)
        db.save_map(
            conn,
            sid,
            state.scene.region,
            seed,
            json.dumps(region_map, ensure_ascii=False, sort_keys=True),
        )
    state.scene.exits = [_Exit(**e) for e in exits_for(region_map, state.scene.location_id)]

    store = EventStore(conn, sid)
    for item in db.load_events(conn, sid):
        store.add(CharacterEvent(session_id=sid, **item))

    vectors = build_memory(ROOT, provider, conn, sid)
    # 先把本局已有记忆回灌（否则恢复后桶是空的），再灌世界知识
    vectors.load(sid)
    seed_lore(vectors, pack)
    llm = build_llm(
        ROOT, rules, pack, cards, provider, None, seed, None,
        events=store, vectors=vectors,
    )
    # B25：把落库的短期叙事与章节摘要灌回去，否则读档等于重新睁眼
    hydrate_llm(llm, conn, sid)
    director = build_director(ROOT, rules, pack, cards, provider, seed)

    # 二期：恢复人类席位（否则重启后人类角色会被 LLM 接管）
    human_ids = [x for x in (meta.get("human_ids") or []) if x in cards]
    seat = None
    if human_ids:
        seat = HumanSeat(
            human_ids=human_ids,
            timeout=float(rules.ruleset.get("human_timeout_seconds") or 120),
        )

    loop = GameLoop(
        rules,
        cards,
        state,
        llm,
        seed=seed,
        conn=conn,
        events=store,
        vectors=vectors,
        pack=pack,
        director=director,
        human=seat,
        region_map=region_map,
    )
    # 回合号以落库的 turns 为准（记忆事件可能被压缩/裁剪，不能当计数器用）
    loop.next_turn = db.last_turn_index(conn, sid) + 1

    session = Session(
        sid=sid,
        pack=pack,
        cards=cards,
        loop=loop,
        events=store,
        conn=conn,
        human=seat,
        timeline=meta.get("timeline") or state.world.timeline,
        world_day=int(meta.get("world_day") or 1),
        owner=owner,
    )
    app.state.sessions[sid] = session
    return session.snapshot()
