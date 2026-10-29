"""M14 · 玩家视图的**单一服务端出口**（S4-2 / E-2）。

抄 boardgame.io 的 `filter-player-view.ts`（E-2）：裁剪发生在**服务端**，
而且裁的是**状态 + 日志**两处；未过滤的字段**根本不在**发出去的内容里。

**为什么是「单一出口」**：以前裁剪散在多个点（快照内联裁、SSE 内联裁、
前端再裁一遍），前端逻辑一有漏洞就泄露。现在所有对客户端的出口都过
`filter_for_viewer()`——**一处裁剪，全端点一致**。

**可见性规则仍走既有单源**：世界包的 `secrets`（身份可见性）与
`visibility.thoughts`（思维过程可见性）说了算，框架不发明新规则。

增量同步用 **JSON Patch 差分**（rfc6902 风格）：只发「过滤前后」的差分，
未过滤字段根本不会出现在 patch 里——天然防泄露。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from typing import Any, Callable

#: 身份类字段：`secrets.profession: self` 时，**别人**的这些字段要抹掉。
#: 注意 `path` 是**原始 id**（比 `path_label` 更致命：id 直接等于角色名）。
IDENTITY_FIELDS = ("profession", "path", "path_label")

#: 私有字段：只给本人与观察者（角色级「已确认的信息」）。
PRIVATE_FIELDS = ("notes",)


def filter_for_viewer(
    viewer: str | None,
    payload: Any,
    *,
    secrets: dict[str, Any] | None = None,
    observer: bool = False,
    thoughts_visible: Callable[[str], bool] | None = None,
    kind: str = "state",
) -> Any:
    """**所有出口的唯一入口**：按 viewer 裁剪状态与日志。

    - `viewer=None` 或 `observer=True` → 观察者视角，原样返回（不裁）；
    - `kind="state"` → 裁**状态**（身份字段 / 私有字段）；
    - `kind="log"` → 裁**日志**（思维过程：`_thoughts_visible` 说不给就删字段）。

    规则来自世界包：`secrets.profession`（身份）；思维过程由调用方传入
    `thoughts_visible`（复用 `deps._thoughts_visible` 这个单源）。
    """
    if payload is None:
        return payload
    if viewer is None or observer:
        return payload
    if str(kind) == "log":
        return _filter_log(payload, thoughts_visible)
    return _filter_state(viewer, payload, secrets)


def _filter_state(viewer: str, payload: Any, secrets: dict[str, Any] | None) -> Any:
    """状态裁剪：身份字段（按 `secrets.profession`）+ 私有字段（只给本人）。

    角色清单可能在两个键下：快照用 `characters`，单回合结果用 `actors`——
    两处都裁，否则回合结果会把职业又漏出去。
    """
    if not isinstance(payload, dict):
        return payload
    data = dict(payload)
    mode = str((secrets or {}).get("profession") or "all")
    for key in ("characters", "actors"):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        out: list[Any] = []
        for char in rows:
            if not isinstance(char, dict):
                out.append(char)
                continue
            item = dict(char)
            cid = str(item.get("id") or "")
            if cid and cid != str(viewer):
                if mode == "self":
                    for field in IDENTITY_FIELDS:
                        if field in item:
                            item[field] = ""
                for field in PRIVATE_FIELDS:
                    if field in item and item[field]:
                        item[field] = [] if isinstance(item[field], list) else ""
            out.append(item)
        data[key] = out
    return data


def _filter_log(payload: Any, thoughts_visible: Callable[[str], bool] | None) -> Any:
    """日志裁剪：思维过程按可见性删字段（事件本身「谁做了什么」仍公开）。"""
    if not isinstance(payload, dict) or "thought" not in payload:
        return payload
    cid = str(payload.get("id") or "")
    if callable(thoughts_visible) and thoughts_visible(cid):
        return payload
    return {key: value for key, value in payload.items() if key != "thought"}


def _escape(key: str) -> str:
    """JSON Pointer 转义（`~` → `~0`，`/` → `~1`）。"""
    return str(key).replace("~", "~0").replace("/", "~1")


def json_patch(prev: Any, cur: Any, path: str = "") -> list[dict[str, Any]]:
    """rfc6902 风格的差分（`add` / `remove` / `replace`）。

    只发**变化**的部分：增量同步省带宽，而且未过滤字段根本不会出现在 patch 里。
    列表按整体替换处理（不做逐元素 diff——收益小、易错）。
    """
    out: list[dict[str, Any]] = []
    if isinstance(prev, dict) and isinstance(cur, dict):
        for key in cur:
            child = f"{path}/{_escape(key)}"
            if key not in prev:
                out.append({"op": "add", "path": child, "value": cur[key]})
            else:
                out.extend(json_patch(prev[key], cur[key], child))
        for key in prev:
            if key not in cur:
                out.append({"op": "remove", "path": f"{path}/{_escape(key)}"})
    elif prev != cur:
        out.append({"op": "replace", "path": path or "/", "value": cur})
    return out


__all__ = [
    "IDENTITY_FIELDS",
    "PRIVATE_FIELDS",
    "filter_for_viewer",
    "json_patch",
]
