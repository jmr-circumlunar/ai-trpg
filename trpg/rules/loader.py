"""规则表加载：YAML 是数值的唯一真相，代码只读不算。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..models.enums import Intent
from . import clock as clock_mod

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"缺少规则文件：{path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Rules:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self.ruleset = _load(self.root / "rules" / "ruleset.yaml")
        self.equipment = _load(self.root / "rules" / "equipment.yaml")
        self.professions = _load(self.root / "rules" / "professions.yaml")["professions"]
        self.seeds = _load(self.root / "rules" / "event_seeds.yaml")["seeds"]
        self.clock = _load(self.root / "rules" / "clock.yaml")

    # ── 技能 ────────────────────────────────────────────────
    @property
    def skill_attr(self) -> dict[str, str]:
        return self.ruleset["skill_attr"]

    @property
    def skill_action_type(self) -> dict[str, str]:
        return self.ruleset["skill_action_type"]

    def valid_skill(self, skill: str | None) -> bool:
        return bool(skill) and skill in self.skill_attr

    def attr_for_skill(self, skill: str | None) -> str | None:
        if not skill:
            return None
        return self.skill_attr.get(skill)

    def action_type_for_skill(self, skill: str | None) -> str:
        """未知技能降级为 contest（通用检定），不报错。"""
        if not skill:
            return "contest"
        return self.skill_action_type.get(skill, "contest")

    # ── 难度 ────────────────────────────────────────────────
    @property
    def situation_delta_cap(self) -> int:
        return int(self.ruleset.get("situation_delta_cap", 2))

    def dc(
        self,
        action_type: str,
        stage: int,
        intent: Intent | str = Intent.normal,
        situation_delta: int = 0,
        skill: str | None = None,
        phase: str | None = None,
    ) -> int:
        base = self.ruleset["dc_base"].get(action_type, self.ruleset["dc_base"]["contest"])
        stage_add = self.ruleset["stage_mod"].get(stage, 0)
        intent_key = intent.value if isinstance(intent, Intent) else str(intent)
        intent_add = self.ruleset["intent_mod"].get(intent_key, 0)

        cap = self.situation_delta_cap
        situation = max(-cap, min(cap, int(situation_delta)))

        # 时段修正（按技能），例如入夜潜行更容易、交涉更难
        time_add = clock_mod.skill_mod(phase, skill, self.phases) if phase else 0

        return max(5, int(base) + int(stage_add) + int(intent_add) + situation + time_add)

    # ── 时间 ────────────────────────────────────────────────
    @property
    def phases(self) -> dict[str, Any]:
        return self.clock.get("phases") or {}

    @property
    def default_step_minutes(self) -> int:
        return int(self.clock.get("default_step_minutes", 10))

    @property
    def max_step_minutes(self) -> int:
        return int(self.clock.get("max_step_minutes", 480))

    @property
    def min_step_minutes(self) -> int:
        return int(self.clock.get("min_step_minutes", 5))

    @property
    def allowed_steps(self) -> list[dict]:
        return list(self.clock.get("allowed_steps") or [])

    def fallback_minutes(self, action_types: list[str]) -> int:
        """GM 没申报时间时的兜底：取本回合最"重"的行动类型。"""
        table = self.clock.get("fallback_by_action_type") or {}
        values = [int(table.get(t, 0) or 0) for t in action_types]
        values = [v for v in values if v > 0]
        return max(values) if values else self.default_step_minutes

    def phase_of(self, hour: int) -> str:
        return clock_mod.phase_of(hour, self.phases)

    def phase_label(self, phase: str) -> str:
        return clock_mod.phase_label(phase, self.phases)

    # ── 骰子与四档 ──────────────────────────────────────────
    @property
    def dice_sides(self) -> int:
        return int(self.ruleset["dice"]["sides"])

    @property
    def crit_face(self) -> int:
        return int(self.ruleset["dice"]["crit"])

    @property
    def crit_margin(self) -> int:
        return int(self.ruleset["dice"]["crit_margin"])

    @property
    def fail_forward_floor(self) -> int:
        return int(self.ruleset["dice"]["fail_forward_floor"])

    # ── 装备 ────────────────────────────────────────────────
    @property
    def tag_vocab(self) -> list[str]:
        return list(self.equipment["tag_vocab"])

    def tier_range(self, tier: int, slot: str) -> tuple[int, int]:
        lo, hi = self.equipment["tier_range"][tier][slot]
        return int(lo), int(hi)

    def era_allowed_tags(self, era: str) -> list[str]:
        pool = self.equipment["era_pool"].get(era)
        if not pool:
            return self.tag_vocab
        return list(pool["allow"])

    def quality_for_roll(self, total: int) -> str:
        for name, cfg in self.equipment["quality"].items():
            lo, hi = cfg["roll"]
            if int(lo) <= total <= int(hi):
                return name
        return "rough"
