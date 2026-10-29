"""M14 接口 · 事件文案表只读端点（S7-1）。

**前后端共用一份文案**：前端要显示事件提示时，走这个端点拿**同一张表**，
不许前端自己写第二份措辞。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...agents.prompts.events import load_event_texts
from ..deps import ROOT

router = APIRouter()


@router.get("/api/worlds/{world_id}/event-texts")
def world_event_texts(world_id: str, request: Request) -> dict:
    """事件文案表（只读）：角色 × 事件的措辞 + 变量占位 + 情境提醒。

    **缺省为空**：这个世界没有声明文案时返回空表（前端据此不显示任何提示）。
    """
    texts = load_event_texts(ROOT, world_id)
    events = []
    for item in texts:
        roles = dict(item.get("roles") or {})
        events.append(
            {
                "id": str(item.get("id")),
                "category": str(item.get("category") or ""),
                "roles": {str(k): str(v) for k, v in roles.items()},
                "variables": [str(v) for v in (item.get("variables") or [])],
                "hints": [str(h) for h in (item.get("hints") or [])],
            }
        )
    return {
        "world_id": world_id,
        "events": events,
        "source": "world" if events else "empty",
    }
