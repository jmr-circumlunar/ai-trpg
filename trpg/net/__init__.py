"""L0 公共设施：最小 HTTP JSON 客户端。

doc: 02-约束/01-分层与依赖.md
module: L0
layer: L0
"""

from .http import HttpError, post_json

__all__ = ["HttpError", "post_json"]
