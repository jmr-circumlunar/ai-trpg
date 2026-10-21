"""极简 .env 读取：不引入 python-dotenv。

优先级：真实环境变量 > .env 文件 > 默认值。
"""

from __future__ import annotations

import os
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def llm_settings(root: Path) -> dict[str, str]:
    file_env = read_env_file(root / ".env")

    def pick(name: str, default: str = "") -> str:
        return os.environ.get(name) or file_env.get(name) or default

    return {
        "api_key": pick("MINIMAX_API_KEY") or pick("LLM_API_KEY"),
        "base_url": pick("MINIMAX_BASE_URL") or pick("LLM_BASE_URL", "https://api.minimaxi.com/v1"),
        "model": pick("MINIMAX_MODEL") or pick("LLM_MODEL", "MiniMax-M3"),
        "model_player": pick("MINIMAX_MODEL_PLAYER") or pick("MINIMAX_MODEL") or pick("LLM_MODEL", "MiniMax-M3"),
    }
