"""LLM 客户端（OpenAI 兼容协议）。

S0 只定义接口，不接真实网络——`--mock` 路径足以验证编排。
S1 实现要点（按设计文档）：
  - 统一 base_url / api_key，覆盖 DeepSeek / Qwen / Kimi / GLM / Ollama
  - 结构化输出：JSON mode + pydantic 校验 + 失败重试 1 次
  - 每次调用写 agent_calls（tokens / latency / raw）
"""

from __future__ import annotations

from typing import Protocol

from ..models import CharacterCard, GameState, PlayerAction


class LLMClient(Protocol):
    model: str

    def describe_scene(self, state: GameState, turn: int) -> dict: ...

    def player_action(
        self, actor_id: str, card: CharacterCard, state: GameState, turn: int
    ) -> PlayerAction: ...

    def narrate(self, state: GameState, action: PlayerAction, result, turn: int) -> dict: ...

    def summarize(self, state: GameState, turn: int) -> dict: ...


class OpenAICompatibleClient:
    """S1 实现。S0 调用会显式失败，避免误以为已接通。"""

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0.9) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature

    def _not_implemented(self) -> None:
        raise NotImplementedError(
            "真实模型接入属于 S1；当前请使用 --mock"
        )

    def describe_scene(self, state: GameState, turn: int) -> dict:
        self._not_implemented()

    def player_action(
        self, actor_id: str, card: CharacterCard, state: GameState, turn: int
    ) -> PlayerAction:
        self._not_implemented()

    def narrate(self, state: GameState, action: PlayerAction, result, turn: int) -> dict:
        self._not_implemented()

    def summarize(self, state: GameState, turn: int) -> dict:
        self._not_implemented()
