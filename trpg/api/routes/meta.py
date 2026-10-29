"""局外成长（点数 / 价目表）（M14）。

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

@router.get("/api/meta")
def get_meta(request: Request, world: str = "", owner: str = "") -> dict:
    """局外成长：余额 + 本世界可买项 + 流水。`owner` 显式传参仅供 CLI 调试用。"""
    owner = owner or owner_of(request)
    rules = Rules(ROOT)
    try:
        pack = load_world(world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc
    hooks = load_hooks(pack.root, pack.world_id)
    conn = _new_conn()
    try:
        wallet = Wallet(conn, owner_id=owner)
        return {
            "world": pack.world_id,
            "currency": {"key": hooks.currency_key, "label": hooks.currency_label},
            "balance": wallet.balance(hooks.currency_key),
            "spend": meta_shop.listing(hooks, pack, set(rules.skill_attr.keys()), wallet),
            "earn_rules": hooks.earn_rules(),
            "entries": wallet.entries(),
            "ledger": wallet.ledger(20),
        }
    finally:
        conn.close()


@router.post("/api/meta/spend")
def spend_meta(payload: dict, request: Request) -> dict:
    """局外消费：点数 → 角色卡上的东西（技能 / 天赋 / 痕迹）。"""
    owner = owner_of(request)
    world = str(payload.get("world") or "")
    cid = str(payload.get("character_id") or "")
    kind = str(payload.get("kind") or "")
    target = str(payload.get("target") or "")
    if not cid or not kind or not target:
        raise HTTPException(400, "需要 character_id / kind / target")

    rules = Rules(ROOT)
    try:
        pack = load_world(world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc
    hooks = load_hooks(pack.root, pack.world_id)
    conn = _new_conn()
    try:
        repo = CharacterRepo(conn)
        card = repo.get(cid, owner)
        if card is None:
            raise HTTPException(404, f"角色不存在：{cid}")
        wallet = Wallet(conn, owner_id=owner)
        result = meta_shop.purchase(
            hooks, wallet, pack, set(rules.skill_attr.keys()), card, kind, target
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "购买失败")
        repo.save(card, cid, world_id=pack.world_id, owner=owner)
        result["balance"] = wallet.balance(hooks.currency_key)
        result["character_id"] = cid
        return result
    finally:
        conn.close()
