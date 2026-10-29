"""M13 阶段执行体：声明层（`turn_flow.yaml`）→ 执行层（`loop._phase_*`）的唯一映射。

两个概念必须分清：

| 层 | 是什么 | 谁决定 |
|---|---|---|
| **声明层** | `turn_flow.yaml` 的 `phases[]`，7 个 `kind` 描述这一局跑什么 | 世界包 |
| **执行层** | 本包登记的 6 个执行体，是代码里真正做事的地方 | 框架 |

一个执行体可能承担多个声明阶段——最典型的是 `action`：它同时承接 `declare` 与
`resolve` 两个声明阶段（现有实现里「逐个行动 → 立刻判定 → 把结果写进 briefs」必须在
同一个循环里，拆开就会破坏「后手看到前手结果」的语义）。

| kind | 执行体 | 说明 |
|---|---|---|
| `narration` | `_phase_narration` | 生成场景、申报敌人 |
| `action` | `_phase_action` | NPC 并行 + 玩家逐个行动与判定 |
| `broadcast` | `_phase_broadcast` | 合并成一次 GM 叙事调用 |
| `commit` | `_phase_commit` | 状态合并、掉落、战斗结算 |
| `check` / `collective` / `apply` | 暂无独立执行体 | 由 `plan_turn` 的 `break_if` 与后续批次承接 |

**固定段**（不在上表、每回合必跑）：`director`（回合前）与 `settle`（回合后）——
它们是回合骨架，不是世界可选能力，所以不受 `when` 控制。

要改「先叙事还是先行动」，改 `PHASE_STEPS`，不要改 `step()`。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from .context import TurnContext

# 执行顺序：固定段夹住四个可跳过的执行体
PHASE_STEPS: tuple[str, ...] = (
    "director",
    "narration",
    "action",
    "skill",
    "collective",
    "check",
    "broadcast",
    "commit",
    "settle",
)

# 声明层的 kind → 执行层步骤名（同名，但显式登记，避免以后改名时静默失配）
KIND_TO_STEP: dict[str, str] = {
    "narration": "narration",
    "action": "action",
    "skill": "skill",
    "collective": "collective",
    "apply": "apply",
    "check": "check",
    "broadcast": "broadcast",
    "commit": "commit",
}

# 每回合必跑，不受 when / capabilities 影响
FIXED_STEPS: frozenset[str] = frozenset({"director", "settle"})

__all__ = ["FIXED_STEPS", "KIND_TO_STEP", "PHASE_STEPS", "TurnContext"]
