"""M14 装配层：只做 include_router 与兼容导出。

- 共享上下文：`deps.py`
- 请求体模型：`schemas.py`
- 端点分组：`routes/*.py`

本文件保持薄：不内联任何玩法逻辑，也不再新增端点。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from .deps import *  # noqa: F401,F403
from .deps import app
from .routes import auth, characters, event_texts, items, meta, pages, play, sessions, trace, worlds
from .routes.play import resume_session  # 兼容历史导入
from .routes.sessions import reset_session
from .schemas import (  # noqa: F401
    AccountReq,
    AgentReq,
    CharacterForm,
    CreateSessionReq,
    HumanActionReq,
    LoginReq,
)

for _module in (auth, pages, worlds, characters, items, meta, sessions, play, trace, event_texts):
    app.include_router(_module.router)

__all__ = ["app"]
