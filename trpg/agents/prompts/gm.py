"""M12 Prompt · GM：场景描述、叙事、批量叙事。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import json

from ...models import CharacterCard, GameState

from .blocks import *  # noqa: F401,F403

def gm_system(
    world_name: str,
    era: str,
    style: str,
    taboos: list[str],
    world_core: str = "",
    world_orgs: str = "",
    pack=None,
) -> str:
    taboo_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(taboos)) or "（无）"
    extra = _world_extra(pack, "gm")
    return f"""你是「{world_name}」这个 {era} 世界的主持人（GM）。
你不决定成败，只负责把已定的事实编织成有意义的场景与后果。

# 硬约束（违反即无效）
1. 成败与数值已由骰子给出，你只能解释，不能改判。
2. 叙事中不得出现任何具体数值（+3、HP 7、DC 15 一律禁止）。
3. 不得违背【硬状态】中的任何事实。
4. 不得引入【禁忌】中的内容。
5. 失败不得卡死剧情，必须留下至少一条出路。

# 世界设定（常驻，不可违背）
{world_core or "（本世界暂无 core 设定）"}

# 势力（态度 -5 敌对 ~ +5 友好；细节需要时查）
{world_orgs or "（本世界暂无势力数据）"}

# 基调
{style}

# 禁忌
{taboo_text}

# 输出纪律
只输出 JSON，不要 markdown 代码块，不要解释。{extra}"""


def _world_extra(pack, name: str) -> str:
    """世界包对 GM 骨架的补充（B47）：`worlds/<id>/prompts/<name>.md` 存在才生效。

    与 `pc.py::_world_extra` 同一条规矩：**只追加、不替换**——
    变量插值（世界名/基调/禁忌/常驻设定）仍由框架负责。
    """
    root = getattr(pack, "root", None)
    if root is None:
        return ""
    try:
        from pathlib import Path

        path = Path(root) / "prompts" / f"{name}.md"
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return f"\n\n# 本世界的补充设定（prompts/{name}.md）\n{text}\n"


def _capability(pack, name: str) -> bool:
    """世界是否开了某个系统（B31）。没有 pack 时视为开启——与改造前完全一致。"""
    if pack is None:
        return True
    try:
        from ...rulespack import assemble

        return assemble(pack).has(name)
    except Exception:  # noqa: BLE001
        return True


def gm_scene_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    turn: int,
    phase_label: str = "",
    npcs: list[dict] | None = None,
    seed_hint: dict | None = None,
    pack=None,
    allowed_event_categories: list[str] | None = None,
    event_hints: str = "",
) -> str:
    npcs_block = gm_scene_present_npcs_block(npcs or [])

    # B31：输出 schema 按能力裁剪。关掉 combat 的世界（狼人杀）不该被要求申报敌人，
    # 否则模型会照 schema 输出 enemies，而 `state.enemies` 在那边不可写，白白被拒一次。
    if _capability(pack, "combat"):
        enemies_decl = (
            "**如果这个场景里有会威胁玩家的东西，必须现在就用 `enemies` 申报**"
            "（正在靠近的、被关着的、躲在暗处的都算）。\n"
            "你在这里申报，玩家才能在本回合看到它并选择打或躲；"
            "只在叙事里提一句，玩家就没法反应。没有威胁时写空数组。\n"
        )
        enemies_field = (
            ',\n "enemies": [{"id": "en_1", "name": "覆着管线的实验体",'
            ' "threat": "normal"}]'
        )
        threat_line = (
            "\n`threat` ∈ weak / normal / strong / boss（数值由代码折算）；"
            "同一场战斗沿用同一个 id。"
        )
    else:
        enemies_decl = ""
        enemies_field = ""
        threat_line = ""
    # 当前区域的既有事实：地点表 / 势力 / 传闻。以前写了 region_text() 却从没调用，
    # 结果 GM 只知道 `loc_gate` 这个 id，于是地点与组织全靠即兴编。
    region_block = ""
    if pack is not None:
        try:
            detail = str(pack.region_text(state.scene.region) or "").strip()
        except Exception:  # noqa: BLE001
            detail = ""
        if detail:
            region_block = f"\n# 当前区域（已确立的事实，不可违背）\n{detail[:1200]}\n"
    seed_block = ""
    if seed_hint:
        consequences = " / ".join(seed_hint.get("consequences") or []) or "（无）"
        seed_block = f"""
