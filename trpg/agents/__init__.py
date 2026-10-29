"""doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""
from .agent import TASK_TOKENS, AgentLLM
from .agent_director import Director, LLMDirector
from .spec import LLMConfig, ModelSpec
from .mock import MockLLM
from .providers import MiniMaxAdapter, OpenAICompatAdapter, ProviderAdapter, extract_json

__all__ = [
    "AgentLLM",
    "Director",
    "LLMConfig",
    "LLMDirector",
    "MiniMaxAdapter",
    "MockLLM",
    "ModelSpec",
    "OpenAICompatAdapter",
    "ProviderAdapter",
    "TASK_TOKENS",
    "extract_json",
]
