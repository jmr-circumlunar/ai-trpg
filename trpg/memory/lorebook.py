"""Lorebook 的**第二种形态**：设定词条触发（关键词命中才注入）。

**为什么要两种形态**（J-4）：背景文档（`archive/*.md` → 向量检索）适合「找相关背景」，
设定条目（`lorebook.yaml` → 词条触发）适合「某专有名词一出现就注入其设定」。
**两者并存，不是二选一。**

**字段沿用 SillyTavern World Info 的成熟形态**（联网核对 2026-10-28）：
`key`（触发词）、`scan_depth`（往回扫几条）、`sticky`（命中后保持几轮）、
`priority`（多条命中时的顺序）、`content`（注入文本）。

**词条是内容，不是框架常量**：框架只提供读取与匹配，
条目全在世界包 `worlds/<id>/lorebook.yaml` 里（本模块不写任何词条）。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoreEntry:
    """一条设定词条。"""

    id: str
    keys: list[str] = field(default_factory=list)
    content: str = ""
    priority: int = 0
    sticky: int = 0
    scan_depth: int = 0  # 0 = 用全局默认扫描深度


def parse_entries(raw: list[dict[str, Any]] | None) -> list[LoreEntry]:
    """把世界包里的原始条目解析成 `LoreEntry`（缺字段用缺省，坏条目跳过）。"""
    out: list[LoreEntry] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        keys = [str(k) for k in (item.get("keys") or item.get("key") or []) if str(k)]
        content = str(item.get("content") or item.get("text") or "").strip()
        if not keys or not content:
            # 没有触发词或没有内容的条目没有意义（内容不能为空，否则注入空白）
            continue
        out.append(
            LoreEntry(
                id=str(item.get("id") or f"lore{len(out) + 1}"),
                keys=keys,
                content=content,
                priority=int(item.get("priority") or 0),
                sticky=int(item.get("sticky") or 0),
                scan_depth=int(item.get("scan_depth") or 0),
            )
        )
    return out


def _hit(entry: LoreEntry, window: list[str]) -> bool:
    haystack = "\n".join(window).lower()
    return any(key.lower() in haystack for key in entry.keys)


def match_lorebook(
    entries: list[LoreEntry],
    texts: list[str],
    *,
    sticky_state: dict[str, int] | None = None,
    max_entries: int = 3,
    default_scan_depth: int = 2,
) -> list[LoreEntry]:
    """返回命中的条目（**未命中就不注入**）。

    - `texts`：按时间序的最近回合文本（越靠后越新）；
    - 条目自己的 `scan_depth` 覆盖全局默认（0 = 用默认）；
    - `sticky`：命中后按条目的 sticky 值保持命中若干轮（状态放在 `sticky_state` 里，
      由调用方持有，天然可重入）。
    """
    if not entries or not texts:
        return []

    state = sticky_state if sticky_state is not None else {}
    hits: list[LoreEntry] = []
    for entry in entries:
        depth = entry.scan_depth if entry.scan_depth > 0 else int(default_scan_depth)
        window = list(texts)[-depth:] if depth > 0 else []
        remaining = int(state.get(entry.id, 0) or 0)
        if remaining > 0:
            state[entry.id] = max(0, remaining - 1)
            hits.append(entry)
            continue
        if window and _hit(entry, window):
            if entry.sticky > 0:
                state[entry.id] = int(entry.sticky)
            hits.append(entry)

    hits.sort(key=lambda e: (-e.priority, e.id))
    if max_entries > 0:
        hits = hits[:max_entries]
    return hits


def render_lorebook(entries: list[LoreEntry]) -> list[str]:
    """给 prompt 的注入文本。"""
    return [f"（设定）{entry.content}" for entry in entries if entry.content]


__all__ = ["LoreEntry", "match_lorebook", "parse_entries", "render_lorebook"]
