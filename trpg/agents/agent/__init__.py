"""M12 Agent 与交互 · 业务层 AgentLLM（GM / 玩家 / NPC / 编年史）。

组合式拆分：常量与模型调用在 `base.py`，工具在 `tools.py`，回合运行时在 `runtime.py`。
对外只暴露 `AgentLLM` 与 `TASK_TOKENS`，保持原 `trpg.agents.agent` 导入路径。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

from .base import DEFAULT_TABOOS, SUMMARY_KEEP, TASK_TIER, TASK_TOKENS, AgentBase
from .runtime import RuntimeMixin
from .tools import ToolMixin


class AgentLLM(RuntimeMixin, ToolMixin, AgentBase):
    """多 Agent 业务层：调模型、拼上下文、执行工具。"""


__all__ = ["AgentLLM", "TASK_TOKENS"]
