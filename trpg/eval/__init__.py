"""M16 可观测与评测：纯内存批量 arena + 校准指标（S2-5）。

三条命令（`python -m trpg.eval <命令>`）：
- `arena`：跑一批对局，出阵营胜率 / 存活率 / 每 agent 胜率 + 随机基线对照；
- `leaderboard`：每 agent 胜率排行；
- `calibrate`：怀疑度校准（Brier / skill score / Murphy 分解 / 可靠性表）。

**纯内存**：不落库、不走网络；同 seed 同结果。

doc: 03-模块/M16-可观测与评测/00-模块总述.md
module: M16
layer: L7
"""

from .arena import ArenaResult, GameResult, run_arena
from .calibration import (
    ExitProxies,
    Pair,
    base_rate,
    brier_score,
    brier_skill_score,
    murphy_decomposition,
    reliability_table,
    vote_distribution_entropy,
)

__all__ = [
    "ArenaResult",
    "ExitProxies",
    "GameResult",
    "Pair",
    "base_rate",
    "brier_score",
    "brier_skill_score",
    "murphy_decomposition",
    "reliability_table",
    "run_arena",
    "vote_distribution_entropy",
]
