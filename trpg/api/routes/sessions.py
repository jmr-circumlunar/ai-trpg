"""会话生命周期（M14）：建局、列表、查询、回滚。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from fastapi import APIRouter

from ...map import exits_for, generate_region_map, name_minor_buildings
from ...models import Exit
from ...models.state import is_eliminated
from ..deps import *  # noqa: F401,F403
from ..schemas import *  # noqa: F401,F403
from ..schemas import EffortReq  # noqa: F401

def _new_checkpointer():
    """每局一个**进程内**存档器。

    为什么不用 `AsyncSqliteSaver`：它的生命周期是 async context，与 `Session` 的
    长驻形态不匹配；而进程内存档已经足够支撑 `interrupt`（人类接管）与同进程回放。
    重启后 checkpoint 丢失，但业务状态由 `state` 快照 + `turn_receipts` 保证。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


router = APIRouter()

@router.post("/api/sessions")
def create_session(req: CreateSessionReq, request: Request) -> dict:
    owner = owner_of(request)
    rules = Rules(ROOT)
    try:
        pack = load_world(req.world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 二期：只能从已解锁的时间点开局
    timeline_id = req.timeline or pack.default_timeline
    if req.timeline and not pack.timeline_available(req.timeline):
        available = [t["id"] for t in pack.available_timelines()]
        raise HTTPException(
            400, f"时间点未解锁：{req.timeline}（可选：{', '.join(available) or '无'}）"
        )

    conn = _new_conn()
    # 一账号 × 一世界至多一个进行中局：换世界或开新局必须先「保存并退出」或「结束本局」。
    # 未认证（owner == 'local'，测试 / 离线 CLI）不适用：那是单操作员模式，没有"切换账号"概念。
    opened = (
        db.open_sessions(conn, owner, pack.world_id, statuses=("running",))
        if owner != LOCAL_OWNER
        else []
    )
    if opened:
        alive = opened[0]
        conn.close()
        raise HTTPException(
            409,
            f"《{pack.name}》还有一局没结束（第 {alive['current_turn']} 回合，"
            f"{alive['status']}）：请先保存并退出，或结束本局，再开新局",
        )

    if req.character_ids:
        repo = CharacterRepo(conn)
        picked = repo.get_many(req.character_ids, owner)
        if not picked:
            conn.close()
            raise HTTPException(400, "指定的角色都不存在，请先在角色库创建")
        # 角色卡带世界归属：血统 / 身份 / 天赋都是世界包专属 id，
        # 跨世界开会出现「铁谷局里有 S 级混血种」这种脏数据
        foreign = [
            f"{cid}（属于 {repo.world_of(cid, owner)}）"
            for cid in req.character_ids
            if repo.world_of(cid, owner) and repo.world_of(cid, owner) != pack.world_id
        ]
        if foreign:
            conn.close()
            raise HTTPException(
                400,
                f"这些角色属于别的世界包，不能在 {pack.world_id} 使用：{', '.join(foreign)}",
            )
        # 三层世界要求每个角色都有出身（血统是这个世界的前提）
        if getattr(pack, "has_origins", False):
            no_origin = [
                cid for cid, card in picked.items() if not card.ext.origin
            ]
            if no_origin:
                conn.close()
                raise HTTPException(
                    400,
                    f"这个世界是三层职业（出身 / 天赋 / 身份），这些角色没有出身：{', '.join(no_origin)}",
                )
        cards = picked
    else:
        cards = build_cards(req.players, pack, seed=req.seed)

    # P3：席位 → Agent 映射（一个 Agent 是一个人，不能在同一局占两个席位）
    agent_ids = [str(x) for x in (req.agent_ids or [])]
    dup = sorted({a for a in agent_ids if a and agent_ids.count(a) > 1})
    if dup:
        conn.close()
        raise HTTPException(400, f"同一个 Agent 不能占两个席位：{', '.join(dup)}")
    for aid in [a for a in agent_ids if a]:
        if db.get_agent(conn, aid, owner) is None:
            conn.close()
            raise HTTPException(404, f"Agent 不存在：{aid}")

    # 二期：人类席位必须指向真实在场的角色
    human_ids = [str(x) for x in (req.human_ids or [])]
    # S4-3：房间页只知道**号位**，不知道角色 id → 这里按席位顺序映射
    if req.human_seats:
        from ...rules.seats import human_seat_ids

        for cid in human_seat_ids(cards, list(req.human_seats)):
            if cid not in human_ids:
                human_ids.append(cid)
    unknown = [x for x in human_ids if x not in cards]
    if unknown:
        conn.close()
        raise HTTPException(400, f"人类席位指向不存在的角色：{', '.join(unknown)}")

    # 三期：同世界连续多局 → 世界第几天累加
    world_day = db.next_world_day(conn, pack.world_id)
    state = build_state(
        pack,
        cards,
        req.era,
        timeline=timeline_id,
        world_day=world_day,
        seed=req.seed,
        rules_map=rules.map,
    )
    # 地图：开局生成一次就落库（纯函数，同 seed 同图；resume 只读不重生成）
    try:

        region_map = generate_region_map(pack, state.scene.region, req.seed, rules.map)
        # 次要建筑命名**一次**（命名完随地图落库，之后只读；没有命名器就不命名）
        region_map, _ = name_minor_buildings(
            region_map, getattr(req, "building_namer", None)
        )
        db.save_map(
            conn,
            state.session_id,
            state.scene.region,
            req.seed,
            json.dumps(region_map, ensure_ascii=False, sort_keys=True),
        )
    except Exception:  # noqa: BLE001  地图生不出来不该拦住开局
        region_map = None

    # 二期：跨局传承——上一局的结局转成本局的开场传闻
    # 三期：世界账本——已死的人不再出现
    try:
        dead = world_ledger.dead_npcs(conn, pack.world_id)
        if dead:
            import dataclasses

            pack = dataclasses.replace(
                pack, npcs=[n for n in pack.npcs if str(n.get("id")) not in dead]
            )
    except Exception:  # noqa: BLE001
        pass

    legacy = _legacy_block(conn, pack, req.inherit_from or "")
    if req.inherit_from and legacy:
        state.party.flags.append(f"legacy:{req.inherit_from}")

    db.save_session(
        conn,
        state.session_id,
        req.seed,
        pack.world_id,
        state.world.era,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        name=f"{req.provider} 局",
        timeline=state.world.timeline,
        human_ids=human_ids,
        world_day=world_day,
        account_id=owner,
        agent_ids=agent_ids,
    )
    # 配置一旦用于开局就锁死：世界 / 席位 / 模型都不许再改（服务端强制）
    db.lock_config(conn, state.session_id)

    # 三期：局外成长的「带入」——把允许携带的元成长注入本局角色卡
    try:
        meta_hooks = load_hooks(pack.root, pack.world_id)
        if meta_hooks.carry:
            wallet = Wallet(conn, owner_id=owner)
            for card in cards.values():
                meta_carry.apply_carry(meta_hooks, wallet, card, pack)
    except Exception:  # noqa: BLE001
        pass

    db.save_cards(conn, state.session_id, cards)
    db.save_state(conn, state)

    store = EventStore(conn, state.session_id)
    vectors = build_memory(ROOT, req.provider, conn, state.session_id)
    seed_lore(vectors, pack)
    llm = build_llm(
        ROOT,
        rules,
        pack,
        cards,
        req.provider,
        req.model,
        req.seed,
        req.reasoning_effort,
        events=store,
        vectors=vectors,
        style=req.style or None,
        legacy=legacy,
    )
    director = build_director(
        ROOT,
        rules,
        pack,
        cards,
        req.provider,
        req.seed,
        req.model,
        req.reasoning_effort,
        style=req.style or None,
    )

    # 二期：人类席位（无人类时不建，避免多一层判断）
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
        seed=req.seed,
        conn=conn,
        events=store,
        vectors=vectors,
        pack=pack,
        director=director,
        human=seat,
        region_map=region_map,
        # 进程内存档：够支撑 `interrupt` 的人类接管与同进程回放。
        # 重启后 checkpoint 丢失，但业务状态由 `state` 快照 + `turn_receipts` 保证。
        checkpointer=_new_checkpointer(),
        thread_id=state.session_id,
    )

    session = Session(
        sid=state.session_id,
        pack=pack,
        cards=cards,
        loop=loop,
        events=store,
        conn=conn,
        human=seat,
        timeline=state.world.timeline,
        inherit_from=req.inherit_from or "",
        world_day=world_day,
        owner=owner,
    )
    # 经验接线：定下 char→agent 映射，再把历史经验读进 prompt。
    # 顺序不能反——先知道「谁是谁」，才知道该读谁的经验。
    session.bind_agents(req.agent_ids)
    session.load_experience()

    app.state.sessions[session.sid] = session
    return session.snapshot()

