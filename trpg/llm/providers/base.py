"""传输层抽象：ProviderAdapter。

业务层（AgentLLM）只依赖这个协议，不认识任何具体供应商。
新增一家供应商 = 实现一个 adapter + 在 config/providers.yaml 加 4 行。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..http import HttpError, post_json

# 可重试的网络/服务端错误（0 = 连接失败，529 = MiniMax 集群过载）
NET_RETRY_STATUS = {0, 429, 500, 502, 503, 504, 529}


@dataclass
class ChatResult:
    content: str
    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    kind: str

    def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.9,
        max_tokens: int = 1200,
    ) -> ChatResult: ...


def post_with_retry(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: float,
    attempts: int = 3,
    on_retry=None,
) -> dict:
    """指数退避：1s / 2s / 4s。on_retry 用于外部统计。"""
    delay = 1.0
    for attempt in range(attempts):
        try:
            return post_json(url, payload, headers, timeout=timeout)
        except HttpError as exc:
            retryable = exc.status in NET_RETRY_STATUS
            if retryable and attempt < attempts - 1:
                if on_retry:
                    on_retry(exc)
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")
