"""Prompt 组装（骨架见设计文档 02.3）。

原则：
- 硬状态必注入，叙事片段可裁剪
- 叙事里不得出现具体数值（数值由前端渲染徽章）
- PC 只看公开叙述与自己的私有目标（信息隔离）
"""

from __future__ import annotations

import json

from ..models import CharacterCard, GameState

_WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]


def compact_state(
    state: GameState,
    cards: dict[str, CharacterCard],
    max_chars: int = 2000,
    phase_label: str = "",
) -> str:
    """把 GameState 渲染成紧凑文本（硬状态，不可违背）。"""
    phase = f"（{phase_label}）" if phase_label else ""
    lines = [
        f"地点：{state.scene.location_id}（{state.scene.region}）",
        f"时间：第 {state.clock.day} 天 {state.clock.hour:02d}:{state.clock.minute:02d}"
        f"{phase}，天气 {state.clock.weather}",
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
        lines.append(
            f"- {cid} {name}：HP {rt.hp}/{hp_max}　理智 {rt.sanity}"
            f"　位置 {rt.position.value}　状态 {','.join(rt.conditions) or '正常'}"
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


def gm_system(world_name: str, era: str, style: str, taboos: list[str]) -> str:
    taboo_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(taboos)) or "（无）"
    return f"""你是「{world_name}」这个 {era} 世界的主持人（GM）。
你不决定成败，只负责把已定的事实编织成有意义的场景与后果。

# 硬约束（违反即无效）
1. 成败与数值已由骰子给出，你只能解释，不能改判。
2. 叙事中不得出现任何具体数值（+3、HP 7、DC 15 一律禁止）。
3. 不得违背【硬状态】中的任何事实。
4. 不得引入【禁忌】中的内容。
5. 失败不得卡死剧情，必须留下至少一条出路。

# 基调
{style}

# 禁忌
{taboo_text}

# 输出纪律
只输出 JSON，不要 markdown 代码块，不要解释。"""


def gm_scene_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    turn: int,
    phase_label: str = "",
) -> str:
    return f"""# 硬状态（事实，不可违背）
{compact_state(state, cards, phase_label=phase_label)}

# 本回合任务
describe_scene（第 {turn} 回合）：写 2~4 句当前场景，给玩家一个可行动的局面。
不要替玩家做决定，不要宣布任何成功或失败的结果。

# 输出格式
{{"narration": "…", "hooks": ["…"]}}"""


def gm_narrate_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    action_text: str,
    outcome: str,
    dice_line: str,
    turn: int,
    actor_label: str = "",
) -> str:
    who = f"{actor_label}：" if actor_label else ""
    return f"""# 硬状态（事实，不可违背）
{compact_state(state, cards)}

# 本回合玩家行动与判定（已定，不可改）
行动者：{actor_label or "某角色"}
行动：{action_text}
判定：{dice_line} → 结果 {outcome}

结果档位含义：
- crit_success：达成意图并额外获益（新线索 / 省资源 / 敌方破绽）
- success：达成意图
- fail_forward：达成意图，但必须付出代价（HP / 时间 / 暴露 / 失物）
- failure：未达成，引入后果，但必须留一条出路

# 本回合任务
narrate_result（第 {turn} 回合）：依据判定结果写 2~4 句后果叙事，并给出结构化状态变更。
state_delta 只能使用以下路径，且必须符合类型：
- characters.<id>.hp（整数，0 ~ 上限）
- characters.<id>.sanity（整数）
- characters.<id>.conditions（字符串列表，add 追加一个状态词）
- characters.<id>.inventory（物品 id 列表）
- characters.<id>.position（只能是 "scene" / "away" / "dead"，不能自造词）
- party.gold / party.supplies（整数）
- party.flags（**字符串列表，用 add 追加单个字符串**，如 "tracking_thin_man"；不要写对象）
- party.reputation（{{势力名: 整数}}）
- tension（整数）、stage（整数）、scene.location_id（已有地点 id）、quests（任务对象列表）
（不要修改 version / session_id / world）
约束：单回合 tension 变化不超过 ±10；没有明确的大事时不要扣 HP；不要每回合都给状态变更。

# 输出格式
{{"narration": "…", "narration_meta": {{"beat": "rising"}},
 "state_delta": {{"turn": {turn}, "changes": [{{"path": "tension", "op": "add", "value": 3}}]}}}}"""


def pc_system(card: CharacterCard, skills: list[str], eras: str = "") -> str:
    ext = card.ext
    examples = (card.data.mes_example or "").strip()[:600]
    return f"""# 你在扮演
{card.name}｜{card.data.personality}

# 背景
{card.data.description}

# 说话方式（模仿例句的句式与语气）
{examples or "（无例句，保持口吻一贯）"}

# 你的秘密（对你之外的人不可见）
目标：{ext.private_goal}
恐惧：{ext.fear}
底线：{ext.bottom_line}

# 规则
- 只输出 JSON，不要解释。
- thought 是私下盘算，别人看不到；action 只写你要做什么、怎么做，不写结果。
- skill 只能从这些里选：{"、".join(skills)}；不适用就填 null。
- 不得替其他角色行动，不得宣布自己成功。
- **你必须主动推进自己的目标**：整回合只做「观察」「等待」是浪费。没有更好的办法时，就制造一个机会。
- 篇幅：thought ≤ 40 字，action ≤ 60 字，approach ≤ 20 字。**JSON 字符串内不要换行**（用空格代替），否则会被截断。
{eras}"""