@router.get("/api/sessions")
def list_saved_sessions(request: Request) -> dict:
    conn = _new_conn()
    try:
        return {"sessions": db.list_sessions(conn, owner=owner_of(request))}
    finally:
        conn.close()

@router.get("/api/sessions/{sid}")
def get_session(sid: str, request: Request, viewer: str | None = None) -> dict:
    """会话快照。`viewer` 是当前查看者的角色 id（不传 = 上帝视角）。"""
    return _get(sid, request).snapshot(viewer)


@router.patch("/api/sessions/{sid}")
def rename_session(sid: str, req: SessionRenameReq, request: Request) -> dict:
    """给这局改名——存档槽的「命名存档」。

    名字落库（`sessions.name`），不再靠浏览器本地存：本地存换设备就没了，
    而且大厅/存档页/对局页会各存一份、彼此对不上。
    """
    from fastapi import HTTPException

    owner = owner_of(request)
    conn = _new_conn()
    try:
        stored_owner = db.session_owner(conn, sid)
        if stored_owner and stored_owner != owner:
            raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")
        db.set_session_name(conn, sid, req.name)
    finally:
        conn.close()
    return {"session_id": sid, "name": req.name}


@router.get("/api/sessions/{sid}/identity/{cid}")
def get_identity(sid: str, cid: str, request: Request, viewer: str | None = None) -> dict:
    """某个角色的身份信息——按世界包的 `secrets` 矩阵裁剪（C56）。

    | 看的人 | 职业 | 阵营 |
    |---|---|---|
    | 本人 | ✅ | ✅ |
    | 同阵营（`secrets.camp: self_and_camp`） | ❌ | ✅ |
    | 其他人 / 上帝视角 | ✅ | ✅ |

    阵营**永不**随快照下发（`snapshot()` 不含 camp），只能从这里按可见性拿。
    """
    from fastapi import HTTPException

    session = _get(sid, request)
    runtime = session.state.characters.get(cid)
    if runtime is None:
        raise HTTPException(status_code=404, detail="没有这个角色")

    card = session.cards.get(cid)
    secrets = session._secrets()
    prof_mode = str(secrets.get("profession") or "all")
    camp_mode = str(secrets.get("camp") or "self")

    is_self = viewer is not None and viewer == cid
    is_god = viewer is None
    viewer_camp = ""
    if viewer is not None:
        viewer_rt = session.state.characters.get(viewer)
        viewer_camp = str(getattr(viewer_rt, "camp", "") or "") if viewer_rt else ""
    same_camp = bool(viewer_camp) and viewer_camp == str(getattr(runtime, "camp", "") or "")

    profession_visible = is_god or is_self or prof_mode != "self"
    camp_visible = is_god or is_self or (camp_mode == "self_and_camp" and same_camp)

    return {
        "char_id": cid,
        "name": card.data.name if card is not None else "",
        "profession": str(card.ext.profession or "") if profession_visible and card else "",
        "camp": str(getattr(runtime, "camp", "") or "") if camp_visible else "",
        "camp_revealed": bool(getattr(runtime, "camp_revealed", False)),
        "out": is_eliminated(runtime),
        "visible": {
            "profession": profession_visible,
            "camp": camp_visible,
        },
    }


