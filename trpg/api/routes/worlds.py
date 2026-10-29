"""世界包信息与角色模板查询（M14）。

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

@router.get("/api/worlds")
def list_worlds() -> dict:
    out = []
    for wid in discover_worlds(ROOT):
        try:
            pack = load_world(wid, ROOT)
        except WorldPackError:
            continue
        out.append(
            {
                "id": pack.world_id,
                "name": pack.name,
                "tone": pack.tone,
                "eras": [
                    {"id": e.id, "label": e.label, "tier_max": e.tier_max}
                    for e in pack.eras.values()
                ],
                "regions": pack.regions,
                "default_timeline": pack.default_timeline,
                # 二期：可选时间点（只有 available 的能开局）
                "timelines": [
                    {
                        "id": str(t.get("id")),
                        "label": str(t.get("label") or t.get("id")),
                        "era": str(t.get("era") or pack.default_era),
                        "available": bool(t.get("available")),
                        "premise": str(t.get("premise") or "")[:160],
                    }
                    for t in pack.timeline
                ],
                "counts": {
                    "seeds": len(pack.seeds),
                    "items": len(pack.items),
                    "npcs": len(pack.npcs),
                },
                # 大厅用：人数区间（界面铺号位，不再给人数下拉）与界面声明（布局 + 主题）
                "players": dict(pack.players or {}),
                "ui": dict(pack.ui or {}),
            }
        )
    return {"worlds": out}


@router.get("/api/me/worlds")
def my_worlds(request: Request) -> dict:
    """我的世界列表：每个世界的进行中局 / 历史局数 / 上次游玩。世界选择页用。"""
    owner = owner_of(request)
    conn = _new_conn()
    try:
        stats = db.world_session_stats(conn, owner)
        # 每个世界给一个「继续」目标：running 优先，其次最新的 paused
        rows = sorted(
            db.open_sessions(conn, owner),
            key=lambda s: (s["status"] == "running", s["updated_at"]),
            reverse=True,
        )
        opened: dict[str, dict] = {}
        for row in rows:
            opened.setdefault(row["world_id"], row)
    finally:
        conn.close()

    out = []
    for wid in discover_worlds(ROOT):
        try:
            pack = load_world(wid, ROOT)
        except WorldPackError:
            continue
        item = stats.get(wid) or {}
        out.append(
            {
                "id": wid,
                "name": pack.name,
                "tone": (pack.tone or "").strip()[:160],
                "open": opened.get(wid),
                "history": int(item.get("ended", 0)),
                "last_played": item.get("last_played", ""),
                "players": dict(pack.players or {}),
                "ui": dict(pack.ui or {}),
            }
        )
    return {"worlds": out}


@router.get("/api/personas")
def list_personas() -> dict:
    """全局人格库（`config/agents.yaml`）：agent 就是「人」，跨世界共用。

    路径叫 `personas` 而不是 `agents`：`/api/agents` 已经是**玩家自建 agent** 的
    CRUD（`routes/characters.py`，按 owner 隔离），两者不是一回事，不能共用一个路径。

    开局设置页按它铺「号位 × agent」；世界包与身份都不参与。
    """
    from ...config.loader import config_path, load_yaml

    doc = load_yaml(config_path(ROOT, "agents"), {}) or {}
    agents = list(doc.get("agents") or [])
    return {"agents": agents, "random": bool(doc.get("agents_random", True))}


@router.get("/api/worlds/{world_id}/saves")
def world_saves(world_id: str, request: Request) -> dict:
    """某个世界的存档槽：**1 个自动 + 5 个命名**（槽位规则由后端定，前端只渲染）。

    - `auto`：最近的一局（进行中优先）——「继续」的目标；
    - `slots`：按更新时间倒序的接下来 5 局，名字取 `sessions.name`（空则「槽 N」）；
    - `extra`：再往前的历史局数量（只给计数，不展开——列表长了就没人看了）；
    - `saves[]`：保留旧字段，兼容还没升级的前端。
    """
    owner = owner_of(request)
    conn = _new_conn()
    try:
        rows = db.list_sessions(conn, owner=owner)
    finally:
        conn.close()

    saves = [row for row in rows if str(row.get("world_id") or "") == world_id]
    # 进行中的排前面（那是「继续」的目标），其余按更新时间倒序
    ordered = sorted(saves, key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    ordered.sort(key=lambda row: 0 if str(row.get("status") or "") == "running" else 1)

    auto = ordered[0] if ordered else None
    slots = [
        {
            "index": index,
            "name": str(row.get("name") or f"槽 {index}"),
            "session": row,
        }
        for index, row in enumerate(ordered[1:6], start=1)
    ]
    while len(slots) < 5:
        index = len(slots) + 1
        slots.append({"index": index, "name": f"槽 {index}", "session": None})

    return {
        "world": world_id,
        "auto": auto,
        "slots": slots,
        "extra": max(0, len(ordered) - 6),
        "saves": saves,
    }


@router.get("/api/worlds/{world_id}/cards/{name}")
def world_card(world_id: str, name: str):
    """身份牌面图（世界包自带的美术资源）：`ui/cards/<name>.jpg`。

    只允许**固定文件名**（白名单）：世界包不能借这个接口读服务器上的任意文件。
    没有这张牌就 404，前端回落到纯色牌面。
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    # 可用的牌名由**世界包声明**（`ui.card_images`）+ 通用牌背 `back`。
    # 框架里不能硬编码任何世界词（`check_generic` 会拦），所以这里动态取。
    try:
        pack = load_world(world_id, ROOT)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="没有这个世界") from None
    allowed = {"back"} | {
        str(item) for item in ((pack.ui or {}).get("card_images") or {}).values()
    }
    if name not in allowed:
        raise HTTPException(status_code=404, detail="没有这张牌")
    path = ROOT / "worlds" / world_id / "ui" / "cards" / f"{name}.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="这个世界没有这张牌")
    return FileResponse(path, media_type="image/jpeg")


