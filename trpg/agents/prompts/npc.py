"""M12 Prompt · NPC 与仲裁：NPC 提示、硬矛盾检查（Arbiter）、编年史压缩。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import json

from ...models import CharacterCard, GameState

from .blocks import *  # noqa: F401,F403

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

def npc_system(npc: dict, world_name: str, relations_text: str = "") -> str:
    attitude = int(npc.get("attitude", 0) or 0)
    tone = "敌对" if attitude <= -2 else ("冷淡" if attitude < 1 else "友善")
    org = npc.get("org") or "无"
    # 关系段（S5-1 补齐 B8）：关系是**带时间区间的事实**，过期的不给模型看。
    # 没给关系就整段不出现 —— 空段落只会让模型编造关系。
    relations_block = (
        f"\n你与别人的关系（带时间区间；只按仍在成立的关系行事）：\n{relations_text}\n"
        if relations_text
        else ""
    )
    return f"""# 你在扮演 {npc.get('name')}（{world_name} 的 NPC）

组织：{org}
你的目标：{npc.get('goal')}
你的秘密（不会主动说出口，但会影响你的话）：{npc.get('secret')}
对玩家的态度：{attitude}（{tone}）
性格：{'、'.join(npc.get('tags') or []) or '普通'}{relations_block}
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
