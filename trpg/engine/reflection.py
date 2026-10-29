"""局末经验提炼：**引擎级动作**，API 与 CLI 共用同一份。

## 为什么不能只放在 API 的 `_finish` 里

CLI（`run.py run`）是另一个入口。它跑出来的局**永远不会产生经验**——
实测：真实模型跑完 8 人 6 回合，`agent_experience` 是 **0 条**。

经验属于「这一局结束了」这件事，不属于某个 HTTP 端点。所以抬到引擎层：

- `Session.bind_agents()` 用它算 `char → agent`
- `api/deps.py::_reflect_experience()` 与 `run.py` 收尾都调 `reflect_all()`

## 三条约束

1. **写回 agent 而不是存档**——下局开局由 `load_experience` 读回来，只写不读等于没做；
2. **失败静默**——经验是锦上添花，一次调用失败不能把收尾流程搞崩；
3. **出局也算经历**——`TurnRecord.actors` 只记当回合最后一个行动阶段的人，
   第一晚就出局的角色不在里面，但它恰恰最有话可说。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from typing import Any


def experience_enabled(pack) -> bool:
    """世界是否开了经验系统（`systems.experience`；缺省开启，关闭需显式声明）。"""
    try:
        from ..rulespack import assemble

        return assemble(pack).has("experience")
    except Exception:  # noqa: BLE001  装配失败不阻断收尾
        return False


def char_to_agent(cards, world_id: str, agent_ids: list[str] | None = None) -> dict[str, str]:
    """角色 → agent 标识。

    优先用显式绑定（`agent_ids`，**按席位顺序**的数组；库里叫 `agent_ids_json`）；
    没绑的席位派生 `<world>:<char>`，保证同世界同席位跨局沿用经验。

    **诚实说明**：席位级 id 不是 persona 级身份，真正的 persona 绑定要等 M20。
    """
    explicit = [str(item or "") for item in (agent_ids or [])]
    mapping: dict[str, str] = {}
    for index, cid in enumerate(cards or {}):
        bound = explicit[index] if index < len(explicit) else ""
        mapping[str(cid)] = bound or f"{world_id}:{cid}"
    return mapping


def events_for(records, cid: str) -> list[str]:
    """这个角色这一局「做过什么」（含出局）。

    用 `TurnRecord` 而不是 `EventStore`：回执里本来就是逐角色的行动原文，
    而且**读档后仍在**（事件流在 resume 之后可能被清空）。
    """
    out: list[str] = []
    for rec in records or []:
        for index, actor in enumerate(getattr(rec, "actors", []) or []):
            if actor == cid:
                actions = getattr(rec, "actions", []) or []
                if index < len(actions) and actions[index]:
                    out.append(str(actions[index]))
        for item in getattr(rec, "deaths", []) or []:
            if str((item or {}).get("id") or "") == cid:
                out.append("我这一局出局了。")
    return out


def reflect_all(
    loop,
    records,
    ending: str,
    mapping: dict[str, str],
    conn,
    world_id: str,
    sid: str = "",
    pack: Any = None,
    keep: int | None = None,
    limit: int | None = None,
) -> int:
    """对每个有经历的角色调 `reflect()` 并写库，返回写入条数。"""
    if conn is None or not mapping:
        return 0
    if pack is not None and not experience_enabled(pack):
        return 0

    hook = getattr(getattr(loop, "llm", None), "reflect", None)
    if not callable(hook):
        return 0

    from ..storage.experience import (
        DEFAULT_KEEP,
        MAX_PER_RUN,
        add_experience,
        list_experience,
        prune_experience,
    )

    keep_n = DEFAULT_KEEP if keep is None else int(keep)
    limit_n = MAX_PER_RUN if limit is None else int(limit)
    written = 0

    for cid, agent_id in mapping.items():
        events = events_for(records, cid)
        if not events:
            # 这一局没轮到它做事（开局即终局）：没有可反思的素材，不硬编
            continue

        existing = [
            str(item.get("text") or "")
            for item in list_experience(conn, agent_id, world_id, limit=keep_n)
        ]
        try:
            learned = hook(ending, events, existing, limit=limit_n)
        except Exception:  # noqa: BLE001
            continue

        for item in learned or []:
            text = str((item or {}).get("text") or "").strip()
            if not text:
                continue
            try:
                if add_experience(
                    conn,
                    agent_id,
                    world_id,
                    text,
                    kind=str(item.get("kind") or "lesson"),
                    source_session=sid,
                    source_turn=len(records or []),
                ):
                    written += 1
            except Exception:  # noqa: BLE001
                continue

        try:
            # 上限保护：十局前的教训不如上一局刚吃过的亏管用
            prune_experience(conn, agent_id, world_id, keep=keep_n)
        except Exception:  # noqa: BLE001
            pass

    return written


__all__ = [
    "char_to_agent",
    "events_for",
    "experience_enabled",
    "reflect_all",
]
