"""M12 Prompt · 导演与结语：幕次/主线决策、开局、结局文本。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import json

from ...models import CharacterCard, GameState
from ...models.state import is_eliminated

from .blocks import *  # noqa: F401,F403

def epilogue_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    kind: str = "ended",
    turn: int = 0,
) -> str:
    """结局请求：收束这一局，不开启下一局。"""
    reason = "全灭" if kind == "wipe" else "手动结束"
    dead = [
        (cards[cid].data.name if cid in cards else cid)
        for cid, rt in state.characters.items()
        if is_eliminated(rt)
    ]
    dying = [
        (cards[cid].data.name if cid in cards else cid)
        for cid, rt in state.characters.items()
        if rt.hp <= 0 and rt.position.value != "dead"
    ]
    return f"""# 结局（第 {turn} 回合，原因：{reason}）

## 硬状态
{compact_state(state, cards)}

已死：{'、'.join(dead) or "无"}　濒死未救：{'、'.join(dying) or "无"}

# 任务
写 2~4 段收束：这一局最后停在哪、谁付出了什么代价、留下什么没解决。
- 不要出现任何数值（HP、DC、tier 都不许写）。
- 不要写「未完待续」，不要替下一个局许诺。
- 死亡的人就写死，不要暗示复活。

# 输出格式
{{"epilogue": "…"}}"""

def director_system(world_name: str, style: str = "", taboos: list[str] | None = None) -> str:
    taboo_text = "、".join(taboos or []) or "（无）"
    return f"""你是「{world_name}」的导演（Director）。你不写叙事，只做结构决策。

# 硬约束
1. 只输出 JSON，不要 markdown，不要解释。
2. 你只决定「发生什么事的骨架」，**不决定成败、不写数值**——判定与数值由代码给出。
3. 主线要能在 3~4 幕内讲完，并给玩家留出自由行动的空间。
4. 禁忌：{taboo_text}
5. 风格：{style or "冷峻、克制"}

# 输出格式
{{"main": "一句话主线", "quests": [{{"id": "q_main", "title": "…", "progress": 0}}]}}"""


def director_open_user(
    state: GameState,
    cards: dict[str, CharacterCard],
    seeds: list[dict] | None = None,
    npcs: list[dict] | None = None,
) -> str:
    seed_rows = "\n".join(
        f"- {s.get('id')}（{s.get('type')}）：{s.get('skeleton')}"
        for s in (seeds or [])[:8]
    ) or "（无种子）"
    npc_rows = "、".join(str(n.get("name")) for n in (npcs or [])[:12]) or "（无）"
    cast = "、".join(
        f"{c.data.name}（{c.ext.profession or c.ext.origin or '角色'}）" for c in cards.values()
    )
    return f"""# 本局前提
世界：{state.world.world_id}　时间点：{state.world.timeline}
地点：{state.scene.location_id}（{state.scene.region}）
时间：第 {state.clock.day} 天 {state.clock.hour:02d}:{state.clock.minute:02d}
开场：{state.scene.description}

# 玩家
{cast}

# 这个世界里的人（可以动用，但不要替玩家做决定）
{npc_rows}

# 可参考的事件骨架（取其冲突结构，不要照抄）
{seed_rows}

# 任务
为本局设计 1 条主线 + 1~2 条支线。title ≤ 18 字，progress 一律 0，id 自拟（q_ 开头）。
只输出 JSON。"""
