"""探测 MiniMax 是否提供 embedding 接口（向量库的前置依赖）。

用法：python tools/probe_embedding.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trpg.agents.env import llm_settings  # noqa: E402
from trpg.net.http import post_json  # noqa: E402

CANDIDATES = [
    ("/embeddings", "embo-01"),
    ("/embeddings", "MiniMax-Embedding"),
    ("/text/embeddings", "embo-01"),
]


def main() -> int:
    s = llm_settings(ROOT)
    base = s["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {s['api_key']}"}

    for path, model in CANDIDATES:
        url = f"{base}{path}"
        payload = {
            "model": model,
            "texts": ["雨夜的码头", "钟楼的铁栅"],
            "type": "db",
        }
        started = time.perf_counter()
        try:
            data = post_json(url, payload, headers, timeout=45)
            elapsed = time.perf_counter() - started
            print(f"OK   {url} model={model}  {elapsed:.1f}s")
            print("  top keys:", list(data.keys()))
            for key, value in data.items():
                if isinstance(value, list):
                    print(f"  {key}: list[{len(value)}] first={str(value[0])[:140] if value else ''}")
                else:
                    print(f"  {key}: {str(value)[:140]}")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {url} model={model}  {type(exc).__name__}: {str(exc)[:160]}")

    print("\n结论：MiniMax 当前不可用于 embedding，需要换方案")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
