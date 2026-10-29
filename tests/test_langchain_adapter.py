"""LangChain 传输层：与自研 adapter **同构**，可一键切换。

替换期最怕的不是「新链路不通」，而是「新链路悄悄改了语义」：
usage 键名变了、重试次数叠成 9 次、finish_reason 丢了——这些不会报错，
只会让 metrics 表和成本对账慢慢失真。所以这里的断言全部盯**语义等价**。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents.providers import KIND_MAP  # noqa: E402
from trpg.agents.providers.base import ChatResult  # noqa: E402
from trpg.agents.providers.langchain_provider import LangChainAdapter  # noqa: E402


def _dotenv(name: str, default: str = "") -> str:
    """取配置：环境变量优先，其次 `.env`（只读，不改系统环境）。"""
    if os.environ.get(name):
        return os.environ[name]
    path = ROOT / ".env"
    if not path.exists():
        return default
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


class _FakeMessage:
    content = "你好"
    response_metadata = {"finish_reason": "stop", "model_name": "MiniMax-M3.1-Flash"}
    usage_metadata = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


class _FakeChatOpenAI:
    """替身：记录构造参数与消息，返回固定响应。不打网络。"""

    last: dict = {}

    def __init__(self, **kwargs) -> None:
        type(self).last = {"kwargs": kwargs}

    def invoke(self, messages):
        type(self).last["messages"] = messages
        return _FakeMessage()


@pytest.fixture
def fake(monkeypatch):
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeChatOpenAI)
    _FakeChatOpenAI.last = {}
    return _FakeChatOpenAI


def test_kind_is_registered():
    """`config/providers.yaml` 里 `kind: langchain` 必须能解析到实现。"""
    assert KIND_MAP["langchain"] is LangChainAdapter


def test_chat_returns_chat_result(fake):
    adapter = LangChainAdapter(base_url="https://x/v1", api_key="k", timeout=30)

    result = adapter.chat(
        [{"role": "user", "content": "hi"}],
        model="m",
        temperature=0.5,
        max_tokens=100,
    )

    assert isinstance(result, ChatResult)
    assert result.content == "你好"
    assert result.model == "MiniMax-M3.1-Flash"
    assert result.finish_reason == "stop"


def test_usage_keys_match_legacy_adapter(fake):
    """usage 键必须与自研 adapter 一致，否则 metrics 会悄悄少一列。"""
    adapter = LangChainAdapter(base_url="", api_key="", timeout=30)

    result = adapter.chat([{"role": "user", "content": "hi"}], model="m")

    assert result.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


def test_no_implicit_retry(fake):
    """`max_retries=0`：上层已有重试，两层叠加会变 3×3 次、把超时放大到分钟级。"""
    adapter = LangChainAdapter(base_url="", api_key="", timeout=30)

    adapter.chat([{"role": "user", "content": "hi"}], model="m")

    assert _FakeChatOpenAI.last["kwargs"]["max_retries"] == 0


def test_base_url_and_key_are_forwarded(fake):
    adapter = LangChainAdapter(
        base_url="https://api.minimaxi.com/v1", api_key="secret", timeout=30
    )

    adapter.chat([{"role": "user", "content": "hi"}], model="m")

    kwargs = _FakeChatOpenAI.last["kwargs"]
    assert kwargs["base_url"] == "https://api.minimaxi.com/v1"
    assert kwargs["api_key"] == "secret"
    assert kwargs["model"] == "m"


def test_messages_are_passed_through(fake):
    adapter = LangChainAdapter(base_url="", api_key="", timeout=30)
    messages = [{"role": "system", "content": "你是 GM"}, {"role": "user", "content": "写场景"}]

    adapter.chat(messages, model="m")

    assert _FakeChatOpenAI.last["messages"] == messages


def test_latency_is_recorded(fake):
    adapter = LangChainAdapter(base_url="", api_key="", timeout=30)

    result = adapter.chat([{"role": "user", "content": "hi"}], model="m")

    assert result.latency_ms >= 0


def test_structured_uses_json_schema_by_default(monkeypatch):
    """默认走 `json_schema`：schema 直接约束模型，比在 prompt 里描述字段可靠。"""
    from pydantic import BaseModel

    class Vote(BaseModel):
        target: str

    seen: dict = {}

    class _FakeStructured:
        def invoke(self, messages):
            seen["messages"] = messages
            return Vote(target="chr_2")

    class _FakeLLM(_FakeChatOpenAI):
        def with_structured_output(self, schema, method="json_schema", **kwargs):
            seen["schema"] = schema
            seen["method"] = method
            return _FakeStructured()

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeLLM)
    adapter = LangChainAdapter(base_url="", api_key="", timeout=30)

    result, latency_ms = adapter.structured(
        Vote, [{"role": "user", "content": "投谁"}], model="m"
    )

    assert result.target == "chr_2"
    assert seen["method"] == "json_schema"
    assert seen["schema"] is Vote
    assert latency_ms >= 0


def test_structured_method_is_overridable(monkeypatch):
    """`json_schema` 不合用时能切 `json_mode`，不用改调用方。"""
    from pydantic import BaseModel

    class Vote(BaseModel):
        target: str

    seen: dict = {}

    class _FakeStructured:
        def invoke(self, messages):
            return Vote(target="chr_1")

    class _FakeLLM(_FakeChatOpenAI):
        def with_structured_output(self, schema, method="json_schema", **kwargs):
            seen["method"] = method
            return _FakeStructured()

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeLLM)
    adapter = LangChainAdapter(base_url="", api_key="", timeout=30)

    adapter.structured(
        Vote, [{"role": "user", "content": "投谁"}], model="m", method="json_mode"
    )

    assert seen["method"] == "json_mode"


@pytest.mark.skipif(
    not os.environ.get("TRPG_REAL_MODEL"), reason="真实模型冒烟不进 CI"
)
def test_real_minimax_smoke():
    """真实 MiniMax 冒烟：验证「adapter → ChatOpenAI → MiniMax」整条链路。"""
    from trpg.agents.providers import LangChainAdapter as Adapter

    adapter = Adapter(
        base_url=_dotenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
        api_key=_dotenv("MINIMAX_API_KEY"),
        timeout=60,
    )

    result = adapter.chat(
        [{"role": "user", "content": "只回答两个字：收到。"}],
        model=_dotenv("MINIMAX_MODEL", "MiniMax-M3.1-Flash"),
        temperature=0.1,
        max_tokens=50,
    )

    assert result.content.strip()
    assert result.usage["total_tokens"] > 0


@pytest.mark.skipif(
    not os.environ.get("TRPG_REAL_MODEL"), reason="真实模型冒烟不进 CI"
)
def test_real_minimax_structured():
    """真实 MiniMax 的结构化输出：`json_schema` 是否真的按 schema 出字段。

    这条是 M2 的前提——如果它不稳，`PlayerAction` 就不能交给 schema 约束，
    得继续用手写 `from_raw` + `extract_json`。
    """
    from pydantic import BaseModel, Field

    class Vote(BaseModel):
        """一次投票决定。"""

        target: str = Field(description="投给谁，角色 id")
        reason: str = Field(description="一句话理由")

    adapter = LangChainAdapter(
        base_url=_dotenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
        api_key=_dotenv("MINIMAX_API_KEY"),
        timeout=60,
    )

    result, latency_ms = adapter.structured(
        Vote,
        [
            {
                "role": "user",
                "content": "你在玩狼人杀，场上有 chr_1 / chr_2。"
                "你要投 chr_2，理由是「他昨晚太安静」。只输出 JSON。",
            }
        ],
        model=_dotenv("MINIMAX_MODEL", "MiniMax-M3.1-Flash"),
        temperature=0.1,
        max_tokens=200,
    )

    assert result.target == "chr_2"
    assert result.reason
    assert latency_ms > 0