def _world_agents(conn, owner: str, world_id: str) -> list[dict]:
    """本世界用过的 agent + 各自在这个世界积累的经验条数（内部实现，两个端点共用）。

    归属原则：**agent 是什么**是全局配置（`/api/agents`，选世界之前配好）；
    **agent 在这个世界学到了什么**是世界级的，所以放在世界首页与上下文里。
    """
    from ...storage.experience import count_experience

    rows = [
        row
        for row in db.list_sessions(conn, owner)
        if str(row.get("world_id") or "") == world_id
    ]
    used: list[str] = []
    for row in rows:
        meta = db.load_session_meta(conn, str(row.get("session_id") or ""))
        for aid in meta.get("agent_ids") or []:
            aid = str(aid)
            if aid and aid not in used:
                used.append(aid)

    catalog = {str(item["id"]): item for item in db.list_agents(conn, owner)}
    out = []
    for aid in used:
        agent = catalog.get(aid)
        if agent is None:
            continue
        out.append(
            {
                "id": aid,
                "name": str(agent.get("name") or ""),
                "persona": str(agent.get("persona") or ""),
                "voice": str(agent.get("voice") or ""),
                "experience": int(count_experience(conn, aid, world_id)),
            }
        )
    return out


@router.get("/api/worlds/{world_id}/agents")
def world_agents(world_id: str, request: Request) -> dict:
    """本世界的 agent 三份信息：

    - `roster`：本世界**参战名单**（配置页维护，世界首页只读展示）；
    - `available`：全局 agent 库里这个账号的人（配置页可勾选）；
    - `used`：这个世界上**已经用过**的人 + 各自在本世界积累的经验条数。

    人格本身（名字/性格/说话风格）只在全局 Agent 库编辑，这里只读。
    """
    owner = owner_of(request)
    conn = _new_conn()
    try:
        agents = _world_agents(conn, owner, world_id)
        roster_ids = [item["agent_id"] for item in db.list_world_agents(conn, world_id, owner)]
        catalog = db.list_agents(conn, owner)
    finally:
        conn.close()

    by_id = {str(item["id"]): item for item in catalog}
    roster = []
    for agent_id in roster_ids:
        agent = by_id.get(agent_id)
        if agent is None:
            continue
        roster.append(
            {
                "id": agent_id,
                "name": str(agent.get("name") or ""),
                "persona": str(agent.get("persona") or ""),
                "voice": str(agent.get("voice") or ""),
            }
        )

    available = [
        {
            "id": str(item["id"]),
            "name": str(item.get("name") or ""),
            "persona": str(item.get("persona") or ""),
            "voice": str(item.get("voice") or ""),
        }
        for item in catalog
    ]

    return {
        "world": world_id,
        "roster": roster,
        "available": available,
        "agents": agents,
    }


@router.put("/api/worlds/{world_id}/agents")
def set_world_agents(world_id: str, req: WorldAgentsReq, request: Request) -> dict:
    """设置本世界参战名单（整体替换）。

    只接受**该账号自己的** agent 档案 id——别人的 agent 不能被拉进你的局。
    """
    owner = owner_of(request)
    conn = _new_conn()
    try:
        catalog = {str(item["id"]) for item in db.list_agents(conn, owner)}
        wanted = []
        for agent_id in req.agent_ids or []:
            agent_id = str(agent_id).strip()
            if agent_id and agent_id in catalog and agent_id not in wanted:
                wanted.append(agent_id)
        db.set_world_agents(conn, world_id, owner, wanted)
    finally:
        conn.close()
    return {"world": world_id, "roster": wanted}


