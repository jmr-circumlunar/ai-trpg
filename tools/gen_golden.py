"""生成行为快照基线 `tests/golden/ironvale_turn3.json`。

**只允许在代码处于基线状态时运行**（即确认此刻 `ironvale` 行为就是「正确」的）。
之后任何改动都必须让 `pytest tests/test_behavior_snapshot.py` 继续通过；
行为确实需要变更时，同时更新 golden 并在决策日志登记理由。

用法：
    .\\.venv\\Scripts\\python.exe tools\\gen_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_behavior_snapshot import GOLDEN, build_snapshot  # noqa: E402


def main() -> int:
    rows = build_snapshot()
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入 {GOLDEN.relative_to(ROOT)}（{len(rows)} 回合）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
