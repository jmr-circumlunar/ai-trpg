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
    npcs: list[dict] | None = None,
    seed_hint: dict | None = None,
) -> str:
    npcs_block = gm_scene_present_npcs_block(npcs or [])
    seed_block = ""
    if seed_hint:
        consequences = " / ".join(seed_hint.get("consequences") or []) or "（无）"
        seed_block = f"""
# 本回合可参考的剧情骨架（可参考、可改造、可不用；不要生硬照搬）
类型：{seed_hint.get('type', '')}
骨架：{seed_hint.get('skeleton', '')}
可能的后果：{consequences}
"""
    return f"""# 硬状态（事实，不可违背）
{compact_state(state, cards, phase_label=phase_label)}

# 本回合在场 NPC 候选（只能从这里选，最多 2 个）
{npcs_block}
{seed_block}

# 本回合任务
describe_scene（第 {turn} 回合）：写 2~4 句当前场景，给玩家一个可行动的局面。
不要替玩家做决定，不要宣布任何成功或失败的结果。
另外挑 0~2 个此刻应该在场的 NPC（他们会各自行动，不要替他们说话）。

# 输出格式
{{"narration": "…", "hooks": ["…"], "present_npcs": ["npc_id"]}}"""


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


AGENT_PROTOCOL = """# 你可以调用工具，也可以直接提交行动
每次只输出一个 JSON 对象：

- 提交行动（**绝大多数情况直接用它**）
  {{"type": "final", "thought": "…", "action": "…", "approach": "…", "skill": "潜行", "target": null, "intent": "normal"}}

- 检索自己的记忆（**默认不要用**；只在需要回忆 3 回合以前的事、确认旧道具、接续旧剧情线时才调）
  {{"type": "tool", "tool": "recall", "args": {{"query": "钟楼 铁栅"}}}}

- 观察场景细节
  {{"type": "tool", "tool": "look", "args": {{"focus": "巷口"}}}}

- 看自己的状态
  {{"type": "tool", "tool": "check_self", "args": {{}}}}

# 纪律
- 最多 3 步；正常情况**第一步就 final**。
- 最近几回合发生的事你本来就记得（在上下文里），不需要 recall。
- 想做一件事但缺信息时，才调 look 或 recall。
"""


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
- **thought 必须是中文**，且是角色的内心话（第一人称、口语、可带偏见），不是旁白也不是推理稿。≤ 40 字。
- 篇幅：thought ≤ 40 字，action ≤ 60 字，approach ≤ 20 字。**JSON 字符串内不要换行**（用空格代替），否则会被截断。

{AGENT_PROTOCOL}
{eras}"""


def pc_user(
    state: GameState,
    card: CharacterCard,
    public_recent: list[str],
    turn: int,
    memories: list[str] | None = None,
    chapter: list[str] | None = None,
    previous: list[str] | None = None,
) -> str:
    ext = card.ext
    hp_max = ext.hp_max
    inv = "、".join(e.name_flavor or e.template_id for e in ext.equipment) or "空手"
    recent = "\n".join(f"- {x}" for x in public_recent[-3:]) or "（故事刚开始）"
    chapter_block = (
        "\n".join(f"- {c}" for c in chapter) if chapter else "（还没有更早的回顾）"
    )
    # 短期记忆分层：近 3 回合原文常驻，更早靠摘要；具体长记忆要调 recall
    memory_block = ""
    if memories:
        memory_block = "\n# 你临时想起的事（刚调过 recall）\n" + "\n".join(
            f"- {m}" for m in memories
        )
    previous_block = ""
    if previous:
        previous_block = "\n# 本回合已经发生（排在你前面的队友）\n" + "\n".join(
            f"- {x}" for x in previous
        )

    return f"""# 更早发生了什么（分章回顾）
{chapter_block}

