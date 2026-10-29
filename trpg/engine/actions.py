"""M13 · **统一动作提交入口**（人类与 AI 同构）。

抄的是 boardgame.io 的源码级铁证（E-1）：bot 与人共用 `store.dispatch` ——
真人点按钮与 bot 提交动作是**同一条路径**，没有「AI 专用通道」。

我们的同构长这样：

1. **一个解析器**：两种来源都产出 `PlayerAction`（`PlayerAction.from_raw`）；
2. **一个提交函数**：`submit_action()` 负责登记与幂等，人类与 AI 都调它；
3. **行动者由引擎决定**：`turn_flow.yaml` 的阶段序列说了算，
   提交方只负责「交什么动作」，不决定「轮到谁」；
4. **技能选择同 schema**：人的技能选择与 AI 共用 M12 的 `SkillChoiceOut`；
5. **幂等**：同一行动者同一回合重复提交 → 返回既有动作，**不产生第二次行动**。

`/pending` 与 `/action` 的对外契约**不变**：本模块只在内核里把两条路合成一条。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.action import PlayerAction, SkillChoice

#: 提交来源：`human`（真人）/ `ai`（模型）/ `ghost`（代打）。
SOURCES = ("human", "ai", "ghost")


@dataclass
class ActionSubmission:
    """一次提交的结果。`duplicate=True` 表示这次提交被幂等挡掉了。"""

    action: PlayerAction
    source: str = "ai"
    accepted: bool = True
    duplicate: bool = False
    reason: str = ""


def parse_skill_choice(raw: dict[str, Any] | None) -> tuple[str, str]:
    """技能选择：**人机共用同一个契约**（M01 `SkillChoice`，字段与 M12 `SkillChoiceOut` 一致）。

    返回 `(skill, target)`；没给就是空串（= 不发动）。
    """

    data = raw or {}
    choice = SkillChoice.model_validate(
        {
            "skill": str(data.get("skill") or "").strip(),
            "target": str(data.get("target") or "").strip(),
        }
    )
    return choice.skill, choice.target


def submit_action(
    actor_id: str,
    raw: Any,
    *,
    source: str = "ai",
    turn: int = 0,
    registry: dict[str, Any] | None = None,
) -> ActionSubmission:
    """**统一动作提交**：人类与 AI 都走这里。

    - `raw` 可以是原始 dict（人提交的 JSON / 模型输出的 dict）或 `PlayerAction`；
    - `registry` 是「本回合已提交的动作」登记（`{actor_id: PlayerAction}`），
      重复提交同一行动者 → 返回**既有动作**（`accepted=False`, `duplicate=True`）；
    - `source` 只用于留痕与日志，不影响动作语义。
    """
    actor = str(actor_id or "").strip()
    if not actor:
        raise ValueError("动作提交必须带行动者 id")

    reg = registry if registry is not None else {}
    existing = reg.get(actor)
    if existing is not None:
        return ActionSubmission(
            action=existing,
            source=source,
            accepted=False,
            duplicate=True,
            reason="该行动者本回合已提交，返回既有动作（幂等）",
        )

    if isinstance(raw, PlayerAction):
        action = raw.model_copy(update={"actor_id": actor})
    else:
        data = dict(raw or {})
        skill, target = parse_skill_choice(data)
        if skill and not data.get("skill"):
            data["skill"] = skill
        if target and not data.get("target"):
            data["target"] = target
        action = PlayerAction.from_raw(actor, data)

    reg[actor] = action
    return ActionSubmission(action=action, source=source, accepted=True)


__all__ = ["ActionSubmission", "SOURCES", "parse_skill_choice", "submit_action"]
