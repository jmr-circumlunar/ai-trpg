"""人类席位：把 DECLARE 阶段的「轮到你了」变成一次可等待的 Future。

设计边界（见知识库 `过程记录/方案/03-二期-人机混合与多时间线.md`）：
- 只在 DECLARE 阶段等人，叙事与判定仍归 GM / 代码；
- 提交内容与 LLM 输出同构，共用 `PlayerAction.from_raw`；
- 超时或显式 `{"ghost": true}` 由 LLM 代打，不把整局卡死；
- 不落库：重启后由 `resume` 按 `human_ids` 重新建席位。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Pending:
    actor_id: str
    name: str
    turn: int
    briefs: list[str]
    skills: list[str]
    future: asyncio.Future
    deadline: float
    # 等人的**种类**：`action`（行动）或 `vote`（投票）。
    # 投票时前端要显示候选列表，而不是行动输入框。
    kind: str = "action"
    candidates: list[str] = field(default_factory=list)


@dataclass
class HumanSeat:
    human_ids: list[str] = field(default_factory=list)
    timeout: float = 120.0
    # 两态（P4-T10）：
    #   handoff：超时就由 LLM 代打（默认，不卡死整局）
    #   wait   ：一直等，不抢人头（人去倒水不会回来发现"我已经行动过了"）
    # 但仍设上限，避免整局永久挂住。
    mode: str = "handoff"
    wait_max: float = 1800.0
    log: list[dict] = field(default_factory=list)
    _pending: dict[str, _Pending] = field(default_factory=dict)
    # 图路径（LangGraph `interrupt`）收上来的提交，先存在这里等 `_declare_one` 取走。
    # 为什么不用 Future：`interrupt` 是「暂停整个图」，不是「await 一个对象」，
    # 恢复后代码从节点开头重跑，所以要把结果放在一个**可重入读取**的槽里。
    _preloaded: dict[str, dict] = field(default_factory=dict)

    def preload(self, cid: str, data: dict) -> None:
        """把 `interrupt` 收到的提交存进槽位（图路径用）。"""
        self._preloaded[str(cid)] = dict(data or {})

    def take_preloaded(self, cid: str) -> dict | None:
        """取走并清空该角色的预提交；没有返回 None（调用方回落等待）。"""
        return self._preloaded.pop(str(cid), None)

    # ── 配置 ────────────────────────────────────────────────
    def is_human(self, cid: str) -> bool:
        return str(cid) in self.human_ids

    def set_humans(self, ids: list[str] | None) -> None:
        self.human_ids = [str(x) for x in (ids or [])]

    # ── 前端可见状态 ─────────────────────────────────────────
    def pending(self) -> dict[str, Any] | None:
        """当前在等谁（同一时刻最多一个，因为 DECLARE 是顺序的）。"""
        if not self._pending:
            return None
        item = next(iter(self._pending.values()))
        remaining = int(max(0.0, item.deadline - time.monotonic()) * 1000)
        return {
            "char_id": item.actor_id,
            "name": item.name,
            "turn": item.turn,
            "briefs": item.briefs,
            "skills": item.skills,
            "timeout_ms": int(self.timeout * 1000),
            "remaining_ms": remaining,
            "kind": item.kind,
            "candidates": list(item.candidates),
        }

    # ── 循环侧 ──────────────────────────────────────────────
    async def wait_action(
        self,
        cid: str,
        name: str,
        turn: int,
        briefs: list[str],
        skills: list[str],
        timeout: float | None = None,
        kind: str = "action",
        candidates: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """等人提交；超时返回 None（调用方回落 LLM 代打）。

        `kind="vote"` 时前端应显示候选列表让人挑一个，而不是行动输入框。
        """
        loop = asyncio.get_running_loop()
        if timeout is not None:
            limit = float(timeout)
        elif self.mode == "wait":
            limit = float(self.wait_max)  # 不代打，但也不会无限挂
        else:
            limit = float(self.timeout)
        item = _Pending(
            actor_id=str(cid),
            name=name,
            turn=turn,
            briefs=list(briefs),
            skills=list(skills),
            future=loop.create_future(),
            deadline=time.monotonic() + limit,
            kind=str(kind or "action"),
            candidates=[str(x) for x in (candidates or [])],
        )
        self._pending[str(cid)] = item
        try:
            return await asyncio.wait_for(item.future, timeout=limit)
        except asyncio.TimeoutError:
            self.log.append({"turn": turn, "char_id": cid, "kind": "timeout"})
            return None
        except asyncio.CancelledError:  # 服务停 / 请求取消：当作弃权
            self.log.append({"turn": turn, "char_id": cid, "kind": "cancelled"})
            raise
        finally:
            self._pending.pop(str(cid), None)

    # ── 接口侧 ──────────────────────────────────────────────
    def submit(self, cid: str, data: dict[str, Any]) -> bool:
        """人类提交行动。返回 False = 没有在等这个角色（或已经超时）。"""
        item = self._pending.get(str(cid))
        if item is None or item.future.done():
            return False
        payload = dict(data or {})
        item.future.set_result(payload)
        self.log.append(
            {
                "turn": item.turn,
                "char_id": str(cid),
                "kind": "ghost" if payload.get("ghost") else "submitted",
                "action": str(payload.get("action") or "")[:60],
            }
        )
        self.log = self.log[-100:]
        return True