def pc_user(state: GameState, card: CharacterCard, public_recent: list[str], turn: int) -> str:
    ext = card.ext
    hp_max = ext.hp_max
    inv = "、".join(e.name_flavor or e.template_id for e in ext.equipment) or "空手"
    recent = "\n".join(f"- {x}" for x in public_recent[-2:]) or "（故事刚开始）"

    return f"""# 你知道的（最近两回合公开叙述）
{recent}

# 你的状态
HP {ext.hp}/{hp_max}　理智 {ext.sanity}/{ext.sanity_max}　装备：{inv}
当前地点：{state.scene.location_id}（{state.scene.region}）

# 本回合
第 {turn} 回合，根据上面的局面决定你要做什么。

# 输出格式
{{"thought": "…", "action": "…", "approach": "…", "skill": "潜行", "target": null, "intent": "normal"}}"""


def gm_narrate_batch_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    items: list[dict],
    turn: int,
    phase_label: str = "",
    steps: list[dict] | None = None,
) -> str:
    """一次处理本回合所有玩家的行动与判定（避免 N 次串行调用）。"""
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. [{it['actor_id']}] {it['label']}：{it['action']}\n"
            f"   判定：{it['dice_line']} → 结果 {it['outcome']}"
        )
    actions_block = "\n".join(lines)

    step_hint = " / ".join(str(s["minutes"]) for s in (steps or [])) or "5 / 10 / 30 / 60 / 240 / 480"
    step_detail = "、".join(
        f"{s['minutes']}（{s['label']}）" for s in (steps or [])
    ) or "5 / 10 / 30 / 60 / 240 / 480"

    return f"""# 硬状态（事实，不可违背）
{compact_state(state, cards, phase_label=phase_label)}

# 本回合全部行动与判定（已定，不可改）
{actions_block}

结果档位含义：
- crit_success：达成意图并额外获益（新线索 / 省资源 / 敌方破绽）
- success：达成意图
- fail_forward：达成意图，但必须付出代价（HP / 时间 / 暴露 / 失物）
- failure：未达成，引入后果，但必须留一条出路

# 本回合任务
narrate_result（第 {turn} 回合）：
1. 为**每一个** actor 各写 1~3 句后果叙事，按 actor_id 对应；
2. 各人的叙事要互相呼应（他们同处一地，可能互相看见彼此的结果）；
3. 最后给出本回合的合并状态变更。

state_delta 只能使用以下路径，且必须符合类型：
- characters.<id>.hp（整数，0 ~ 上限）
- characters.<id>.sanity（整数）
- characters.<id>.conditions（字符串列表，add 追加一个状态词）
- characters.<id>.inventory（物品 id 列表）
- characters.<id>.position（只能是 "scene" / "away" / "dead"）
- party.gold / party.supplies（整数）
- party.flags（**字符串列表，用 add 追加单个字符串**）
- party.reputation（{{势力名: 整数}}）
- tension（整数）、stage（整数）、scene.location_id（已有地点 id）、quests（任务对象列表）
- **clock.advance（分钟数，只能前进）**：按本回合实际消耗申报，档位 {step_detail}
（不要修改 version / session_id / world；不要直接改 clock.day/hour/minute）
约束：单回合 tension 变化不超过 ±10；没有明确的大事时不要扣 HP。

# 时间申报（必做）
判断本回合行动实际花了多久，用 `clock.advance` 申报（只取 {step_hint} 中的一个值）。
只需一次，不要为每个角色各报一次；如果多人分散行动，取最长的那条。
例：{{"path": "clock.advance", "op": "add", "value": 30}}

# 输出格式
{{"narrations": [{{"actor_id": "chr_p1", "text": "…"}}],
 "narration_meta": {{"beat": "rising"}},
 "state_delta": {{"turn": {turn}, "changes": [{{"path": "tension", "op": "add", "value": 3}}]}}}}"""


def arbiter_user(narration: str, state_text: str) -> str:
    return f"""检查下面的叙事是否与硬状态矛盾，只查 4 条，不管文采：
1. 是否让已死或已离场的角色行动？
2. 是否使用了不存在的物品，或消耗了已耗尽的资源？
3. 是否与地点、时间矛盾？
4. 是否出现了具体数值？

# 硬状态
{state_text}

# 待检查叙事
{narration}

# 输出格式
{{"ok": true, "violations": [{{"type": "dead_actor", "detail": "…"}}]}}"""


def chronicler_user(turn: int, narration: str) -> str:
    return f"""把第 {turn} 回合的原文压缩为 1~3 条事件摘要，每条必须含：谁 / 在哪 / 做了什么 / 后果。
同时抽取实体（人物 / 地点 / 物品）与重要度（1~10）。

# 原文
{narration}

# 输出格式
{{"summaries": [{{"text": "…", "entities": ["…"], "importance": 5}}], "new_entities": []}}"""


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