def _card_url(session, image_name: str) -> str:
    """牌面 URL：资产地址由后端生成（前端不拼路径）。

    白名单来自世界包的 `ui.card_images`，取不到就返回空串（前端不显示底图）。
    """
    name = str(image_name or "").strip()
    if not name:
        return ""
    world_id = str(
        getattr(session, "world_id", "")
        or getattr(getattr(session, "pack", None), "world_id", "")
        or ""
    )
    if not world_id:
        return ""
    return f"/api/worlds/{world_id}/cards/{name}"


@router.get("/api/sessions/{sid}/table")
def get_table(sid: str, request: Request, viewer: str | None = None) -> dict:
    """牌桌（上帝视角用）：一次拿全「谁是谁、还活着吗、手上还有什么」。

    为什么单独一个端点：`snapshot()` 是**共享**快照，按 C56 永远不带 `camp`；
    而牌桌本来就是全知用途（未传 viewer / 已出局者）。非观察者调用时按同一套
    `secrets` 规则裁剪：别人的职业、阵营、笔记、资源都拿不到。
    """
    from ...models.state import is_alive

    session = _get(sid, request)
    god = _is_observer(session, viewer)
    rows = []
    card_images = dict((getattr(session.pack, "ui", {}) or {}).get("card_images") or {})
    camp_labels = dict(getattr(session.pack, "camp_labels", {}) or {})
    for cid, rt in session.state.characters.items():
        card = session.cards.get(cid)
        mine = viewer == cid
        profession = str(card.ext.profession or "") if card is not None else ""
        rows.append(
            {
                "id": cid,
                "name": (card.data.name if card is not None else cid),
                "personality": (card.data.personality if card is not None else ""),
                "profession": profession if (god or mine) else "",
                # 牌面图由**世界包**声明（`ui.card_images`）：前端是通用层，
                # 不该知道「狼人该配哪张图」这种世界语义。
                "card_image": card_images.get(profession, "") if (god or mine) else "",
                # 牌面 **URL** 由后端给（前端不拼路径、不做映射）
                "card_url": _card_url(session, card_images.get(profession, "")),
                "camp": str(getattr(rt, "camp", "") or "") if god else "",
                # 阵营显示名由世界包声明（`camp_labels`）：前端是通用层
                "camp_label": (
                    camp_labels.get(str(getattr(rt, "camp", "") or ""), "") if god else ""
                ),
                "alive": bool(is_alive(rt)),
                "out": is_eliminated(rt),
                "notes": list(rt.notes or []) if (god or mine) else [],
                "charges": dict(rt.skill_charges or {}) if (god or mine) else {},
            }
        )
    return {
        "turn": len(getattr(session, "records", []) or []),
        "winner": str(session.state.winner or ""),
        "characters": rows,
    }


