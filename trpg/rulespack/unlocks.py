"""解锁树依赖校验：命名引用，不做表达式。

每个解锁项形如 {id, requires: [其他 id]}；requires 只能是命名引用
（不执行表达式、不执行世界包表达式）。校验只做三件事：
引用是否存在、有没有成环、有没有自依赖。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from typing import Any


def validate_unlocks(nodes):
    """返回问题列表（空 = 依赖树合法）。"""
    problems = []
    ids = {}
    for node in nodes or []:
        if not isinstance(node, dict):
            problems.append("解锁项必须是映射")
            continue
        nid = str(node.get("id") or "")
        if not nid:
            problems.append("解锁项缺少 id")
            continue
        if nid in ids:
            problems.append("解锁 id 重复：" + nid)
            continue
        ids[nid] = node

    graph = {}
    for nid, node in ids.items():
        raw = node.get("requires")
        if raw is None:
            graph[nid] = []
            continue
        if not isinstance(raw, (list, tuple)):
            problems.append(nid + ": requires 必须是 id 列表（命名引用，不做表达式）")
            graph[nid] = []
            continue
        deps = []
        for item in raw:
            if not isinstance(item, str):
                problems.append(nid + ": requires 只能是命名 id（不做表达式）")
                continue
            dep = item.strip()
            if not dep:
                continue
            if dep == nid:
                problems.append(nid + ": 不能依赖自己")
                continue
            if dep not in ids:
                problems.append(nid + ": 依赖的解锁项不存在：" + dep)
                continue
            deps.append(dep)
        graph[nid] = deps

    state = {}

    def visit(node, stack):
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            problems.append("解锁依赖成环：" + " -> ".join(stack + [node]))
            return
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep, stack + [node])
        state[node] = 2

    for nid in graph:
        visit(nid, [])

    seen = set()
    out = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            out.append(problem)
    return out
