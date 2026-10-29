"""M17 · 中间表示（IR）：规则文本 / 小说 → **结构化数据**（S7-2）。

IR 是**数据**，不是一门语言（D-5 不引入 DSL）。字段名一律中性：
框架不解释「这是狼人杀还是江湖」，只搬运结构。

**IR 的边界**：只放「世界包里要写什么」，不放「怎么写」——
措辞、美术、玩法数值都不在这里（数值进 yaml 骨架，由世界包自己定义）。

doc: 03-模块/M17-世界包工坊/00-模块总述.md
module: M17
layer: L1
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WorldIR:
    """世界包骨架的中间表示。

    每个字段对应世界包的一块声明；**全部可选** —— 缺什么骨架就不写什么
    （骨架从 `worlds/_blank` 的空白包起步，不是从零造）。
    """

    name: str = ""
    id: str = ""
    tone: str = ""
    eras: list[dict[str, Any]] = field(default_factory=list)
    regions: list[dict[str, Any]] = field(default_factory=list)
    professions: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    npcs: list[dict[str, Any]] = field(default_factory=list)
    seeds: list[dict[str, Any]] = field(default_factory=list)
    players: dict[str, Any] = field(default_factory=dict)
    win_condition: dict[str, Any] = field(default_factory=dict)
    ui: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# IR 认识的顶层键（其余进 `extra`，不丢内容）
_KNOWN = {
    "name", "id", "tone", "eras", "regions", "professions", "items",
    "npcs", "seeds", "players", "win_condition", "ui",
}


def parse_ir(text: str | dict[str, Any]) -> tuple[WorldIR | None, list[str]]:
    """从**模型产出的 JSON**（或已经是 dict）解析 IR。

    返回 `(ir, errors)`：
    - 成功 → `(ir, [])`；
    - 失败 → `(None, errors)`，**错误是可操作的**（说清缺什么、哪一段坏了），
      不抛栈 —— 工坊要能把这张错误清单交给人看。
    """
    errors: list[str] = []
    data: Any = text
    if isinstance(text, str):
        raw = text.strip()
        if not raw:
            return None, ["输入为空：需要一段 JSON（中间表示）"]
        # 容错：模型常把 JSON 包在 ```json 里
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as exc:
            return None, [f"不是合法 JSON：{exc}"]
    if not isinstance(data, dict):
        return None, [f"中间表示必须是对象（mapping），实际是 {type(data).__name__}"]

    known = {k: v for k, v in data.items() if k in _KNOWN}
    extra = {k: v for k, v in data.items() if k not in _KNOWN}
    for key in ("eras", "regions", "professions", "items", "npcs", "seeds"):
        value = known.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"{key} 必须是列表，实际是 {type(value).__name__}")
            known[key] = []
        else:
            known[key] = [dict(x) for x in value if isinstance(x, dict)]
    for key in ("players", "win_condition", "ui"):
        value = known.get(key)
        if value is not None and not isinstance(value, dict):
            errors.append(f"{key} 必须是映射，实际是 {type(value).__name__}")
            known[key] = {}
    if not str(known.get("name") or "").strip():
        errors.append("缺 name：世界包至少要有名字")

    if errors:
        return None, errors
    ir = WorldIR(
        name=str(known.get("name") or ""),
        id=str(known.get("id") or ""),
        tone=str(known.get("tone") or ""),
        eras=list(known.get("eras") or []),
        regions=list(known.get("regions") or []),
        professions=list(known.get("professions") or []),
        items=list(known.get("items") or []),
        npcs=list(known.get("npcs") or []),
        seeds=list(known.get("seeds") or []),
        players=dict(known.get("players") or {}),
        win_condition=dict(known.get("win_condition") or {}),
        ui=dict(known.get("ui") or {}),
        extra=dict(extra),
    )
    return ir, []
