"""LangChain 传输层：用 `ChatOpenAI` 驱动任意 OpenAI 兼容端点（含 MiniMax）。

## 为什么它是一个 adapter 而不是一个新框架层

`ProviderAdapter` 只有两个成员（`kind` + `chat()`），`AgentLLM` 只依赖这个协议。
所以「换传输层」= 多实现一个 adapter，**上层 `_chat_json`、mock、prompt 全部不用动**，
`ironvale` 的 golden 也不会变（mock 不走这里）。

## 探活结论（2026-10-23，MiniMax-M3.1-Flash）

| 链路 | 结果 | 注意 |
|---|---|---|
| `chat` | ✅ 通 | `ChatOpenAI(base_url=...)` 直连 `https://api.minimaxi.com/v1` |
| `structured` | ✅ 通 | 三种模式矩阵见下；**prompt 里必须写明「只输出 JSON」** |
| `tools` | ✅ 通 | `bind_tools` + `tool_calls` 可用 |

`with_structured_output(method=...)` 实测矩阵：

| method | 结果 | 说明 |
|---|---|---|
| `json_schema`（LangChain 默认） | ✅ | 由 schema 约束字段名与类型，**首选** |
| `json_mode` | ✅ | 需要 prompt 自己描述字段结构 |
| `function_calling` | ❌ | 返回 `None`（MiniMax 不按这个模式发起调用） |

**踩过的坑**：第一轮探活用 `json_schema` 失败，我误判成「MiniMax 不支持，必须用 `json_mode`」。
真实原因是那次 prompt 里**没有要求「只输出 JSON」**——补上这句后 `json_schema` 立刻通过。
另需注意：`function_calling` 才是 LangChain 的旧默认，`json_schema` 是当前默认值，别搞反。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import time
from typing import Any

from .base import ChatResult


class LangChainAdapter:
    """用 LangChain 的 `ChatOpenAI` 发请求，产出与自研 adapter 同构的 `ChatResult`。

    重试交给调用方语义（与 `post_with_retry` 一致的 3 次退避由 LangChain 的
    `max_retries` 承担），这里 `max_retries=0` 保持「不隐式重试」，
    避免与上层 `_chat_json` 的重试叠加成 9 次。
    """

    kind = "langchain"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        timeout: float = 90.0,
        on_retry=None,
        extra: dict | None = None,
        endpoint: str = "",  # 兼容自研 adapter 的签名；LangChain 自己拼路径，这里忽略
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.on_retry = on_retry
        self.extra = dict(extra or {})

    def _build(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        extra_override: dict | None = None,
    ):
        """懒建客户端：模型名是 `chat()` 的入参（角色卡可以各自指定模型）。"""
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "timeout": self.timeout,
            # 不隐式重试：上层已有自己的重试语义，叠加会变成 3×3 次
            "max_retries": 0,
        }
        if self.base_url:
            options["base_url"] = self.base_url
        if self.api_key:
            options["api_key"] = self.api_key
        if max_tokens:
            options["max_tokens"] = max_tokens
        # 与自研 adapter 同序：先本 provider 的 extra，再按次覆盖（分档 / reasoning_effort）
        merged = dict(self.extra)
        if extra_override:
            merged.update(extra_override)
        options.update(merged)
        return ChatOpenAI(**options)

    def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.9,
        max_tokens: int = 1200,
        extra_override: dict | None = None,
    ) -> ChatResult:
        started = time.perf_counter()
        llm = self._build(model, temperature, max_tokens, extra_override)
        message = llm.invoke(messages)
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = message.content
        if not isinstance(text, str):
            # 多段内容（文本 + 图片）只取文本部分
            text = "".join(
                str(part.get("text", ""))
                for part in (text or [])
                if isinstance(part, dict) and part.get("type") == "text"
            )

        metadata = dict(getattr(message, "response_metadata", {}) or {})
        usage = dict(getattr(message, "usage_metadata", None) or {})
        # 与自研 adapter 的 usage 键保持一致，下游 metrics 不用改
        normalised_usage = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

        return ChatResult(
            content=text or "",
            finish_reason=str(metadata.get("finish_reason") or "stop"),
            usage=normalised_usage,
            model=str(metadata.get("model_name") or model),
            raw=metadata,
            latency_ms=latency_ms,
        )


    def structured(
        self,
        schema,
        messages: list[dict],
        model: str,
        temperature: float = 0.9,
        max_tokens: int = 1200,
        method: str = "json_schema",
        extra_override: dict | None = None,
    ):
        """结构化输出：让模型按 `schema` 产出对象，而不是自由文本 + 手写解析。

        `method` 的实测结论见模块文档：

        - `json_schema`（默认）：由 schema 约束字段名与类型，**首选**
        - `json_mode`：只保证是合法 JSON，字段结构得靠 prompt 说
        - `function_calling`：MiniMax 返回 `None`，**不要用**

        注意：**prompt 里仍要写「只输出 JSON」**。MiniMax 对 `response_format` 的
        遵守是弱约束，不配合提示词时会直接返回自然语言（我在探活时踩过这个坑）。
        """
        started = time.perf_counter()
        llm = self._build(model, temperature, max_tokens, extra_override)
        runner = llm.with_structured_output(schema, method=method)
        result = runner.invoke(messages)
        return result, int((time.perf_counter() - started) * 1000)


__all__ = ["LangChainAdapter"]
