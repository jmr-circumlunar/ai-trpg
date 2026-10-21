"""探测 MiniMax 是否支持关闭/降低 thinking（影响单次调用耗时）。

用法：python tools/probe_thinking.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trpg.llm.env import llm_settings  # noqa: E402
from trpg.llm.http import post_json  # noqa: E402

MESSAGES = [
    {"role": "system", "content": "你是跑团主持人。只输出 JSON。"},
    {"role": "user", "content": '用一句话描述雨夜的码头。输出 {"narration": "..."}'},
]

VARIANTS = {
    "default": {},
    "thinking_disabled": {"thinking": {"type": "disabled"}},
    "reasoning_effort_low": {"reasoning_effort": "low"},
}


def main() -> int:
    s = llm_settings(ROOT)
    url = s["base_url"].rstrip("/") + "/text/chatcompletion_v2"
    print(f"model={s['model']}")

    for label, extra in VARIANTS.items():
        payload = {"model": s["model"], "messages": MESSAGES, "max_tokens": 400, "temperature": 0.9}
        payload.update(extra)
        started = time.perf_counter()
        try:
            data = post_json(url, payload, {"Authorization": f"Bearer {s['api_key']}"}, timeout=120)
            elapsed = time.perf_counter() - started
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            content = (message.get("content") or "")[:60]
            print(
                f"{label:22s} {elapsed:6.1f}s  finish={choice.get('finish_reason')}"
                f"  reasoning_tokens={reasoning}  out={usage.get('completion_tokens')}"
                f"  content={content!r}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{label:22s} FAILED after {time.perf_counter() - started:.1f}s: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
