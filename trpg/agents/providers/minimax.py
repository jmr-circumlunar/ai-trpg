"""MiniMax adapter：与 OpenAI 兼容，但有 3 处差异。

1. 路径 `/text/chatcompletion_v2`
2. 外层 `base_resp.status_code != 0` 表示业务错误
3. 推理模型：`message.reasoning_content` 与 `finish_reason=length`（已在基类处理）

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

from .openai_compat import OpenAICompatAdapter


class MiniMaxAdapter(OpenAICompatAdapter):
    kind = "minimax"

    def __init__(
        self,
        base_url: str = "https://api.minimaxi.com/v1",
        endpoint: str = "/text/chatcompletion_v2",
        api_key: str = "",
        timeout: float = 90.0,
        on_retry=None,
        extra: dict | None = None,
    ) -> None:
        super().__init__(base_url, endpoint, api_key, timeout, on_retry, extra)

    def _check_business_error(self, data: dict) -> None:
        base_resp = data.get("base_resp") or {}
        code = base_resp.get("status_code", 0)
        if code not in (0, None):
            raise RuntimeError(
                f"MiniMax 业务错误 {code}: {base_resp.get('status_msg')}"
            )

        # 推理模型可能只回了 reasoning，content 为空
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            if not (message.get("content") or "").strip() and message.get("reasoning_content"):
                raise RuntimeError("MiniMax 只返回了 reasoning_content，content 为空")


__all__ = ["MiniMaxAdapter"]
