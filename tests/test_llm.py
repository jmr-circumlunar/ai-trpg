import json
from pathlib import Path

import pytest

from trpg.llm import AgentLLM, LLMConfig, extract_json
from trpg.llm.providers import MiniMaxAdapter
from trpg.llm.providers.base import ChatResult
from trpg.models import ModelConfig
from trpg.testing import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    kind = "fake"

    def __init__(self, payload: dict | None = None, exc: Exception | None = None) -> None:
        self.payload = payload or {}
        self.exc = exc
        self.models: list[str] = []

    def chat(
        self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None
    ) -> ChatResult:
        self.models.append(model)
        if self.exc:
            raise self.exc
        return ChatResult(
            content=json.dumps(self.payload, ensure_ascii=False),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


# ── JSON 提取 ──────────────────────────────────────────────
def test_extract_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_prose():
    assert extract_json('好的，结果：{"a": 1} 完毕') == {"a": 1}


def test_extract_invalid():
    with pytest.raises(Exception):
        extract_json("完全没有 json")


# ── 配置解析 ───────────────────────────────────────────────
def test_defaults_resolve():
    spec = LLMConfig(ROOT).resolve()
    assert spec.provider == "minimax"
    assert spec.model == "MiniMax-M3.1-Flash"


def test_card_model_wins():
    cfg = LLMConfig(ROOT)
    spec = cfg.resolve(ModelConfig(provider="ollama", model="qwen2.5:7b", temperature=0.6))
    assert spec.provider == "ollama"
    assert spec.model == "qwen2.5:7b"
    assert spec.temperature == 0.6


def test_unknown_provider_raises():
    with pytest.raises(KeyError):
        LLMConfig(ROOT).adapter("not-configured")


def test_minimax_business_error():
    adapter = MiniMaxAdapter()
    with pytest.raises(RuntimeError):
        adapter._check_business_error(
            {"base_resp": {"status_code": 1002, "status_msg": "bad"}}
        )


def test_minimax_reasoning_only_raises():
    adapter = MiniMaxAdapter()
    with pytest.raises(RuntimeError):
        adapter._check_business_error(
            {"choices": [{"message": {"content": "", "reasoning_content": "思考中"}}]}
        )


# ── 角色级模型生效 ─────────────────────────────────────────
def test_player_action_uses_card_provider():
    cfg = LLMConfig(ROOT)
    cards = {"chr_p1": make_card(), "chr_p2": make_card(profession="战士")}
    cards["chr_p1"].ext.model = ModelConfig(provider="minimax", model="MiniMax-M3.1-Flash")
    cards["chr_p2"].ext.model = ModelConfig(provider="ollama", model="qwen2.5:7b")

    fake_minimax = FakeAdapter(
        {"thought": "t1", "action": "a1", "skill": "观察", "intent": "normal"}
    )
    fake_ollama = FakeAdapter(
        {"thought": "t2", "action": "a2", "skill": "潜行", "intent": "careful"}
    )
    cfg._adapters = {"minimax": fake_minimax, "ollama": fake_ollama}

    agent = AgentLLM(cfg, cards)
    state = make_state()

    a1 = agent.player_action("chr_p1", cards["chr_p1"], state, 1)
    a2 = agent.player_action("chr_p2", cards["chr_p2"], state, 1)

    assert fake_minimax.models == ["MiniMax-M3.1-Flash"]
    assert fake_ollama.models == ["qwen2.5:7b"]
    assert (a1.action, a2.action) == ("a1", "a2")


def test_fallback_on_error():
    cfg = LLMConfig(ROOT)
    cards = {"chr_p1": make_card()}
    cfg._adapters = {"minimax": FakeAdapter(exc=RuntimeError("boom"))}

    agent = AgentLLM(cfg, cards)
    action = agent.player_action("chr_p1", cards["chr_p1"], make_state(), 1)

    assert action.action
    assert agent.usage["fail"] >= 1
