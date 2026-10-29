"""M12 Prompt 组装（纯字符串，不读文件、不查世界）。

按 Agent 角色拆分：`blocks` 公共积木 / `gm` / `pc` / `npc` / `director`。
对外保持原 `trpg.agents.prompts.X` 用法：这里把全部函数汇总导出。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

from .blocks import *  # noqa: F401,F403
from .blocks import __all__ as _blocks_all
from .director import *  # noqa: F401,F403
from .gm import *  # noqa: F401,F403
from .npc import *  # noqa: F401,F403
from .pc import *  # noqa: F401,F403

__all__ = list(_blocks_all) + [
    name for name in dir() if not name.startswith("_") and name not in {"blocks_all"}
]
