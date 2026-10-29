"""结算快照与结局文本。

- `summary()`：纯结构化结算（谁活着、拿了什么、任务到哪一步），落库用
- `epilogue_text()`：规则版结局文本，LLM 不可用时的兜底
- `is_wipe()`：全灭判定（全员 DEAD 才算，濒死不算）

一期不做自动结局：只有「手动结束」与「全灭」两种收尾（HANDOFF §6-16）。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Any

from ..models import CharacterCard, GameState
from ..models.state import is_alive, is_eliminated


def is_wipe(state: GameState) -> bool:
    """全员 DEAD 才算全灭；濒死（dying）还能救，不算。

    这里用 `is_eliminated` 而不是 `is_alive`：濒死 ≠ 全灭（还能救），是**有意**的区别。
    """
    if not state.characters:
        return False
    return all(is_eliminated(rt) for rt in state.characters.values())


def alive_ids(state: GameState) -> list[str]:
    """真正还在场上的人（未出局 **且** 血量 > 0）。

    修复前只判 `position`，0 血的濒死者被算作存活：`camp_superiority` 会因此
    把残血阵营算成多一个人，胜负判反。
    """
    return [cid for cid, rt in state.characters.items() if is_alive(rt)]


@dataclass
class WinResult:
    """胜负判定结果（C58）。`kind` 对应 `world.yaml: win_condition.kind`。"""

    kind: str
    winner: str = ""
    detail: str = ""


def _alive_camps(state) -> dict[str, int]:
    """按阵营统计存活人数；没有 camp 的角色不计入（传统世界因此恒为空）。"""
    counts: dict[str, int] = {}
    for rt in state.characters.values():
        if not is_alive(rt):
            continue
        camp = str(getattr(rt, "camp", "") or "")
        if camp:
            counts[camp] = counts.get(camp, 0) + 1
    return counts


def evaluate_win(
    state, pack, cards: dict[str, CharacterCard] | None = None
) -> WinResult | None:
    """纯函数判胜负（C58）：**LLM 不参与**，只读 state 与世界包配置。

    支持两种形态：
    - 单条件：`win_condition: {kind: ...}`
    - 多条件任一命中：`win_condition: {any_of: [{kind: ...}, ...]}`
      —— 两阵营局用后者（任一方全灭即终局）

    世界没配 `win_condition` 时返回 `None`——`ironvale` / `longzu` 因此完全不受影响，
    它们仍由旧的 `is_wipe()` 语义管（全员出局）。

    `cards` 是「角色 id → 卡」的身份表（卡上带 `profession`）：按**身份**分组的规则
    （`group_eliminated`）离不开它。没传就**抛错**而不是返回 `None`——
    后者会把「调用方漏传」伪装成「游戏还没结束」，是最难查的那类静默失效。
    """
    config = dict(getattr(pack, "win_condition", None) or {}) if pack is not None else {}
    if not config:
        return None

    if "any_of" in config:
        for item in config.get("any_of") or []:
            if isinstance(item, dict):
                result = _evaluate_one(state, item, cards)
                if result is not None:
                    return result
        return None

    return _evaluate_one(state, config, cards)


def _profession_of(cards: dict[str, CharacterCard] | None, cid: str) -> str:
    """角色卡上的身份/职业 id（判「某类身份全灭」用）。"""
    card = (cards or {}).get(cid)
    ext = getattr(card, "ext", None)
    return str(getattr(ext, "profession", "") or "")


def _group_eliminated(
    state, config: dict, cards: dict[str, CharacterCard] | None
) -> WinResult | None:
    """某类身份全灭（屠边）：**本局没发这类身份时不判**。

    为什么空组必须不判：`group` 指向的身份可能这一局压根没发牌
    （例如人少的时候没发某类牌），此时「该组全员出局」恒真，
    第一回合就会直接判胜——这是配置与人数组合出来的假终局。
    """
    if not cards:
        raise ValueError(
            "win_condition.kind=group_eliminated 需要角色卡（身份表）才能判：cards 不能为空"
        )

    group = {str(item) for item in (config.get("group") or []) if str(item).strip()}
    if not group:
        # 配置侧由 `check_world` 拦；这里宁可不判，也不让空组变成恒真
        return None

    members = [
        rt
        for cid, rt in state.characters.items()
        if _profession_of(cards, cid) in group
    ]
    if not members:
        return None
    if any(is_alive(rt) for rt in members):
        return None

    label = str(config.get("label") or "该组身份")
    return WinResult(
        kind="group_eliminated",
        winner=str(config.get("winner") or ""),
        detail=f"{label} 全灭",
    )


def _evaluate_one(
    state, config: dict, cards: dict[str, CharacterCard] | None = None
) -> WinResult | None:
    """单条胜负规则。未识别的 `kind` 返回 `None`（不猜、不硬判）。"""
    kind = str(config.get("kind") or "")
    if not kind:
        return None

    alive = _alive_camps(state)

    if kind == "all_eliminated":
        if all(not is_alive(rt) for rt in state.characters.values()):
            return WinResult(kind="all_eliminated")
        return None

    if kind == "group_eliminated":
        return _group_eliminated(state, config, cards)

    target = str(config.get("camp") or "")
    if not target:
        return None

    if kind == "camp_eliminated":
        if target in alive:
            return None
        others = [camp for camp in alive if camp != target]
        return WinResult(
            kind="camp_eliminated",
            winner=others[0] if len(others) == 1 else "",
            detail=f"{target} 全灭",
        )

    if kind == "camp_superiority":
        if target not in alive:
            return None
        others = sum(count for camp, count in alive.items() if camp != target)
        if others == 0 or alive[target] >= others:
            return WinResult(
                kind="camp_superiority",
                winner=target,
                detail=f"{alive[target]} vs {others}",
            )
        return None

    return None


def summary(
    state: GameState,
    cards: dict[str, CharacterCard],
    kind: str = "ended",
    turn: int = 0,
) -> dict[str, Any]:
    """结算快照：只放事实，不放文采。"""
    characters = []
    for cid, rt in state.characters.items():
        card = cards.get(cid)
        ext = card.ext if card is not None else None
        characters.append(
            {
                "id": cid,
                "name": card.data.name if card is not None else cid,
                "origin": getattr(ext, "origin", ""),
                "path": rt.path or getattr(ext, "path", ""),
                "talents": list(getattr(ext, "talents", []) or []),
                "hp": rt.hp,
                "sanity": rt.sanity,
                "position": rt.position.value,
                "conditions": list(rt.conditions),
                "flags": list(rt.flags),
                "equipment": [
                    {
                        "id": inst.id,
                        "name": inst.name_flavor or inst.template_id,
                        "tier": inst.tier,
                        "quality": inst.quality.value,
                        "attrs": dict(inst.attrs or {}),
                        "tags": list(inst.tags or []),
                    }
                    for inst in (getattr(ext, "equipment", []) or [])
                ],
                "private_goal": getattr(ext, "private_goal", ""),
            }
        )

    return {
        "kind": kind,
        "turn": int(turn),
        "version": state.version,
        "world": {"id": state.world.world_id, "era": state.world.era, "timeline": state.world.timeline},
        "clock": state.clock.model_dump(),
        "stage": state.stage,
        "tension": state.tension,
        "last_scene": state.scene.model_dump(),
        "party": state.party.model_dump(),
        "quests": [q.model_dump() for q in state.quests],
        "characters": characters,
        "survivors": alive_ids(state),
        "wipe": is_wipe(state),
    }


def epilogue_text(state: GameState, cards: dict[str, CharacterCard], kind: str = "ended") -> str:
    """规则版结局：不调模型也能收尾（LLM 失败时用它）。"""
    who = "、".join(
        card.data.name if (card := cards.get(cid)) is not None else cid
        for cid in alive_ids(state)
    ) or "没有人"
    # 结局名单用 `is_eliminated`（正式出局）：濒死还算在场，不该列进阵亡名单
    dead = [
        cards[cid].data.name if cid in cards else cid
        for cid, rt in state.characters.items()
        if is_eliminated(rt)
    ]
    head = (
        "这一局到此为止，所有人都留在了里面。"
        if kind == "wipe"
        else f"这一局到此为止，走出去的是{who}。"
    )
    tail = f"留在里面的：{'、'.join(dead)}。" if dead else "没有人被留下。"
    quests = "；".join(f"{q.title}（{q.status}）" for q in state.quests) or "没有立下什么事"
    return (
        f"{head}第 {state.clock.day} 天 {state.clock.hour:02d}:{state.clock.minute:02d}，"
        f"停在 {state.scene.location_id}。{tail}这段时间里做过的事：{quests}。"
    )
