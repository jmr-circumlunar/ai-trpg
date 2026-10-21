from .base import ChatResult, ProviderAdapter, post_with_retry
from .minimax import MiniMaxAdapter
from .openai_compat import OpenAICompatAdapter, extract_json

KIND_MAP = {
    "openai": OpenAICompatAdapter,
    "minimax": MiniMaxAdapter,
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
