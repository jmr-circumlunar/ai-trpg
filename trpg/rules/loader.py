"""规则表加载：YAML 是数值的唯一真相，代码只读不算。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..models.enums import Intent
from ..clock import core as clock_mod
from .dice_types import build_registry as build_dice_registry

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"缺少规则文件：{path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_combat_rules(root: Path | str | None = None) -> dict[str, Any]:
    """战斗数值（`rules/combat.yaml`）：命中集合 / 伤害公式 / 防御减免 / 敌人威胁折算。

    单独开一个函数，是为了让**战斗模块**不必实例化整套 `Rules` 就能取到自己的数值
    （M09 → M02 是允许的依赖方向；反向仍然禁止）。
    """
    base = Path(root) if root else DEFAULT_ROOT
    return _load(base / "rules" / "combat.yaml")


def load_belief_rules(root: Path | str | None = None) -> dict[str, Any]:
    """信念聚合参数（`rules/belief.yaml`）：先验 / 硬化 / 微调 / 平滑 / 守恒。

    「什么算已确认」不在这里——那是世界内容，由世界包 `knowledge.yaml: belief:` 声明。
    """
    base = Path(root) if root else DEFAULT_ROOT
    return _load(base / "rules" / "belief.yaml")


class Rules:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self.ruleset = _load(self.root / "rules" / "ruleset.yaml")
        self.equipment = _load(self.root / "rules" / "equipment.yaml")
        self.professions = _load(self.root / "rules" / "professions.yaml")["professions"]
        # 事件种子只有一份真相：worlds/<id>/seeds.yaml（规则层不再持有 seeds，B26）
        self.clock = _load(self.root / "rules" / "clock.yaml")
        # 地图生成参数（次要建筑数量/方位枚举/连通度）；主场景在世界包里
        self.map = _load(self.root / "rules" / "map.yaml")
        # 介入规则（P5）：有秩序的区域出事后多久必须有人到场
        self.immersion = _load(self.root / "rules" / "immersion.yaml")
        # 战斗数值（S1-1）：命中集合 / 伤害公式 / 防御减免 / 敌人威胁折算
        self.combat = load_combat_rules(self.root)
        # 信念聚合参数（S2-3）：先验 / 硬化 / 微调 / 平滑 / 守恒
        self.belief = load_belief_rules(self.root)
        # 骰型注册表（S1-2）：名字与参数在 yaml，机制在 `dice_types.py`
        self.dice = _load(self.root / "rules" / "dice.yaml")
        # 建表（幂等：同名覆盖）。世界包/规则改了 yaml，重建 `Rules` 即生效。
        build_dice_registry(self.dice)

    # ── 战斗（M09 读这里）────────────────────────────────────
    @property
    def combat_hit_outcomes(self) -> set[str]:
        """哪些判定结果算「打中了」。"""
        return {str(x) for x in (self.combat.get("hit_outcomes") or [])}

    @property
    def combat_attack_dice(self) -> int:
        return int((self.combat.get("attack") or {}).get("dice", 6))

    @property
    def combat_min_damage(self) -> int:
        return int((self.combat.get("attack") or {}).get("min_damage", 1))

    @property
    def combat_crit_multiplier(self) -> int:
        return int((self.combat.get("attack") or {}).get("crit_multiplier", 2))

    @property
    def combat_defend_reduction(self) -> int:
        return int((self.combat.get("defend") or {}).get("reduction", 2))

    @property
    def combat_defend_duration_turns(self) -> int:
        return int((self.combat.get("defend") or {}).get("duration_turns", 1))

    @property
    def combat_enemy_threat(self) -> dict[str, Any]:
        return dict((self.combat.get("enemy") or {}).get("threat") or {})

    @property
    def combat_threat_stage_bonus(self) -> dict[int, int]:
        table = (self.combat.get("enemy") or {}).get("threat_stage_bonus") or {}
        return {int(k): int(v) for k, v in table.items()}

    @property
    def combat_enemy_clamp(self) -> tuple[int, int]:
        clamp = (self.combat.get("enemy") or {}).get("clamp") or {}
        return int(clamp.get("min", 1)), int(clamp.get("max", 30))

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

    @property
    def condition_vocab(self) -> list[str]:
        """角色状态词封闭表（空 = 不过滤，向后兼容）。"""
        return [str(x) for x in (self.ruleset.get("condition_vocab") or [])]

    @property
    def death_grace_turns(self) -> int:
        """hp 归 0 后还能被救的回合数（超过即 DEAD）。"""
        return max(1, int(self.ruleset.get("death_grace_turns", 2)))

    @property
    def tension_dc_mod(self) -> dict[int, int]:
        """张力分档 → DC 修正（B1）。配置缺失 = 空表 = 不影响 DC。"""
        return {
            int(k): int(v) for k, v in (self.ruleset.get("tension_dc_mod") or {}).items()
        }

    def tension_mod(self, tension: int) -> int:
        """按当前张力取 DC 修正：取「不高于当前张力的最高档」。

        张力是环境难度（越乱越难），与模型给的 situation_delta 相加，
        不受 `situation_delta_cap` 约束——cap 管的是模型单次情境修正。
        """
        table = self.tension_dc_mod
        if not table:
            return 0
        value = max(0, min(100, int(tension or 0)))
        band = max((k for k in table if k <= value), default=min(table))
        return int(table[band])

    # ── 势力态度（B2）────────────────────────────────────────
    @property
    def attitude_dc_mod(self) -> dict[str, Any]:
        return self.ruleset.get("attitude_dc_mod") or {}

    @property
    def social_skills(self) -> list[str]:
        """哪些技能受势力态度影响（技能名只在 yaml，代码不写死）。"""
        return [str(x) for x in (self.attitude_dc_mod.get("skills") or [])]

    @property
    def attitude_inverted(self) -> list[str]:
        """态度修正取反的技能（越敌对越容易，如威吓）。"""
        return [str(x) for x in (self.attitude_dc_mod.get("inverted") or [])]

    @property
    def attitude_bands(self) -> dict[int, int]:
        return {
            int(k): int(v)
            for k, v in (self.attitude_dc_mod.get("bands") or {}).items()
        }

    @property
    def attitude_reputation_step(self) -> int:
        """声望每多少点折算一档态度。"""
        return max(1, int(self.ruleset.get("attitude_reputation_step", 5)))

    def attitude_mod(self, skill: str | None, score: int) -> int:
        """势力态度对 DC 的修正（B2）。非社交技能一律 0。"""
        if not skill or skill not in self.social_skills:
            return 0
        bands = self.attitude_bands
        if not bands:
            return 0
        keys = sorted(bands)
        value = max(keys[0], min(keys[-1], int(score or 0)))
        band = max((k for k in keys if k <= value), default=keys[0])
        mod = int(bands[band])
        return -mod if skill in self.attitude_inverted else mod

    def dc(
        self,
        action_type: str,
        stage: int,
        intent: Intent | str = Intent.normal,
        situation_delta: int = 0,
        skill: str | None = None,
        phase: str | None = None,
        tension: int = 0,
        attitude_score: int = 0,
    ) -> int:
        base = self.ruleset["dc_base"].get(action_type, self.ruleset["dc_base"]["contest"])
        stage_add = self.ruleset["stage_mod"].get(stage, 0)
        intent_key = intent.value if isinstance(intent, Intent) else str(intent)
        intent_add = self.ruleset["intent_mod"].get(intent_key, 0)

        cap = self.situation_delta_cap
        situation = max(-cap, min(cap, int(situation_delta)))

        # 时段修正（按技能），例如入夜潜行更容易、交涉更难
        time_add = clock_mod.skill_mod(phase, skill, self.phases) if phase else 0

        # 张力修正（B1）：局势越紧张越难，纯查表
        tension_add = self.tension_mod(tension)
        # 势力态度修正（B2）：只作用于社交技能，方向由 yaml 的 inverted 决定
        attitude_add = self.attitude_mod(skill, attitude_score)

        return max(
            5,
            int(base)
            + int(stage_add)
            + int(intent_add)
            + situation
            + time_add
            + int(tension_add)
            + int(attitude_add),
        )

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

    # ── 天气（只影响 DC）────────────────────────────────────
    @property
    def weather_effects(self) -> dict[str, Any]:
        return self.clock.get("weather_effects") or {}

    def weather_vocab(self) -> list[str]:
        return list(self.weather_effects.keys())

    def weather_label(self, weather: str) -> str:
        cfg = self.weather_effects.get(str(weather or "")) or {}
        return str(cfg.get("label") or weather or "")

    def weather_mod(self, weather: str, skill: str | None) -> int:
        """天气修正（正向 = 更难）。未知天气 / 未知技能一律 0，不报错。"""
        if not skill:
            return 0
        cfg = self.weather_effects.get(str(weather or "")) or {}
        return int((cfg.get("mod") or {}).get(skill, 0) or 0)

    # ── 骰型注册表（S1-2）──────────────────────────────────
    @property
    def dice_config(self) -> dict[str, Any]:
        """骰型注册表配置（`rules/dice.yaml`）：名字 / 参数 / 护栏 / 档位。"""
        return self.dice or {}

    @property
    def dice_limits(self) -> dict[str, Any]:
        return dict(self.dice.get("limits") or {})

    @property
    def dice_bands(self) -> list[dict[str, Any]]:
        """结果档位映射（数值在 yaml，代码只查表）。"""
        return list(self.dice.get("bands") or [])

    def dice_band_of(self, result: int) -> str:
        """结果落在哪一档（没命中返回空串，不猜）。"""
        from .dice_types import bands_for

        return bands_for(result, self.dice_bands)

    # ── 判定档位表（S1-3）──────────────────────────────────
    @property
    def outcomes(self) -> list[dict[str, Any]]:
        """档位表（`rules/ruleset.yaml: outcomes`）：id / label / when。"""
        return list(self.ruleset.get("outcomes") or [])

    def outcome_for(self, face: int, margin: int) -> str:
        """按档位表求值（老规则搬进 yaml 后的唯一入口）。"""
        from .outcomes import OutcomeContext, evaluate_outcome

        ctx = OutcomeContext(
            face=int(face),
            margin=int(margin),
            crit_face=self.crit_face,
            crit_margin=self.crit_margin,
            fail_forward_floor=self.fail_forward_floor,
        )
        return evaluate_outcome(self.outcomes, ctx)

    def outcome_label(self, outcome_id: str) -> str:
        """档位的中文 label（前端显示用；查不到返回空串）。"""
        from .outcomes import outcome_label as _label

        return _label(self.outcomes, outcome_id)

    def outcome_labels(self) -> dict[str, str]:
        """整张表的 id → label 映射（前端一次拿全）。"""
        from .outcomes import outcome_labels as _labels

        return _labels(self.outcomes)

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
