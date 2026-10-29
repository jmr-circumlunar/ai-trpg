"""最小 HTTP JSON 客户端（urllib，不引第三方依赖）。

doc: 02-约束/01-分层与依赖.md
module: L0
layer: L0
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        self.status = status
        self.body = body[:500]
        self.url = url
        super().__init__(f"HTTP {status} @ {url}: {self.body}")


def post_json(
    url: str,
    payload: dict,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise HttpError(exc.code, detail, url) from exc
    except urllib.error.URLError as exc:
        raise HttpError(0, str(exc.reason), url) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HttpError(200, f"响应不是合法 JSON：{raw[:300]}", url) from exc
