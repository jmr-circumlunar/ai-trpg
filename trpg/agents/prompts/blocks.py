"""M12 Prompt · 公共积木：身份行、目录块、状态压缩、敌人块、序列化。

其它 prompt 模块一律 `from .blocks import *`，不得各自重写一份。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import json

from ...models import CharacterCard, GameState

_WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]

def identity_line(card: CharacterCard | None, runtime=None, pack=None) -> str:
    """角色的一行身份摘要：`出身 X｜身份 Y　`，没有三层职业时返回空串。"""
    if card is None:
        return ""
    ext = card.ext
    pid = (getattr(runtime, "path", "") or ext.path or "") if runtime is not None else (ext.path or "")
    origin = pack.origin_label(ext.origin) if (pack is not None and ext.origin) else ext.origin
    path = pack.path_label(pid) if (pack is not None and pid) else pid
    parts = [p for p in (origin, path) if p]
    return f"〔{'·'.join(parts)}〕" if parts else ""


def path_catalog_block(pack) -> str:
    """给 GM 看的可选身份清单（含门槛与授予方）——改白名单必须同步这里（契约点 C18）。"""
    paths = getattr(pack, "paths", None) if pack is not None else None
    if not paths:
        return ""
    rows = []
    for item in paths:
        req = item.get("requirements") or {}
        bits = []
        if req.get("stage_min"):
            bits.append(f"幕次≥{req['stage_min']}")
        if req.get("stats_min"):
            bits.append("、".join(f"{k}≥{v}" for k, v in req["stats_min"].items()))
        if req.get("min_grade"):
            bits.append(f"血统≥{req['min_grade']}")
        if req.get("requires_path"):
            bits.append(f"需先为 {'/'.join(req['requires_path'])}")
        gate = f"（{'；'.join(bits)}）" if bits else ""
        by = "、".join(item.get("granted_by") or []) or "剧情"
        rows.append(f"  · {item.get('id')}：{item.get('label') or item.get('id')}{gate}｜授予方：{by}")
    return "\n".join(rows)


def _slot_vocabulary() -> str:
    """slot 的合法取值，**从代码的枚举表生成**，不手写第二份。

    手写的那份曾经漏了 `shield`：代码侧 `SLOT_CLASS` 早就支持盾（归到 armor 档），
    但模型在提示词里看不到这个取值，于是永远写不出带盾的装备——这种漂移不会报错，
    只会让某一类物品**永远不出现**。
    """
    from ...equipment.roll import SLOT_CLASS

    return " / ".join(SLOT_CLASS)


def loot_hint_block(pack=None, eqcfg: dict | None = None) -> str:
    """GM 申报战利品的格式说明：只说「是什么」，数值由代码掷（02.2 §十四）。"""
    vocab = list((eqcfg or {}).get("tag_vocab") or [])
    vocab_txt = " / ".join(vocab) if vocab else "（见 rules/equipment.yaml 的 tag_vocab）"
    tier_max = 3
    era = getattr(pack, "eras", {}).get(getattr(pack, "default_era", "")) if pack is not None else None
    if era is not None:
        tier_max = int(getattr(era, "tier_max", 3) or 3)
    sample = (
        '{"name": "磨损的撬锁工具组", "slot": "tool", '
        '"tags": ["lockpick"], "owner": "chr_p1", "tier_hint": 2}'
    )
    return (
        f"  写法：{sample}\n"
        f"  · name 是你起的具体名字（可以有牌子、磨损、刻字）；slot ∈ {_slot_vocabulary()}\n"
        f"  · tags 只能从封闭词表里挑，最多 3 个：{vocab_txt}\n"
        f"  · tier_hint 可省略，1~{max(tier_max, 1)} 之间表达「大概多强」\n"
        "  · **绝对不要写 atk / def / 成色 / 价格**——这些由代码掷，你只负责它是什么"
    )


def compact_state(
    state: GameState,
    cards: dict[str, CharacterCard],
    max_chars: int = 2000,
    phase_label: str = "",
    pack=None,
) -> str:
    """把 GameState 渲染成紧凑文本（硬状态，不可违背）。"""
    from ...clock.core import date_label  # 纯函数，rules 在 llm 的下层，不算向上依赖

    phase = f"（{phase_label}）" if phase_label else ""
    loc_id = state.scene.location_id
    loc_label = pack.location_label(loc_id, state.scene.region) if pack is not None else loc_id
    region_label = (
        pack.region_label(state.scene.region) if pack is not None else state.scene.region
    )
    lines = [
        # 中文名 + id 都给：模型读名字写叙事，写 delta 时要用回 id
        f"地点：{loc_label}（{loc_id}，{region_label}）",
        f"时间：{date_label(state.clock)}{phase}，天气 {state.clock.weather}",
        f"幕次：{state.stage}/4　张力：{state.tension}/100",
        f"队伍：金币 {state.party.gold}　补给 {state.party.supplies}",
    ]
    if state.party.flags:
        lines.append(f"标记：{', '.join(state.party.flags)}")
    if state.party.reputation:
        rep = ", ".join(f"{k}={v:+d}" for k, v in state.party.reputation.items())
        lines.append(f"声望：{rep}")

    lines.append("角色：")
    for cid, rt in state.characters.items():
        card = cards.get(cid)
        name = card.name if card else cid
        hp_max = card.ext.hp_max if card else "?"
        ident = identity_line(card, rt, pack)
        lines.append(
            f"- {cid} {name}：{ident}HP {rt.hp}/{hp_max}　理智 {rt.sanity}"
            f"　钱 {rt.gold}　补给 {rt.supplies}"
            f"　位置 {rt.position.value}　状态 {','.join(rt.conditions) or '正常'}"
        )

    if state.enemies:
        lines.append(
            "场上威胁："
            + "、".join(
                f"{e.name}({eid}) hp {e.hp}/{e.hp_max} atk {e.atk} def {e.defense}"
                for eid, e in state.enemies.items()
            )
        )

    if state.quests:
        lines.append("任务：")
        for q in state.quests:
            if isinstance(q, dict):
                title, status, progress = q.get("title", "?"), q.get("status", "?"), q.get("progress", 0)
            else:
                title, status, progress = q.title, q.status, q.progress
            lines.append(f"- {title}（{status}, 进度 {progress}）")

    text = "\n".join(lines)
    return text[:max_chars]

def enemies_block(state: GameState) -> str:
    """场上敌人（给 PC 看）。没有结构化信息，模型只能从叙事里猜「这东西能不能打」。"""
    if not state.enemies:
        return ""
    rows = []
    for eid, e in state.enemies.items():
        line = f"- {eid}：{e.name}（hp {e.hp}/{e.hp_max}）"
        if e.condition:
            line += f"　{e.condition}"
        rows.append(line)
    return "\n# 场上敌人（可以攻击；`mode: attack` + `target` 填 id）\n" + "\n".join(rows) + "\n"

def gm_scene_present_npcs_block(npcs: list[dict]) -> str:
    """给 GM 的在场 NPC 候选清单。"""
    if not npcs:
        return "（这个世界还没有 NPC 数据）"
    lines = [
        f"- {n['id']}：{n['name']}｜{'、'.join(n.get('tags') or []) or '无标签'}"
        for n in npcs
    ]
    return "\n".join(lines)

def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)

__all__ = [
    "_WEEKDAY",
    "compact_state",
    "dumps",
    "enemies_block",
    "gm_scene_present_npcs_block",
    "identity_line",
    "loot_hint_block",
    "path_catalog_block",
]
