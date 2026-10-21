"""从已有的 .env 导入 MiniMax 配置（不打印密钥）。

用法：
    python tools/import_env.py <source.env>
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET_KEYS = {
    "LLM_API_KEY": "MINIMAX_API_KEY",
    "MINIMAX_API_KEY": "MINIMAX_API_KEY",
    "LLM_BASE_URL": "MINIMAX_BASE_URL",
    "MINIMAX_BASE_URL": "MINIMAX_BASE_URL",
    "LLM_MODEL": "MINIMAX_MODEL",
    "MINIMAX_MODEL": "MINIMAX_MODEL",
}


def parse(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python tools/import_env.py <source.env>")
        return 1

    source = Path(argv[1])
    if not source.exists():
        print(f"找不到源文件：{source}")
        return 1

    src = parse(source.read_text(encoding="utf-8", errors="ignore"))
    out: dict[str, str] = {}
    for src_key, dst_key in TARGET_KEYS.items():
        if src_key in src and dst_key not in out:
            out[dst_key] = src[src_key]

    if not out.get("MINIMAX_API_KEY"):
        print("源文件里没有找到可用的 API Key")
        return 1

    target = Path(__file__).resolve().parents[1] / ".env"
    lines = [f"{k}={v}" for k, v in out.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"已写入 {target}")
    print(f"  key:   {'*' * 8}{out['MINIMAX_API_KEY'][-4:]}（长度 {len(out['MINIMAX_API_KEY'])}）")
    print(f"  base:  {out.get('MINIMAX_BASE_URL', '(默认)')}")
    print(f"  model: {out.get('MINIMAX_MODEL', '(默认)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
