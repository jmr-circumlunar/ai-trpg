"""角色经历（L4 记忆的结构化地基）。

设计取舍：**先用结构化事件，不上向量**。
- 事实必须精确（记错会导致矛盾），向量是模糊召回，用来查事实会让模型编造
- 情景细节（光线/气味/氛围）留给 S2 的向量层，这里只存「谁做了什么、结果如何」

doc: 03-模块/M01-数据模型/00-模块总述.md
module: M01
layer: L0
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EventKind = Literal["action", "outcome", "discovery", "wound", "relation", "promise"]

# outcome -> importance，用于回忆排序（越重要越容易被想起）
IMPORTANCE_BY_OUTCOME = {
    "crit_success": 8,
    "fail_forward": 6,
    "failure": 5,
    "success": 4,
}


class CharacterEvent(BaseModel):
    session_id: str
    char_id: str
    turn: int
    day: int = 1
    hour: int = 20
    kind: EventKind = "action"
    summary: str
    entities: list[str] = Field(default_factory=list)
    importance: int = Field(default=5, ge=1, le=10)

    def render(self) -> str:
        """给 prompt 用的一行文本。"""
        return f"第{self.turn}回合（第{self.day}天{self.hour:02d}时）{self.summary}"