@router.get("/api/worlds/{world_id}/hero")
def world_hero(world_id: str):
    """世界首页背景图（可选）：`worlds/<id>/ui/hero.{png,jpg,webp}`。

    没有图就 404，前端回落到主题色渐变——美术资源属于内容，不给世界包强制要求。
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    base = ROOT / "worlds" / world_id / "ui"
    for name, media in (("hero.webp", "image/webp"), ("hero.jpg", "image/jpeg"), ("hero.png", "image/png")):
        path = base / name
        if path.exists():
            return FileResponse(path, media_type=media)
    raise HTTPException(status_code=404, detail="这个世界没有首页背景图")


@router.get("/api/worlds/{world_id}/theme.css")
def world_theme(world_id: str):
    """世界包自带主题（`worlds/<id>/ui/theme.css`）。

    只读**固定文件名**：世界包不能指定路径，避免拿 `ui.theme` 去读服务器任意文件。
    世界没主题就 404，前端回落到默认暗色。
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    path = ROOT / "worlds" / world_id / "ui" / "theme.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="这个世界没有自带主题")
    return FileResponse(path, media_type="text/css")

@router.get("/api/worlds/{world_id}/professions")
def list_professions(world_id: str) -> dict:
    try:
        pack = load_world(world_id, ROOT)
    except WorldPackError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "world": pack.world_id,
        "professions": [
            {
                "id": p.get("id"),
                "label": p.get("label") or p.get("id"),
                "description": p.get("description", ""),
                "stats": p.get("stats") or {},
                "skills": p.get("skills") or {},
            }
            for p in pack.profession_pool()
        ],
    }


@router.get("/api/worlds/{world_id}/origins")
def list_origins(world_id: str) -> dict:
    """三层职业世界的出身池、天赋池与身份阶梯（角色页 / GM 页用）。"""
    try:
        pack = load_world(world_id, ROOT)
    except WorldPackError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "world": pack.world_id,
        "origins": [
            {
                "id": o.get("id"),
                "label": o.get("label") or o.get("id"),
                "weight": int(o.get("weight") or 0),
                "description": o.get("description", ""),
                "personality": o.get("personality", ""),
                "goals": o.get("private_goals") or [],
                "talents": o.get("talents") or [],
                "stats_mod": o.get("stats_mod") or {},
                "cost": o.get("cost", ""),
            }
            for o in pack.origins
        ],
        "talents": pack.talent_pool,
        "paths": [
            {
                "id": p.get("id"),
                "label": p.get("label") or p.get("id"),
                "description": p.get("description", ""),
                "default": bool(p.get("default")),
                "granted_by": p.get("granted_by") or [],
                "requirements": p.get("requirements") or {},
                "grants": p.get("grants") or {},
            }
            for p in pack.paths
        ],
        "path_default": pack.path_default(),
    }



@router.get("/api/worlds/{world_id}/context")
def world_context(world_id: str, request: Request) -> dict:
    """**世界上下文**：这一屏渲染所需的全部世界侧数据，一次给全。

    这是「世界包驱动渲染」的落点（C62 / C63）：
    - **所有资产 URL 由后端生成**（前端不再拼 `/cards/xxx`、`/theme.css`、`/hero`）；
    - 牌面白名单与 `GET /api/worlds/{id}/cards/{name}` **同源**（同一份 `ui.card_images`）；
    - `capabilities` 复用 `rulespack.assemble(pack)`（与 `/config` 同源，不另造一份）；
    - `agents` 复用 `_world_agents()`（与 `/agents` 端点同一份实现）。

    页面只消费这一份 → 换世界时整页跟着换，且不会出现「半新半旧」。
    """
    from fastapi import HTTPException

    from ...rulespack import assemble

    try:
        pack = load_world(world_id, ROOT)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="没有这个世界") from None

    ui = dict(getattr(pack, "ui", {}) or {})
    card_images = {str(k): str(v) for k, v in (ui.get("card_images") or {}).items()}
    # 牌背是通用件（`back`），身份牌面由世界包声明——白名单与 `/cards/{name}` 同源
    card_ids = sorted(set(card_images.values()) | {"back"})

    ui_dir = ROOT / "worlds" / world_id / "ui"
    assets: dict[str, str] = {}
    if any((ui_dir / f"hero{ext}").exists() for ext in (".jpg", ".png", ".webp")):
        assets["hero"] = f"/api/worlds/{world_id}/hero"

    owner = owner_of(request)
    conn = _new_conn()
    try:
        agents = _world_agents(conn, owner, world_id)
    finally:
        conn.close()

    return {
        "world": {
            "id": pack.world_id,
            "name": pack.name,
            "tone": (pack.tone or "")[:200],
        },
        "ui": {
            "layout": str(ui.get("layout") or "flow"),
            "theme": str(ui.get("theme") or ""),
            "theme_url": f"/api/worlds/{world_id}/theme.css"
            if (ui_dir / "theme.css").exists()
            else "",
            "assets": assets,
            "cards": [
                {"id": card_id, "url": f"/api/worlds/{world_id}/cards/{card_id}"}
                for card_id in card_ids
            ],
            "card_images": card_images,
            "camp_labels": dict(getattr(pack, "camp_labels", {}) or {}),
        },
        "players": dict(getattr(pack, "players", {}) or {}),
        "agents": agents,
        "capabilities": {"enabled": sorted(assemble(pack).enabled)},
    }
