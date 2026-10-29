"""M04 配置：统一的 YAML 配置读取入口。

各模块不再各自 open + yaml.safe_load，一律走这里：
- 路径规则只有一个（root/config/<name>.yaml）
- 文件缺失返回 default，不抛

doc: 03-模块/M04-配置/00-模块总述.md
module: M04
layer: L0
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = "config"


def config_path(root: Path | str, name: str) -> Path:
    """root/config/<name>.yaml；name 允许带 .yaml 后缀。"""
    filename = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
    return Path(root) / CONFIG_DIR / filename


def load_yaml(path: Path | str, default: Any = None) -> Any:
    """读 YAML；文件不存在或为空时返回 default。"""
    file = Path(path)
    if not file.exists():
        return {} if default is None else default
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if raw is None:
        return {} if default is None else default
    return raw
