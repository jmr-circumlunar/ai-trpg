"""M04 模型 · NPC 关系：**带时间区间的事实**（S5-1）。

为什么不是「当前值」：关系会变 —— 「某 NPC 在某段时间是盟友，之后破裂」。
只存当前值就**不可回溯**（问「当时他们是什么关系」就没法答）。

双时态两维（J-1 / Graphiti 的 bi-temporal 模型）：
- **valid time**（事实何时在世界里成立）：`valid_from` / `valid_to`（`None` = 仍成立）；
- **transaction time**（系统何时知道）：`recorded_turn` / `created_at`。

关掉一条关系 = 把当前有效区间**闭合**（写 `valid_to`），**不删历史**。

doc: 03-模块/M01-数据模型/00-模块总述.md
module: M01
layer: L0
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Relation(BaseModel):
    """一条关系事实。

    - `subject` / `object`：两端 id（角色 id 或 NPC id）；
    - `kind`：关系类型（**中性词**，如 ally / rival / kin；具体含义由世界包解释，框架不认）；
    - `weight`：强度（-1..1 或 0..1，由调用方定义；框架不解释取值）；
    - `valid_from` / `valid_to`：事实成立的回合区间（`valid_to=None` 表示仍成立）；
    - `source_event`：来源事件 id（可回溯到具体事件）；
    - `recorded_turn`：系统**知道**这条关系的回合（transaction time）。
    """

    subject: str = Field(min_length=1)
    object: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    weight: float = 0.0
    valid_from: int = 0
    valid_to: int | None = None
    source_event: str = ""
    recorded_turn: int = 0

    def active_at(self, turn: int | None) -> bool:
        """这条关系在 `turn` 回合是否成立。

        `turn=None` 表示「此刻」（= 现在）：看 `valid_to` 是否还开着。
        """
        if self.valid_to is None:
            return True
        if turn is None:
            return False
        return int(self.valid_from) <= int(turn) <= int(self.valid_to)

    def describe(self) -> str:
        """一行人话（注入 prompt 用）。不解释 kind 的含义 —— 那是世界包的事。"""
        span = f"第 {self.valid_from} 回合起"
        if self.valid_to is not None:
            span += f"，至第 {self.valid_to} 回合止"
        else:
            span += "（至今仍成立）"
        reason = f"；依据：{self.source_event}" if self.source_event else ""
        return f"{self.subject} 与 {self.object}：{self.kind}（强度 {self.weight:g}）· {span}{reason}"


def relations_to_dicts(relations: list[Relation] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """统一成 dict 列表（给 API / 导出用）。"""
    out: list[dict[str, Any]] = []
    for item in relations or []:
        if isinstance(item, Relation):
            out.append(item.model_dump())
        elif isinstance(item, dict):
            out.append(dict(item))
    return out