# 本回合可参考的剧情骨架（可参考、可改造、可不用；不要生硬照搬）
类型：{seed_hint.get('type', '')}
骨架：{seed_hint.get('skeleton', '')}
可能的后果：{consequences}
"""
    # S5-4：GM 的 schema 按**本回合允许的事件类别**裁剪（不只是 enemies/loot）。
    # 区域自治 low = 只能描述既有事实；mid = 可加本回合候选类别；high = 全部类别。
    # 没有允许类别时**整段不出现**（空段落只会让模型自己编事件）。
    categories = [str(x) for x in (allowed_event_categories or []) if str(x)]
    if categories:
        events_block = (
            "\n# 本回合允许引入的事件类型（只能写这些；不要自造别的类型）\n"
            + "、".join(categories)
            + "\n"
        )
    else:
        events_block = "\n# 本回合不引入新事件：只描述已经成立的事实。\n"
    # S7-1：情境提醒来自**文案表**（世界包声明）。没有提醒 → 整段不出现
    # （缺省零变化：现有对局 prompt 逐字节不变）。
    hints_text = str(event_hints or "").strip()
    hints_block = f"\n# 情境提醒\n{hints_text}\n" if hints_text else ""
    return f"""# 硬状态（事实，不可违背）
{compact_state(state, cards, phase_label=phase_label, pack=pack)}

# 本回合在场 NPC 候选（只能从这里选，最多 2 个）
{npcs_block}
{region_block}
{seed_block}
{events_block}{hints_block}
# 本回合任务
describe_scene（第 {turn} 回合）：写 2~4 句当前场景，给玩家一个可行动的局面。
不要替玩家做决定，不要宣布任何成功或失败的结果。
另外挑 0~2 个此刻应该在场的 NPC（他们会各自行动，不要替他们说话）。

{enemies_decl}# 输出格式
{{"narration": "…",
 "options": [{{"id": "opt_1", "label": "把复测单折进袖口", "hint": "隐蔽 · 手要快"}}],
 "present_npcs": ["npc_id"]{enemies_field}}}

`options` 是**玩家此刻能立刻做的事**，1~4 条：
- 必须是具体动作（"把复测单折进袖口"），不是态度或感想（"保持警惕"）
- 必须来自已确立的事实（在场的人、手上的东西、刚发生的事），不许凭空发明道具或人物
- `hint` 用 4~10 字点出代价或风险（"隐蔽 · 手要快"），没把握就留空串
- 不要把「离开/等待」凑数，除非此刻真的只有这条路
{threat_line}"""


def gm_narrate_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    action_text: str,
    outcome: str,
    dice_line: str,
    turn: int,
    actor_label: str = "",
    pack=None,
    condition_vocab: list[str] | None = None,
) -> str:
    who = f"{actor_label}：" if actor_label else ""
    cond_text = "、".join(condition_vocab or []) or "（词表未定义，用简短状态词）"
    return f"""# 硬状态（事实，不可违背）
{compact_state(state, cards, pack=pack)}

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
- characters.<id>.conditions（**只能取这些词**：{cond_text}；add 追加一个，写叙事短语会被丢弃）
- characters.<id>.inventory（物品 id 列表）
- characters.<id>.position（只能是 "scene" / "away" / "dead"，不能自造词）
- characters.<id>.gold / characters.<id>.supplies（整数，个人的钱）：**花钱办事扣本人**，
  多人凑钱的大开销才动 party
- party.gold / party.supplies（整数，**团队公共仓**，只在凑钱办事时使用）
- party.flags（**字符串列表，用 add 追加单个字符串**，如 "tracking_thin_man"；不要写对象）
- party.reputation（{{势力名: 整数}}）
- tension（整数）、stage（整数）、scene.location_id（已有地点 id）
- quests（任务对象列表）：**推进已有任务只给 `{{"id": "q_x", "progress": 2}}` 或 `{{"id": "q_x", "status": "done"}}`**；
  新建任务必须带 `id` + `title`；不要为了改一个字段把整个任务重写一遍。
  **同一件事只能有一个 id**，不要换个 id 重报；任务栏最多 8 条，多出来的会被丢掉
（不要修改 version / session_id / world）
约束：单回合 tension 变化不超过 ±10；没有明确的大事时不要扣 HP；不要每回合都给状态变更。

