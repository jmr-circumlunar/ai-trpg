"""探测 MiniMax 响应结构（排查空内容 / 推理字段 / finish_reason）。

用法：python tools/probe_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trpg.llm.env import llm_settings  # noqa: E402
from trpg.llm.http import post_json  # noqa: E402


def main() -> int:
    s = llm_settings(ROOT)
    url = s["base_url"].rstrip("/") + "/text/chatcompletion_v2"
    print(f"base_url={s['base_url']} model={s['model']}")

    payload = {
        "model": s["model"],
        "messages": [{"role": "user", "content": '只输出 JSON：{"ok": true}'}],
        "max_tokens": 200,
    }
    resp = post_json(url, payload, {"Authorization": f"Bearer {s['api_key']}"}, timeout=60)

    print("top-level keys :", list(resp.keys()))
    choice = (resp.get("choices") or [{}])[0]
    print("choice keys    :", list(choice.keys()))
    print("finish_reason  :", choice.get("finish_reason"))
    message = choice.get("message") or {}
    print("message keys   :", list(message.keys()))
    print("content        :", repr((message.get("content") or "")[:200]))
    if "reasoning_content" in message:
        print("reasoning[:200]:", repr((message.get("reasoning_content") or "")[:200]))
    print("usage          :", resp.get("usage"))
    print("base_resp      :", resp.get("base_resp"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
