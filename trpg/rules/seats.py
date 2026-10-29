"""M02 规则 · 席位声明：**每席是人还是模型**（S4-3）。

为什么要它：以前「号位」只是房间页的一行座位，「换成本人」按钮灰显 ——
真人席位只能靠后端手写 `human_ids`，玩家在界面上没法表达「这一席我来坐」。

现在席位是一份**声明**（`rules/seats.yaml` 或世界包 `ui/seats.yaml`）：
- `label`：这一席怎么称呼（中性，不含世界语义）；
- `agent`：默认 agent（空 = 用世界默认）；
- `human`：默认是否真人。

读取顺序：**世界包优先、规则层兜底** —— 世界包想改就放 `ui/seats.yaml`，
不改就用框架默认。两层都是 yaml，机制在代码。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_seat_config(root: Path | str, world_id: str) -> dict[str, Any]:
    """读取席位声明配置（世界包优先，规则层兜底）。"""
    base = Path(root)
    world_cfg = _read_yaml(base / "worlds" / str(world_id) / "ui" / "seats.yaml")
    if world_cfg:
        return world_cfg
    return _read_yaml(base / "rules" / "seats.yaml")


def seat_declarations(
    root: Path | str,
    world_id: str,
    count: int,
) -> list[dict[str, Any]]:
    """按号位数量展开声明（1..count）。

    每项：`{index, label, agent, human}`。未声明的号位用 `default` 模板补齐，
    `label` 里的 `{index}` 会被替换成实际号位号。
    """
    cfg = load_seat_config(root, world_id)
    default = dict(cfg.get("default") or {})
    declared = {
        int(item.get("index") or 0): dict(item or {})
        for item in (cfg.get("seats") or [])
        if isinstance(item, dict)
    }
    out: list[dict[str, Any]] = []
    for index in range(1, max(0, int(count or 0)) + 1):
        item = dict(default)
        item.update(declared.get(index, {}))
        label = str(item.get("label") or "{index} 号位")
        out.append(
            {
                "index": index,
                "label": label.replace("{index}", str(index)),
                "agent": str(item.get("agent") or ""),
                "human": bool(item.get("human")),
            }
        )
    return out


def human_seat_ids(cards: dict[str, Any], human_seats: list[int] | None) -> list[str]:
    """把「号位序号（1 起）」映射成**角色 id 列表**。

    `cards` 的插入顺序就是号位顺序（发牌时按席位生成），所以第 i 个号位
    对应 `list(cards)[i-1]`。映射不到就跳过（宁可少一个真人席位，也不要指错人）。
    """
    order = list(cards.keys())
    out: list[str] = []
    for index in human_seats or []:
        try:
            pos = int(index) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= pos < len(order):
            out.append(str(order[pos]))
    return out
