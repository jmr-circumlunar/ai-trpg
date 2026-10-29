"""MiniMax × LangChain 兼容性探活（P0 闸门）。

**为什么先探活**：整套替换方案押在「MiniMax 能被 LangChain 直接驱动」这个假设上。
三条链路里任何一条不通，实现路线就要改（换 provider 封装 / 保留自研 adapter 做 BaseChatModel 包装）。
先花 30 秒探活，比写完 8 个文件再发现走不通便宜得多。

验三件事：

| # | 验什么 | 不通时的后果 |
|---|---|---|
| 1 | `chat`：能否用 ChatOpenAI 指向 MiniMax 端点 | 整个模型层替换不成立 |
| 2 | `structured`：`with_structured_output` 能否返回合法结构 | `PlayerAction.from_raw` 手写解析不能删 |
| 3 | `tools`：`bind_tools` + `tool_calls` 是否工作 | 工具调用只能继续用文本协议 |

用法：

    .\\.venv\\Scripts\\python.exe tools\\probe_minimax.py

退出码 0 = 三项全通；1 = 有不通项（输出会指明是哪一项、以及下一步该试什么）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    """读 `.env`（不覆盖已存在的环境变量）。只读，不改系统环境。"""
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_MODEL"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _require(env: dict[str, str]) -> tuple[str, str, str]:
    key = env.get("MINIMAX_API_KEY", "")
    base = env.get("MINIMAX_BASE_URL", "") or "https://api.minimaxi.com/v1"
    model = env.get("MINIMAX_MODEL", "")
    if not key:
        print("[FAIL] MINIMAX_API_KEY 为空：先把 key 填进 .env")
        sys.exit(1)
    if not model:
        print("[FAIL] MINIMAX_MODEL 为空：`.env.example` 里有参考值")
        sys.exit(1)
    return key, base, model


def _build(env: dict[str, str]):
    """建一个 ChatOpenAI 客户端指向 MiniMax。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("[FAIL] 缺 langchain-openai：pip install langchain-openai")
        sys.exit(1)

    key, base, model = _require(env)
    return ChatOpenAI(
        model=model,
        base_url=base,
        api_key=key,
        timeout=45,
        max_retries=0,
        temperature=0.3,
    )


def probe_chat(llm) -> bool:
    """链路 1：普通对话。"""
    try:
        message = llm.invoke("用一句话回答：1+1 等于几？")
        text = str(getattr(message, "content", "") or "").strip()
        if not text:
            print("[FAIL] chat：返回内容为空")
            return False
        print(f"[ OK ] chat：{text[:60]}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] chat：{type(exc).__name__}: {str(exc)[:200]}")
        print("       下一步：多半是端点路径不对（MiniMax 是 /text/chatcompletion_v2），")
        print("       先确认 base_url 下是否存在 OpenAI 兼容的 /chat/completions。")
        return False


def probe_structured(llm) -> bool:
    """链路 2：结构化输出（中文 JSON）。

    两种模式都试：`function_calling`（默认，靠 tool schema 约束）与
    `json_mode`（靠 `response_format={"type":"json_object"}` + prompt 引导）。
    第一轮实测 MiniMax 在默认模式下**返回自然语言**，所以 json_mode 才是主路径。
    """
    from pydantic import BaseModel, Field

    class Vote(BaseModel):
        """一次投票决定。"""

        target: str = Field(description="投给谁，填角色 id，例如 chr_2")
        reason: str = Field(description="一句话理由，不超过 20 字")

    prompt = (
        "你在玩狼人杀，场上有 chr_1 / chr_2 / chr_3。"
        "你要投 chr_2，理由是「他昨晚太安静」。\n"
        '只输出 JSON，形如 {"target": "chr_2", "reason": "……"}，不要任何解释。'
    )

    attempts: list[str] = []
    # 三种模式**全部**跑一遍（不能遇到能用的就 return）：
    # 要的是完整矩阵，才能判断该用哪种、以及默认模式为什么不行。
    # `json_schema` 是 LangChain 的默认值，也是 OpenAI 的原生结构化输出。
    for method in ("json_schema", "json_mode", "function_calling"):
        try:
            structured = llm.with_structured_output(Vote, method=method)
            result = structured.invoke(prompt)
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{method:<16} ❌ {type(exc).__name__}: {str(exc)[:100]}")
            continue
        if isinstance(result, Vote) and result.target:
            attempts.append(
                f"{method:<16} ✅ target={result.target} reason={result.reason[:16]}"
            )
        else:
            attempts.append(f"{method:<16} ❌ 结构不对：{result!r}"[:160])

    for item in attempts:
        print(f"       {item}")

    ok = any("✅" in item for item in attempts)
    if ok:
        print("[ OK ] structured：至少一种模式可用（见上表）")
    else:
        print("[FAIL] structured：三种模式全不通，只能保留手写解析")
    return ok


def probe_tools(llm) -> bool:
    """链路 3：工具调用。"""
    from langchain_core.tools import tool

    @tool
    def say(speech: str) -> str:
        """对在场的人说一句话。"""
        return f"你说了：{speech}"

    try:
        bound = llm.bind_tools([say])
        message = bound.invoke(
            "你必须发言。用 say 工具说这句话：我昨晚一直在屋里。"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] tools：{type(exc).__name__}: {str(exc)[:200]}")
        return False

    calls = list(getattr(message, "tool_calls", []) or [])
    if not calls:
        print("[FAIL] tools：模型没有发起工具调用（可能只回了文本）")
        print(f"       实际内容：{str(getattr(message, 'content', ''))[:120]}")
        return False
    print(f"[ OK ] tools：{calls[0].get('name')}({calls[0].get('args')})")
    return True


def main() -> int:
    env = load_env()
    key, base, model = _require(env)
    print(f"model={model} base={base} key_len={len(key)}")
    print("-" * 60)

    llm = _build(env)
    results = {
        "chat": probe_chat(llm),
        "structured": probe_structured(llm),
        "tools": probe_tools(llm),
    }

    print("-" * 60)
    passed = sum(1 for ok in results.values() if ok)
    print(f"通过 {passed}/{len(results)}：{results}")
    if passed == len(results):
        print("结论：MiniMax 可被 LangChain 直接驱动，模型层替换方案成立。")
        return 0
    print("结论：有链路不通。按上面提示选择替代方案（见方案 30 风险表 R1）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
