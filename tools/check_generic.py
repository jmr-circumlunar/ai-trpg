"""通用性护栏（C60）：`trpg/` 里不得出现任何世界专属词汇。

框架只提供通用能力。一旦框架代码里出现某个世界的词（狼人、预言家、放逐……），
说明那个能力已经被某类游戏绑架：正确做法是把它变成参数或下沉到 `worlds/`。

唯一豁免：注释里显式写 `# world-example:` 的行（用于说明「某世界会把它命名为 X」）。

用法：
    .\\.venv\\Scripts\\python.exe tools\\check_generic.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 世界专属词汇。通用概念（如「夜晚」作为时段）不在此列——时段由 rules/clock.yaml 定义。
BLACKLIST: tuple[str, ...] = (
    "night",
    "dawn",
    "werewolf",
    "seer",
    "witch",
    "hunter",
    "villager",
    "exile",
    "cupid",
)

EXEMPT_MARK = "# world-example:"
# 不用 \b：`night_action` 里 night 与下划线之间没有词边界，会漏检。
# 这里要求「前面不是字母数字、后面不是字母」，既能抓到 night_action，也不会误伤 enlighten。
_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(BLACKLIST) + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def find_violations(root: Path | str) -> list[tuple[str, int, str]]:
    """返回 [(文件路径, 行号, 命中词)]；空列表 = 通过。"""
    base = Path(root)
    if not base.exists():
        return []

    violations: list[tuple[str, int, str]] = []
    for path in sorted(base.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if EXEMPT_MARK in line:
                continue
            for match in _PATTERN.finditer(line):
                violations.append((str(path), lineno, match.group(1).lower()))
    return violations


def main() -> int:
    target = ROOT / "trpg"
    violations = find_violations(target)
    if not violations:
        print("通用性校验通过：trpg/ 内无世界专属词汇")
        return 0

    for path, lineno, word in violations:
        shown = Path(path).relative_to(ROOT) if Path(path).is_relative_to(ROOT) else path
        print(f"{shown}:{lineno} 出现世界专属词汇「{word}」")
    print(
        f"\n共 {len(violations)} 处。框架只应有通用能力，"
        "世界词汇请下沉到 worlds/<id>/（C60）。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
