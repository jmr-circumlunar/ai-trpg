"""物品候选与晋升补丁（M14）。

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

@router.get("/api/items/candidates")
def item_candidates(world: str = "") -> dict:
    """物品晋升候选：使用次数达标的道具（人工确认后才写回世界包）。"""
    rules = Rules(ROOT)
    try:
        pack = load_world(world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc
    promo = rules.equipment.get("promotion") or {}
    min_uses = int(promo.get("prop_to_instance") or 3)
    min_sessions = int(promo.get("instance_to_template") or 3)
    conn = _new_conn()
    try:
        rows = db.item_usage_candidates(conn, pack.world_id)
        out = []
        for row in rows:
            level = ""
            if row["sessions"] >= min_sessions:
                level = "template"
            elif row["uses"] >= min_uses:
                level = "instance"
            if level:
                out.append({**row, "level": level})
        return {
            "world": pack.world_id,
            "thresholds": {
                "prop_to_instance": min_uses,
                "instance_to_template": min_sessions,
                "require_review": bool(promo.get("require_review", True)),
            },
            "candidates": out,
        }
    finally:
        conn.close()


@router.post("/api/items/promote")
def promote_item(payload: dict) -> dict:
    """把候选道具**提议**进世界包。

    架构约束（02.9）：数据层只读，运行时**不改** `worlds/`。
    这里只生成待确认补丁 `data/promotions/*.yaml`；
    人工 review 后执行 `python tools/apply_promotions.py --apply` 才真正写进世界包。
    """
    world = str(payload.get("world") or "")
    canonical = str(payload.get("canonical") or "").strip()
    if not canonical:
        raise HTTPException(400, "需要 canonical（道具名）")
    try:
        pack = load_world(world or _default_world(), ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc
    tier = max(1, min(5, int(payload.get("tier") or 1)))
    slot = str(payload.get("slot") or "trinket")
    tags = [str(t) for t in (payload.get("tags") or [])][:3]
    try:
        from ...equipment.promote import build_patch, write_pending

        patch = build_patch(
            world_id=pack.world_id,
            canonical=canonical,
            slot=slot,
            tier=tier,
            tags=tags,
        )
        path = write_pending(ROOT, patch)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"生成待确认补丁失败：{exc}") from exc
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        rel = str(path)
    return {
        "ok": True,
        "applied": False,
        "world": pack.world_id,
        "template_id": patch["template_id"],
        "name": canonical,
        "pending": rel,
        "hint": "补丁已生成；确认后执行 python tools/apply_promotions.py --apply",
    }
