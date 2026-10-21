"""记忆与上下文参数（来自 config/memory.yaml）。

调参是调试的主要手段，所以全部外置：
改 YAML → 同 seed 跑两组 → 对比叙事与召回日志。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import yaml


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
    # agent
    max_steps: int = 3

    @classmethod
    def load(cls, root: Path | str, path: Path | None = None) -> "MemorySettings":
        root = Path(root)
        file = path or (root / "config" / "memory.yaml")
        if not file.exists():
            return cls()

        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        flat: dict[str, int] = {}
        for section in ("short_term", "recall", "agent"):
            for key, value in (raw.get(section) or {}).items():
                try:
                    flat[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue

        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in flat.items() if k in allowed}
        return cls(**kwargs)

    def describe(self) -> str:
        return (
            f"近{self.recent_turns}回合 + 每{self.summary_every}回合摘要×{self.chapter_keep}"
            f" | 召回 {self.top_k} 条(向量 {self.vector_top_k})"
            f" | ReAct ≤{self.max_steps} 步"
        )