# 最近几回合（你亲身经历，完全记得）
{recent}
{previous_block}{memory_block}

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
    npc_moves: list[dict] | None = None,
) -> str:
    """一次处理本回合所有玩家的行动与判定（避免 N 次串行调用）。"""
    npc_block = "（本回合没有 NPC 行动）"
    if npc_moves:
        rows = []
        for m in npc_moves:
            row = f"- {m.get('name')}：{m.get('action') or '没有动作'}"
            if m.get("speech"):
                row += f"　说：「{m['speech']}」"
            rows.append(row)
        npc_block = "\n".join(rows)

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

# 本回合 NPC 的动向（事实，请织进叙事，不要改写他们的意图）
{npc_block}

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
判断本回合实际花了多久，用 `clock.advance` 申报（只取 {step_hint} 中的一个值）。
只需一次，不要为每个角色各报一次；多人分散行动时，取最长的那条。

时间**不固定**，按剧情判断：
- 短暂交锋、几句话 → 5
- 搜索一处、谈一件事 → 10
- 审问、拆解、包扎 → 30
- 换城区、穿越 → 60
- 长途、深入地底 → 240
- **扎营睡觉 → 480；睡到下一个时段 → 720；整整一天/昏迷醒来 → 1440**
- 夜间被事件打断（比如刚躺下就有人敲门）→ 只报 5~30，不要直接跳到天亮

例：{{"path": "clock.advance", "op": "add", "value": 480}}

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


def gm_scene_present_npcs_block(npcs: list[dict]) -> str:
    """给 GM 的在场 NPC 候选清单。"""
    if not npcs:
        return "（这个世界还没有 NPC 数据）"
    lines = [
        f"- {n['id']}：{n['name']}｜{'、'.join(n.get('tags') or []) or '无标签'}"
        for n in npcs
    ]
    return "\n".join(lines)


def npc_system(npc: dict, world_name: str) -> str:
    attitude = int(npc.get("attitude", 0) or 0)
    tone = "敌对" if attitude <= -2 else ("冷淡" if attitude < 1 else "友善")
    org = npc.get("org") or "无"
    return f"""# 你在扮演 {npc.get('name')}（{world_name} 的 NPC）

组织：{org}
你的目标：{npc.get('goal')}
你的秘密（不会主动说出口，但会影响你的话）：{npc.get('secret')}
对玩家的态度：{attitude}（{tone}）
性格：{'、'.join(npc.get('tags') or []) or '普通'}

# 规则
- 只输出 JSON，全部中文。
- action：你这一回合做了什么（≤40 字，第三人称看不出你的动机，只写外在行为）。
- speech：你当场说的话（≤60 字；不想说话就填空字符串）。
- **你只为自己的目标行动**，不必帮玩家；可以隐瞒、敷衍、绕开、说谎。
- 不得替玩家决定，不得宣布任何成功或失败。

# 输出格式
{{"action": "…", "speech": "…"}}"""


def npc_user(state: GameState, npc: dict, public_recent: list[str], turn: int) -> str:
    recent = "\n".join(f"- {x}" for x in public_recent[-2:]) or "（故事刚开始）"
    return f"""# 现在的局面（第 {turn} 回合）
地点：{state.scene.location_id}（{state.scene.region}）
时间：第 {state.clock.day} 天 {state.clock.hour:02d}:{state.clock.minute:02d}

# 最近发生了什么
{recent}

# 你的任务
玩家就在附近。按你的目标做一个动作（可以离开、可以搭话、可以装作没看见）。
只输出 JSON。"""


def chronicler_user(turn: int, narration: str) -> str:
    return f"""把第 {turn} 回合的原文压缩为 1~3 条事件摘要，每条必须含：谁 / 在哪 / 做了什么 / 后果。
同时抽取实体（人物 / 地点 / 物品）与重要度（1~10）。

# 原文
{narration}

# 输出格式
{{"summaries": [{{"text": "…", "entities": ["…"], "importance": 5}}], "new_entities": []}}"""


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