@router.post("/api/sessions/{sid}/effort")
def set_session_effort(sid: str, req: "EffortReq", request: Request) -> dict:
    """切换这一局的**思考档位**（对局页顶栏）。

    一档同时改三件事：
    - **思考深度**（`reasoning_effort`）：模型想多深；
    - **输出预算**（`max_tokens`）：它能写多长；
    - **推理步数**（`steps`）：ReAct 最多来回想几轮（工具调用算一轮）。

    档位定义在 `config/providers.yaml: efforts`，不在代码里写死。
    """
    session = _get(sid, request)
    # llm 挂在 loop 上（`loop.llm`），session 本身没有这个属性
    llm = getattr(getattr(session, "loop", None), "llm", None)
    config = getattr(llm, "config", None)
    if config is None:
        # mock 之类的替身没有模型配置：档位记下来也不影响它（照常返回，不报错）
        return {"effort": req.effort, "applied": {}, "options": [], "mock": True}
    applied = config.set_effort(req.effort)
    return {
        "effort": req.effort,
        "applied": applied,
        "options": config.effort_names(),
    }


@router.get("/api/sessions/{sid}/config")
def get_session_config(sid: str, request: Request) -> dict:
    """本局能力清单（C52）：前端据此隐藏/显示区块，避免「能启动但页面像坏了」。"""
    from ...rulespack import assemble

    session = _get(sid, request)
    pack = getattr(getattr(session, "loop", None), "pack", None)
    caps = assemble(pack) if pack is not None else assemble(None)

    return {
        "world": getattr(pack, "world_id", ""),
        "systems": sorted(caps.enabled),
    }


