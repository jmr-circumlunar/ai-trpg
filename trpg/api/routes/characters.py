"""角色库、Agent 席位配置（M14）。

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

@router.get("/api/characters")
def list_characters(request: Request) -> dict:
    conn = _new_conn()
    try:
        return {"characters": CharacterRepo(conn).list(owner_of(request))}
    finally:
        conn.close()


@router.get("/api/characters/{cid}")
def get_character(cid: str, request: Request) -> dict:
    conn = _new_conn()
    try:
        repo = CharacterRepo(conn)
        card = repo.get(cid, owner_of(request))
        if card is None:
            raise HTTPException(404, f"角色不存在：{cid}")
        return {"id": cid, "card": card.model_dump()}
    finally:
        conn.close()


@router.post("/api/characters")
def create_character(form: CharacterForm, request: Request) -> dict:
    conn = _new_conn()
    try:
        try:
            pack = load_world(form.world or _default_world(), ROOT)
        except WorldPackError:
            pack = None
        card = _card_from_form(pack, form)
        cid = CharacterRepo(conn).save(
            card, world_id=getattr(pack, "world_id", "") or "", owner=owner_of(request)
        )
        return {"id": cid, "card": card.model_dump()}
    finally:
        conn.close()


@router.put("/api/characters/{cid}")
def update_character(cid: str, form: CharacterForm, request: Request) -> dict:
    conn = _new_conn()
    try:
        repo = CharacterRepo(conn)
        owner = owner_of(request)
        if repo.get(cid, owner) is None:
            raise HTTPException(404, f"角色不存在：{cid}")
        try:
            pack = load_world(form.world or _default_world(), ROOT)
        except WorldPackError:
            pack = None
        card = _card_from_form(pack, form)
        repo.save(card, cid, world_id=getattr(pack, "world_id", "") or "", owner=owner)
        return {"id": cid, "card": card.model_dump()}
    finally:
        conn.close()


@router.delete("/api/characters/{cid}")
def delete_character(cid: str, request: Request) -> dict:
    conn = _new_conn()
    try:
        ok = CharacterRepo(conn).delete(cid, owner_of(request))
        if not ok:
            raise HTTPException(404, f"角色不存在：{cid}")
        return {"deleted": cid}
    finally:
        conn.close()


@router.post("/api/characters/presets")
def seed_presets(request: Request, world: str = "", players: int = 6, seed: str = "") -> dict:
    """把世界包的预设小队导入角色库（首次使用时点一下即可）。"""
    try:
        pack = load_world(world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(404, str(exc)) from exc
    cards = preset_cards(players, pack=pack, seed=seed or None)
    conn = _new_conn()
    try:
        repo = CharacterRepo(conn)
        owner = owner_of(request)
        ids = [repo.save(card, world_id=pack.world_id, owner=owner) for card in cards.values()]
        return {"ids": ids, "count": len(ids)}
    finally:
        conn.close()


# ── Agent（人）──────────────────────────────────────────────

def _seed_default_agents(conn, owner: str) -> int:
    """把全局人格库（`config/agents.yaml`）落成该 owner 的默认 agent 档案。

    为什么需要：`agent_ids` 只能指向**库里的 agent 档案**（`db.get_agent`），
    而新账号一条都没有 → 房间页无可选的人、开局必报「Agent 不存在」。
    人格库是"出厂就有的人"，所以首次访问时按名字补齐（幂等：同名跳过）。
    """
    from ...config.loader import config_path, load_yaml

    try:
        doc = load_yaml(config_path(ROOT, "agents"), {}) or {}
    except Exception:  # noqa: BLE001
        return 0
    entries = list(doc.get("agents") or [])
    if not entries:
        return 0

    existing = {str(item.get("name") or "") for item in db.list_agents(conn, owner)}
    created = 0
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name or name in existing:
            continue
        persona = str(entry.get("persona") or "")
        traits = entry.get("traits") or []
        if isinstance(traits, list) and traits:
            persona = f"{'、'.join(str(t) for t in traits)}：{persona}"
        db.save_agent(
            conn,
            name=name,
            persona=persona,
            voice=str(entry.get("voice") or ""),
            default_model_json="",
            owner=owner,
        )
        created += 1
    return created


@router.get("/api/agents")
def list_agents_api(request: Request) -> dict:
    conn = _new_conn()
    try:
        owner = owner_of(request)
        # 首次访问补齐「出厂的人」（幂等）——否则房间页空、开局报 Agent 不存在
        _seed_default_agents(conn, owner)
        return {"agents": db.list_agents(conn, owner)}
    finally:
        conn.close()


@router.post("/api/agents")
def create_agent_api(req: AgentReq, request: Request) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Agent 需要一个名字")
    conn = _new_conn()
    try:
        aid = db.save_agent(
            conn,
            name=name,
            persona=req.persona,
            voice=req.voice,
            default_model_json=req.default_model_json,
            owner=owner_of(request),
        )
        return {"id": aid, "agent": db.get_agent(conn, aid)}
    finally:
        conn.close()


@router.put("/api/agents/{aid}")
def update_agent_api(aid: str, req: AgentReq, request: Request) -> dict:
    conn = _new_conn()
    try:
        owner = owner_of(request)
        if db.get_agent(conn, aid, owner) is None:
            raise HTTPException(404, f"Agent 不存在：{aid}")
        db.save_agent(
            conn,
            name=req.name.strip() or aid,
            persona=req.persona,
            voice=req.voice,
            default_model_json=req.default_model_json,
            agent_id=aid,
            owner=owner,
        )
        return {"id": aid, "agent": db.get_agent(conn, aid, owner)}
    finally:
        conn.close()


@router.delete("/api/agents/{aid}")
def delete_agent_api(aid: str, request: Request) -> dict:
    conn = _new_conn()
    try:
        if not db.delete_agent(conn, aid, owner_of(request)):
            raise HTTPException(404, f"Agent 不存在：{aid}")
        return {"deleted": aid}
    finally:
        conn.close()

@router.post("/api/characters/roll")
def roll_character(request: Request, world: str = "", seed: str = "") -> dict:
    """随机生成**一张**角色卡并入角色库（席位里点「🎲 随机」）。同 seed 可复现。"""
    try:
        pack = load_world(world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc
    used_seed = seed.strip() or uuid.uuid4().hex[:6]
    cards = build_cards(1, pack, seed=used_seed)
    card = next(iter(cards.values()))
    conn = _new_conn()
    try:
        cid = CharacterRepo(conn).save(card, world_id=pack.world_id, owner=owner_of(request))
        return {"id": cid, "seed": used_seed, "card": card.model_dump()}
    finally:
        conn.close()


# ── 会话 ────────────────────────────────────────────────────
