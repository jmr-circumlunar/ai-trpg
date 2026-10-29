"""反思层：每 N 回合把多条记忆巩固成高阶结论（角色认知 / 传闻）。

**抄形态**（Generative Agents 的 reflection，联网核对 2026-10-28）：
每条记忆有 1-10 重要分；**当最近事件的累计重要度越过阈值**（他们的实现是 150）
就触发一次巩固：读最近的高重要度记忆 → 合成更高层的洞察。

**我们的分工**（AGENTS 第五条：判定归代码，叙事归模型）：
本模块只做「哪些记忆属于同一条高阶结论」的**确定性聚类**，
产出结构化的 `Consolidation`；**措辞交给上层**（叙事归模型）。

聚类规则：事件之间**共享实体**即视为同一主题（并查集）；
一簇至少 `min_cluster` 条才值得巩固（一条不算「多条」）。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 「关于别人的事」= 传闻；「自己的经历」= 认知
RUMOR_KINDS = ("relation",)


@dataclass
class Consolidation:
    """一条高阶结论（角色认知 / 传闻）。"""

    char_id: str
    turn: int
    kind: str  # "cognition" | "rumor"
    entities: list[str] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    importance: int = 0

    def render(self) -> str:
        """给 prompt 的一行文本。"""
        who = "传闻" if self.kind == "rumor" else "认知"
        target = "、".join(self.entities[:3]) if self.entities else "近期经历"
        points = "；".join(self.texts[:3])
        return f"（{who}）关于{target}：{points}"


def should_consolidate(
    turn: int,
    *,
    every_n: int = 0,
    importance_sum: int = 0,
    threshold: int = 0,
) -> bool:
    """该不该在这一回合巩固？

    两个触发条件（任一成立即可）：
    1. **回合到了倍数**：`every_n > 0` 且 `turn % every_n == 0`；
    2. **累计重要度越阈值**：`threshold > 0` 且最近事件的累计重要分 ≥ 阈值。
    """
    if every_n > 0 and turn > 0 and turn % every_n == 0:
        return True
    if threshold > 0 and importance_sum >= threshold:
        return True
    return False


def _clusters(events: list[Any]) -> list[list[Any]]:
    """按「共享实体」把事件聚成簇（并查集）。没有实体的事件各自成簇。"""
    parent = list(range(len(events)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    entity_owner: dict[str, int] = {}
    for idx, event in enumerate(events):
        for entity in getattr(event, "entities", []) or []:
            key = str(entity)
            if key in entity_owner:
                union(entity_owner[key], idx)
            else:
                entity_owner[key] = idx

    groups: dict[int, list[Any]] = {}
    for idx, event in enumerate(events):
        groups.setdefault(find(idx), []).append(event)
    return list(groups.values())


def consolidate(
    events: list[Any],
    char_id: str,
    turn: int,
    *,
    min_cluster: int = 2,
    max_clusters: int = 3,
) -> list[Consolidation]:
    """把一批事件巩固成高阶结论。

    只有**至少 `min_cluster` 条**、且彼此共享实体的簇才会成为一条结论；
    簇里出现 `relation` 类事件 → 判为「传闻」，否则判为「认知」。
    """
    if not events or min_cluster < 2:
        return []

    out: list[Consolidation] = []
    for cluster in _clusters(list(events)):
        if len(cluster) < min_cluster:
            continue
        entities = sorted({str(e) for ev in cluster for e in (getattr(ev, "entities", []) or [])})
        if not entities:
            # 没有共同实体 = 只是「碰巧同期发生」，不构成一条高阶结论
            continue
        kinds = {str(getattr(ev, "kind", "") or "") for ev in cluster}
        out.append(
            Consolidation(
                char_id=char_id,
                turn=int(turn),
                kind="rumor" if kinds & set(RUMOR_KINDS) else "cognition",
                entities=entities,
                event_refs=[f"turn{int(getattr(ev, 'turn', 0) or 0)}" for ev in cluster],
                texts=[str(getattr(ev, "summary", "") or "") for ev in cluster],
                importance=max(int(getattr(ev, "importance", 0) or 0) for ev in cluster),
            )
        )

    out.sort(key=lambda c: (-c.importance, -len(c.event_refs)))
    if max_clusters > 0:
        out = out[:max_clusters]
    return out


def render_consolidations(items: Iterable[Consolidation]) -> list[str]:
    """给 prompt 的注入文本。"""
    return [c.render() for c in items]


__all__ = [
    "Consolidation",
    "consolidate",
    "render_consolidations",
    "should_consolidate",
]
