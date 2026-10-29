"""确定性地图生成器（**不是 Agent**）。

为什么不是 Agent：Agent 的本性是「每回合重新决策」，而地图要求「构造一次后固定」；
让模型每回合生成结构还会逼着放开 StateDelta 白名单，破坏硬状态。

分界：
- 结构（有哪些地点、谁连谁、方位）= 世界包 + 本模块（纯函数、可复现）
- 名字与外观 = 可选的一次性 LLM 调用（开局，结果落库）
- 当下细节 = GM 每回合的叙事，不改结构

产物是普通 dict（可直接 json 序列化落 `session_map` 表），同 `(world, region, seed)`
一定得到同一张图。

doc: 03-模块/M07-地图与空间/00-模块总述.md
module: M07
layer: L3
"""

from __future__ import annotations

import random
from typing import Any

from ..models.rng import derive_parts


def _cfg(rules_map: dict, region: str) -> dict:
    sec = (rules_map.get("secondary") or {})
    base = dict(sec.get("default") or {})
    override = (sec.get("regions") or {}).get(region) or {}
    base.update(override)
    return base


def generate_region_map(
    pack,
    region: str,
    seed: str = "42",
    rules_map: dict | None = None,
) -> dict:
    """生成一个区域的地图。纯函数：不读时钟、不读 state、不写文件。

    主场景来自 `regions/<id>.md` 的地点表：
    - 表里有第 4 列「出口」→ 用作者给的边（`topology="doc"`）
    - 没有 → 按文档顺序成环（`topology="ring"`），仍然固定可复现
    次要建筑按 `rules/map.yaml` 的数量区间与方位枚举随机挂到主场景上。
    """
    cfg = rules_map or {}
    # 历史格式：seed 在**最后**（不是 `seed:...`），只能用 `derive_parts` 原样保住
    rng = derive_parts(getattr(pack, "world_id", ""), region, seed)

    mains = list(pack.locations(region))
    nodes: list[dict[str, Any]] = []
    edges: set[tuple[str, str]] = set()
    declared = any(loc.get("exits") for loc in mains)

    for loc in mains:
        nodes.append(
            {
                "id": loc["id"],
                "label": loc.get("label") or loc["id"],
                "kind": "main",
                "bearing": "",
                "exits": [],
            }
        )

    ids = [n["id"] for n in nodes]
    if declared:
        topology = "doc"
        valid = set(ids)
        for loc in mains:
            for target in loc.get("exits") or []:
                if target in valid and target != loc["id"]:
                    edges.add(tuple(sorted((loc["id"], target))))
    elif cfg.get("ring_topology", True) and len(ids) > 1:
        topology = "ring"
        for i, cur in enumerate(ids):
            nxt = ids[(i + 1) % len(ids)]
            edges.add(tuple(sorted((cur, nxt))))
    else:
        topology = "isolated"

    # ── 次要建筑：数量与方位都来自 rules/map.yaml ──
    quota = _cfg(cfg, region)
    lo, hi = int(quota.get("min", 0) or 0), int(quota.get("max", 0) or 0)
    count = rng.randint(lo, hi) if hi >= lo else 0
    bearings = list(cfg.get("bearings") or ["北", "东", "南", "西"])
    fallback = list(cfg.get("fallback_names") or ["旧巷"])
    max_degree = int(cfg.get("max_degree", 5) or 5)

    degree: dict[str, int] = {nid: 0 for nid in ids}
    for a, b in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    for i in range(count):
        sid = f"loc_{region}_s{i + 1}"
        host = None
        candidates = [nid for nid in ids if degree.get(nid, 0) < max_degree]
        if candidates:
            host = rng.choice(candidates)
        nodes.append(
            {
                "id": sid,
                "label": rng.choice(fallback),
                "kind": "secondary",
                "bearing": rng.choice(bearings),
                "exits": [],
            }
        )
        ids.append(sid)
        degree[sid] = 0
        if host:
            edges.add(tuple(sorted((host, sid))))
            degree[host] = degree.get(host, 0) + 1
            degree[sid] = 1

    # ── 由边反推每个节点的出口（排序保证字节一致）──
    by_id = {n["id"]: n for n in nodes}
    for a, b in edges:
        by_id[a]["exits"].append(b)
        by_id[b]["exits"].append(a)
    for node in nodes:
        node["exits"] = sorted(set(node["exits"]))

    return {
        "region": region,
        "world_id": str(getattr(pack, "world_id", "")),
        "seed": str(seed),
        "topology": topology,
        "nodes": nodes,
        "edges": [list(e) for e in sorted(edges)],
    }


def name_minor_buildings(region_map: dict, namer) -> tuple[dict, int]:
    """给次要建筑命名**一次**（已经命名过的不动）。

    形态抄 I-6：命名是**一次性**动作 —— 命名完写回地图（落 `session_map`），
    之后只读，所以前后不会改口、也不会每回合重新命名。

    `namer` 是可调用对象 `(kind, bearing, label) -> str`；
    返回 `(地图, 本次命名次数)`。**没有命名器 → 一次都不命名**（行为零变化）。
    """
    nodes = region_map.get("nodes") or []
    changed = 0
    for node in nodes:
        if str(node.get("kind") or "") != "secondary":
            continue
        if node.get("named"):
            continue
        name = ""
        if callable(namer):
            try:
                name = str(
                    namer(
                        str(node.get("kind") or ""),
                        str(node.get("bearing") or ""),
                        str(node.get("label") or ""),
                    )
                    or ""
                ).strip()
            except Exception:  # noqa: BLE001  命名失败不该拦住开局
                name = ""
        if not name:
            continue
        node["label"] = name
        node["named"] = True
        changed += 1
    return region_map, changed


def exits_for(region_map: dict, location_id: str) -> list[dict]:
    """当前地点的出口，直接可填进 `GameState.scene.exits`。

    作者没给方位时用「出口 id」兜底（不编造方向）。
    """
    nodes = {n["id"]: n for n in (region_map.get("nodes") or [])}
    here = nodes.get(location_id)
    if not here:
        return []
    out = []
    for target in here.get("exits") or []:
        node = nodes.get(target) or {}
        out.append(
            {
                "to": target,
                "region": region_map.get("region", ""),
                "hint": f"{node.get('bearing') or ''}{node.get('label') or target}".strip(),
            }
        )
    return out


def node_label(region_map: dict, location_id: str) -> str:
    for node in region_map.get("nodes") or []:
        if node["id"] == location_id:
            return str(node.get("label") or location_id)
    return str(location_id)


def is_adjacent(region_map: dict, src: str, dst: str) -> bool:
    """移动校验：目标必须在 src 的出口里。"""
    if src == dst:
        return True
    for node in region_map.get("nodes") or []:
        if node["id"] == src:
            return dst in (node.get("exits") or [])
    return False
