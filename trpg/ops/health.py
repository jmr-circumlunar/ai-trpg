"""M15 运维：探活快照（只读，不判业务）。

doc: 03-模块/M15-运维与部署/00-模块总述.md
module: M15
layer: L7
"""

from __future__ import annotations


def health_snapshot(sessions: int = 0) -> dict:
    """进程内会话数 + 就绪标记。DB 连通性由调用方按需补。"""
    return {"ok": True, "sessions": int(sessions)}