# 输出格式
{{"narration": "…", "narration_meta": {{"beat": "rising"}},
 "state_delta": {{"turn": {turn}, "changes": [{{"path": "tension", "op": "add", "value": 3}}]}}}}"""

def gm_narrate_batch_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    items: list[dict],
    turn: int,
    phase_label: str = "",
    steps: list[dict] | None = None,
    npc_moves: list[dict] | None = None,
    pack=None,
    loot_hint: str = "",
    condition_vocab: list[str] | None = None,
) -> str:
    """一次处理本回合所有玩家的行动与判定（避免 N 次串行调用）。"""
    catalog = path_catalog_block(pack)
    cond_text = "、".join(condition_vocab or []) or "（词表未定义，用简短状态词）"

    # B31：关掉 equipment 的世界（狼人杀）不该被要求填 loot —— 填了也写不进 inventory
    if _capability(pack, "equipment"):
        loot_block = (
            "# 本回合战利品（可选，顶层字段 `loot`，**不属于 state_delta**）\n"
            "本回合确实拿到的东西才写，**只给名字 / slot / tag，不要写数值**"
            "（tier、成色、属性、价格由代码掷）：\n"
            f"{loot_hint or '  （本回合没有战利品就写 []）'}\n"
        )
        loot_field = (
            ',\n "loot": [{"name": "磨损的撬锁工具组", "slot": "tool",'
            ' "tags": ["lockpick"], "owner": "chr_p1"}]'
        )
        loot_tail = (
            '\n\n上面 `loot` 的示例表示"**有就照这样填**"；'
            "本回合确实没有战利品时，才写空数组 `[]`。"
        )
    else:
        loot_block = ""
        loot_field = ""
        loot_tail = ""
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
{compact_state(state, cards, phase_label=phase_label, pack=pack)}

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
- characters.<id>.conditions（**只能取这些词**：{cond_text}；add 追加一个，写叙事短语会被丢弃）
- characters.<id>.inventory（物品 id 列表）
- characters.<id>.position（只能是 "scene" / "away" / "dead"）
- characters.<id>.path（**只能 set**；换身份必须叙事里出现授予方，且门槛已满足）：
{catalog or "  （本世界未启用身份阶梯，不要写这条）"}
- characters.<id>.gold / characters.<id>.supplies（整数，个人的钱）：**花钱办事扣本人**，
  多人凑钱的大开销才动 party
- party.gold / party.supplies（整数，**团队公共仓**，只在凑钱办事时使用）
- party.flags（**字符串列表，用 add 追加单个字符串**）
- party.reputation（{{势力名: 整数}}）
- tension（整数）、stage（整数）、scene.location_id（已有地点 id）
- quests（任务对象列表）：**推进已有任务只给 `{{"id": "q_x", "progress": 2}}` 或 `{{"id": "q_x", "status": "done"}}`**；
  新建任务必须带 `id` + `title`；不要为了改一个字段把整个任务重写一遍。
  **同一件事只能有一个 id**，不要换个 id 重报；任务栏最多 8 条，多出来的会被丢掉
- **clock.advance（分钟数，只能前进）**：按本回合实际消耗申报，档位 {step_detail}
（不要修改 version / session_id / world；不要直接改 clock.day/hour/minute）
约束：单回合 tension 变化不超过 ±10；没有明确的大事时不要扣 HP。

濒死与死亡（代码判定，你只负责写）：
- hp 归 0 的角色会被标记 `dying`，最多 2 回合内可以救；**濒死的人不能行动**。
- 叙事里要写清「还来得及救」还是「已经在变冷」，但不要替代码宣布死亡。
- 只有把 hp 加回去才算救活；你不能自己删掉 `dying`。

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

{loot_block}# 敌人不在这一步申报
敌人只在开场 SCENE 申报（C25：必须早于玩家决策，否则玩家当回合打不到它）。
本回合叙事里杀出、现身、被发现的威胁，**只写进叙事**，下一回合 SCENE 会重新申报进场；
已有的敌人血量只由战斗结算改变，你不能在这里改数值。

# 输出格式
{{"narrations": [{{"actor_id": "chr_p1", "text": "…"}}],
 "narration_meta": {{"beat": "rising"}}{loot_field},
 "state_delta": {{"turn": {turn}, "changes": [{{"path": "tension", "op": "add", "value": 3}}]}}}}{loot_tail}"""
