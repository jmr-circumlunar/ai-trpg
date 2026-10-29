"""记忆与上下文参数（来自 config/memory.yaml）。

调参是调试的主要手段，所以全部外置：
改 YAML → 同 seed 跑两组 → 对比叙事与召回日志。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from ..config import config_path, load_yaml


@dataclass
class MemorySettings:
    # short_term
    recent_turns: int = 3
    summary_every: int = 5
    chapter_keep: int = 4
    # recall
    top_k: int = 5
    vector_top_k: int = 4
    important_limit: int = 3
    important_threshold: int = 7
    recent_limit: int = 5
    rrf_k: int = 60
    # 向量候选阈值：不设阈值时 top_k 恒满，噪声全被当成"回忆"
    vector_min_score: float = 0.0
    vector_rel_margin: float = 0.0
    # recall · 三因子打分（S5-2，缺省 rrf = 行为零变化）
    scoring_mode: str = "rrf"
    weight_relevance: float = 0.6
    weight_importance: float = 0.2
    weight_recency: float = 0.2
    half_life_turns: float = 20.0
    importance_scale: float = 10.0
    structured_relevance: float = 0.5
    # recall · 矛盾检测（S5-2，缺省关闭）
    conflict_enabled: int = 0
    conflict_min_diff_chars: int = 2
    conflict_min_shared_prefix: int = 2
    # consolidation · 反思层（S5-3，缺省关闭）
    consolidation_every_n: int = 0
    consolidation_threshold: int = 0
    consolidation_min_cluster: int = 2
    # lorebook · 词条触发（S5-3，无词条时零效果）
    lorebook_max_entries: int = 3
    lorebook_scan_depth: int = 2
    # agent
    max_steps: int = 5

    @classmethod
    def load(cls, root: Path | str, path: Path | None = None) -> "MemorySettings":
        root = Path(root)
        file = path or config_path(root, "memory")
        if not file.exists():
            return cls()

        raw = load_yaml(file)
        flat: dict[str, float] = {}
        for section in ("short_term", "recall", "agent", "consolidation", "lorebook"):
            for key, value in (raw.get(section) or {}).items():
                # 阈值类参数是小数；整型参数继续走 int，两者都要能读
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                flat[str(key)] = int(number) if number.is_integer() else number

        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in flat.items() if k in allowed}
        # 字符串参数（目前只有 scoring_mode）单独取：上面的数值通道会把它丢掉
        mode = (raw.get("recall") or {}).get("scoring_mode")
        if isinstance(mode, str) and mode in allowed:
            kwargs["scoring_mode"] = mode
        return cls(**kwargs)

    def describe(self) -> str:
        return (
            f"近{self.recent_turns}回合 + 每{self.summary_every}回合摘要×{self.chapter_keep}"
            f" | 召回 {self.top_k} 条(向量 {self.vector_top_k})"
            f" | ReAct ≤{self.max_steps} 步"
        )
