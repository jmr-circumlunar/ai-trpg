"""标准 OpenAI 兼容端点（/chat/completions）。

覆盖：DeepSeek / Qwen(DashScope 兼容模式) / Kimi / GLM / Ollama / vLLM。
"""

from __future__ import annotations

from typing import Any, Callable

from .base import ChatResult, post_with_retry


class OpenAICompatAdapter:
    kind = "openai"

    def __init__(
        self,
        base_url: str,
        endpoint: str = "/chat/completions",
        api_key: str = "",
        timeout: float = 90.0,
        on_retry: Callable | None = None,
        extra: dict | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.url = f"{self.base_url}{self.endpoint}"
        self.api_key = api_key
        self.timeout = timeout
        self._on_retry = on_retry
        self._json_mode: bool | None = None
        # 供应商特有参数（如 reasoning_effort），由 providers.yaml 配置
        self.extra = extra or {}

    # ── 可被子类覆盖的三个钩子 ───────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _build_payload(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        extra_override: dict | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._json_mode is not False:
            payload["response_format"] = {"type": "json_object"}
        merged = dict(self.extra)
        if extra_override:
            merged.update(extra_override)
        if merged:
            payload.update(merged)
        return payload

    def _check_business_error(self, data: dict) -> None:
        """子类可覆盖：有些供应商用外层字段表达业务错误。"""

    def _parse(self, data: dict, model: str) -> ChatResult:
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        content = message.get("content") or data.get("reply") or ""
        return ChatResult(
            content=content if isinstance(content, str) else str(content),
            finish_reason=(choices[0].get("finish_reason") if choices else None) or "stop",
            usage=data.get("usage") or {},
            model=model,
            raw=data,
        )

    # ── 主流程 ──────────────────────────────────────────────
    def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.9,
        max_tokens: int = 1200,
        extra_override: dict | None = None,
    ) -> ChatResult:
        payload = self._build_payload(
            messages, model, temperature, max_tokens, extra_override
        )

        try:
            data = post_with_retry(
                self.url, payload, self._headers(), self.timeout, on_retry=self._on_retry
            )
            if payload.get("response_format"):
                self._json_mode = True
        except Exception as exc:
            # 不支持 response_format（或路径不接受该字段）时自动降级，只试一次
            status = getattr(exc, "status", None)
            if payload.get("response_format") and status in (400, 404, 422):
                self._json_mode = False
                payload.pop("response_format", None)
                data = post_with_retry(
                    self.url, payload, self._headers(), self.timeout, on_retry=self._on_retry
                )
            else:
                raise

        self._check_business_error(data)
        result = self._parse(data, model)

        # 推理模型的 thinking 也吃 max_tokens：截断时放大预算重来一次
        if result.finish_reason == "length" and max_tokens < 4000:
            if self._on_retry:
                self._on_retry(None)
            return self.chat(
                messages, model, temperature, int(max_tokens * 1.6), extra_override
            )

        return result


def extract_json(text: str) -> dict:
    """从模型输出里取出 JSON 对象（兼容代码围栏与前后废话）。"""
    import json
    import re

    raw = (text or "").strip()
    block = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if block:
        raw = block.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError(f"响应中没有 JSON：{raw[:200]}")


__all__ = ["OpenAICompatAdapter", "extract_json", "Any"]
