"""M04 配置：统一的 YAML 配置读取入口。

doc: 03-模块/M04-配置/00-模块总述.md
module: M04
layer: L0
"""

from .loader import CONFIG_DIR, config_path, load_yaml

__all__ = ["CONFIG_DIR", "config_path", "load_yaml"]
