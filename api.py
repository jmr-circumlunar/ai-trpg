"""FastAPI 入口。

    uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1
"""

from __future__ import annotations

from trpg.api import app

__all__ = ["app"]
