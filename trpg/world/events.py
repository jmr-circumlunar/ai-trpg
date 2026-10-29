"""M04 世界 · 事件声明的**读取**（S5-4）。

只做「把声明读出来」这一件事：
- 世界包 `worlds/<id>/events.yaml` **优先**；
- 没有就用规则层 `rules/events.yaml` **兜底**（框架级默认，谁都能用）。

**区域自治**是**世界内容**（I-3）：`world.yaml` 的 `regions.<id>.autonomy`，
框架只读并据此裁剪 GM 的自由度，不在这里发明任何档位含义。

分层：world 层只出**原始 dict**，调度与打分在 `trpg/engine/events.py`。

doc: 03-模块/M05-世界包/00-模块总述.md
module: M05
layer: L2
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
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}
    return dict(data) if isinstance(data, dict) else {}


def load_event_declarations(root: Path | str, world_id: str) -> list[dict[str, Any]]:
    """读事件声明（世界包优先、规则层兜底）。

    返回原始 dict 列表（`events:` 段）；世界包没声明就返回空列表，
    **不静默回落到规则层**（回落只在世界包**没有这个文件**时发生）。
    """
    base = Path(root)
    world_file = base / "worlds" / str(world_id) / "events.yaml"
    data = _read_yaml(world_file)
    if not data:
        data = _read_yaml(base / "rules" / "events.yaml")
    raw = data.get("events") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def region_autonomy(pack: Any, region: str) -> dict[str, Any]:
    """某个区域的自治声明（`world.yaml: regions.<id>.autonomy`）。

    没有声明 → `{}`（= 框架缺省：不额外限制类别）。
    """
    meta = dict(getattr(pack, "region_meta", {}) or {})
    cfg = dict(meta.get(str(region or "")) or {})
    autonomy = cfg.get("autonomy")
    return dict(autonomy) if isinstance(autonomy, dict) else {}
