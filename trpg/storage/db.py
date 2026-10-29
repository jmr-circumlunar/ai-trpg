"""M03 存储 · SQLite 落库门面。

实现按表族拆到同目录模块，外部一律 `from . import db` 使用本门面：
- `schema.py`  建表 DDL
- `conn.py`    连接 / 建表 / 迁移 / 提交
- `session.py` 会话、世界状态快照、会话地图、角色卡快照
- `turn.py`    回合、骰子、模型调用、结局
- `items.py`   物品使用计数与晋升候选
- `events.py`  角色结构化事件
- `agents.py`  席位 Agent 配置
- `accounts.py` 账号与登录会话

保持门面而不让调用方散着 import 子模块：换后端时只有这一层变。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

from .accounts import *
from .agents import *
from .beliefs import *  # noqa: F401,F403
from .conn import *  # noqa: F401,F403
from .events import *
from .items import *
from .schema import SCHEMA
from .session import *
from .trace import *  # noqa: F401,F403
from .relations import *  # noqa: F401,F403
from .event_log import *  # noqa: F401,F403
from .turn import *

__all__ = [name for name in dir() if not name.startswith("__")]
