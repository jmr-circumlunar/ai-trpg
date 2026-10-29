"""`python -m trpg.eval <命令>` 的入口（arena / leaderboard / calibrate）。

doc: 03-模块/M16-可观测与评测/00-模块总述.md
module: M16
layer: L7
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
