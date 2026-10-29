"""doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""
from .base import ChatResult, ProviderAdapter, post_with_retry
from .langchain_provider import LangChainAdapter
from .minimax import MiniMaxAdapter
from .openai_compat import OpenAICompatAdapter, extract_json

# `kind` 决定用哪个传输层实现。同一个供应商可以有两种实现：
# `minimax`（自研 HTTP，含 json_mode 降级/放大 max_tokens）与
# `langchain`（ChatOpenAI，官方重试与结构化输出）。
KIND_MAP = {
    "openai": OpenAICompatAdapter,
    "minimax": MiniMaxAdapter,
    "langchain": LangChainAdapter,
}

__all__ = [
    "ChatResult",
    "KIND_MAP",
    "MiniMaxAdapter",
    "OpenAICompatAdapter",
    "ProviderAdapter",
    "extract_json",
    "post_with_retry",
]