@router.post("/api/sessions/{sid}/reset")
def reset_session(sid: str, request: Request, to_turn: int = 0, provider: str = "auto") -> dict:
    """故事线重置：回滚到第 N 回合结束时的状态，删掉之后的痕迹。"""
    session = _get(sid, request)
    conn = session.conn or _new_conn()
    state = db.state_at_turn(conn, sid, to_turn)
    if state is None:
        raise HTTPException(400, f"没有第 {to_turn} 回合及之前的快照，无法回滚")

    conn.execute("DELETE FROM turns WHERE session_id = ? AND idx > ?", (sid, to_turn))
    conn.execute("DELETE FROM dice_rolls WHERE session_id = ? AND turn > ?", (sid, to_turn))
    conn.execute(
        "DELETE FROM character_events WHERE session_id = ? AND turn > ?", (sid, to_turn)
    )
    conn.execute(
        "DELETE FROM memory_vectors WHERE session_id = ? AND turn > ?", (sid, to_turn)
    )
    # B25：摘要也要跟着回滚，否则读档会看到「还没发生」的回顾
    conn.execute(
        "DELETE FROM session_summaries WHERE session_id = ? AND upto_turn > ?",
        (sid, to_turn),
    )
    conn.execute("DELETE FROM endings WHERE session_id = ?", (sid,))
    conn.commit()
    db.set_session_status(conn, sid, "running")

    cards = db.load_cards(conn, sid) or session.cards
    rules = Rules(ROOT)
    row = conn.execute("SELECT seed FROM sessions WHERE id = ?", (sid,)).fetchone()
    seed = row[0] if row else "42"

    store = EventStore(conn, sid)
    for item in db.load_events(conn, sid):
        store.add(CharacterEvent(session_id=sid, **item))
    vectors = build_memory(ROOT, provider, conn, sid)
    # 回滚后同样要重建：先把保留下来的记忆回灌，再灌世界知识
    vectors.load(sid)
    seed_lore(vectors, session.pack)
    llm = build_llm(
        ROOT, rules, session.pack, cards, provider, None, seed, None,
        events=store, vectors=vectors,
    )
    # B25：回滚后同样回灌（截断到 to_turn 的叙事与摘要）
    hydrate_llm(llm, conn, sid)
    director = build_director(ROOT, rules, session.pack, cards, provider, seed)
    # 回滚**不动地图**：读已落库的那张；没有（旧局）才补生成一次

    region_map = db.load_map(conn, sid, state.scene.region) or generate_region_map(
        session.pack, state.scene.region, seed, rules.map
    )
    state.scene.exits = [Exit(**e) for e in exits_for(region_map, state.scene.location_id)]
    loop = GameLoop(
        rules,
        cards,
        state,
        llm,
        seed=seed,
        conn=conn,
        events=store,
        vectors=vectors,
        pack=session.pack,
        director=director,
        region_map=region_map,
        checkpointer=_new_checkpointer(),
        thread_id=sid,
    )
    loop.next_turn = int(to_turn) + 1
    loop._opened = True  # 已有主线，不重新开局

    fresh = Session(
        sid=sid,
        pack=session.pack,
        cards=cards,
        loop=loop,
        events=store,
        conn=conn,
    )
    # 回滚到某一回合也是「新的一局」：映射与经验照样要挂上
    fresh.bind_agents(None)
    fresh.load_experience()

    app.state.sessions[sid] = fresh
    return {"reset_to": to_turn, "state": fresh.snapshot()}
