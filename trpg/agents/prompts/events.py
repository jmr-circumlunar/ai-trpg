"""M12 Prompt · 事件文案表：角色 × 事件的措辞（S7-1）。

**为什么文案要进声明**：措辞是**内容**，不是框架常量。放在代码里会出现
「后端注入说 A、前端展示说 B」的两份真相 —— 所以前后端读**同一张表**。

三层职责：
- `load_event_texts`：读表（**世界包优先、框架兜底**；都没有 → 空表）；
- `render_text`：把模板里的 `{变量}` 填上（缺变量 → 留空，不报错）；
- `event_hints`：给 GM 的**情境提醒**（缺省为空 → 什么都不注入 → 逐字节不变）。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_VAR = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}
    return dict(data) if isinstance(data, dict) else {}


def load_event_texts(root: Path | str, world_id: str) -> list[dict[str, Any]]:
    """读事件文案表（世界包优先、框架兜底；都没有 → 空表）。

    **缺省为空是硬要求**：没有任何文案时不得注入任何东西，
    否则现有对局的 prompt 会变（golden 必须逐字节不变）。
    """
    base = Path(root)
    data = _read_yaml(base / "worlds" / str(world_id) / "prompts" / "events.yaml")
    if not data:
        data = _read_yaml(base / "prompts" / "events.yaml")
    raw = data.get("events") or []
    return [dict(item) for item in raw if isinstance(item, dict) and item.get("id")]


def render_text(template: str, variables: dict[str, Any] | None = None) -> str:
    """填模板变量：缺变量 → 留空占位（不报错、不抛异常）。"""
    values = {str(k): v for k, v in (variables or {}).items()}

    def repl(match: re.Match) -> str:
        key = match.group(1)
        value = values.get(key, "")
        return "" if value is None else str(value)

    return _VAR.sub(repl, str(template or ""))


def text_for(
    texts: list[dict[str, Any]],
    event_id: str,
    role: str,
    variables: dict[str, Any] | None = None,
) -> str:
    """取某个事件在某个角色槽位上的文案（没有 → 空串）。"""
    for item in texts or []:
        if str(item.get("id")) != str(event_id):
            continue
        roles = dict(item.get("roles") or {})
        template = roles.get(str(role))
        if not template:
            return ""
        return render_text(str(template), variables)
    return ""


def event_hints(
    texts: list[dict[str, Any]],
    event_ids: list[str] | None = None,
) -> str:
    """给 GM 的情境提醒（按事件 id 取 `hints`）。

    没有事件 id → 取全部（去重保序）。没有提醒 → **空串**（不注入空段落）。
    """
    wanted = [str(x) for x in (event_ids or [])]
    hints: list[str] = []
    for item in texts or []:
        if wanted and str(item.get("id")) not in wanted:
            continue
        for hint in item.get("hints") or []:
            text = str(hint).strip()
            if text and text not in hints:
                hints.append(text)
    return "\n".join(f"- {h}" for h in hints)
