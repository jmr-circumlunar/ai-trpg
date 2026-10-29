"""M19 引擎 · 事件调度：**声明 + 纯代码打分**（S5-4）。

两条硬约束：
1. **零 LLM**：调度只做纯函数计算 —— 同一个 seed + 同一份声明 + 同一份状态
   → **同一批事件**（可预测、可复现、可进 CI）。
2. **框架零世界词**：事件类别用中性词（`encounter` / `discovery` / `hazard` /
   `social`），含义由世界包解释；框架只比 id 与类别名。

**动作三态**（确定性 vs 模型解释在**数据层**分开）：
- `text`：确定性文本 —— 代码直接用，不经模型；
- `effects`：确定性效果 —— 路径 + 值，走 StateDelta **白名单内**的 `apply_delta`；
- `intent`：模型解释意图 —— 只给 GM 当参考，**不自动执行**（模型不代替玩家决定）。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models.rng import derive


@dataclass
class ActionSpec:
    """一个事件的**动作三态**。"""

    id: str = ""
    category: str = ""
    intensity: float = 1.0
    cooldown_turns: int = 0
    requires: dict[str, Any] = field(default_factory=dict)
    # ① 确定性文本
    text: str = ""
    # ② 确定性效果（`{"path": ..., "op": ..., "value": ...}`）
    effects: list[dict[str, Any]] = field(default_factory=list)
    # ③ 模型解释意图（不自动执行）
    intent: str = ""
    # ④ 封锁声明（确定性）：本事件触发后，列出的类别在 duration_turns 回合内不再发生
    locks: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        """一行摘要（给日志与 trace 用）。"""
        return f"{self.id}({self.category}) 强度 {self.intensity:g}"


def parse_action_spec(raw: dict[str, Any]) -> ActionSpec:
    """从声明 dict 解析出动作三态（缺字段用缺省，不报错）。

    `effects` 只接受 dict 列表（路径 + 值），其它形态一律忽略 ——
    **确定性效果必须是结构化的**，不能是一句自由文本。
    """
    data = dict(raw or {})
    effects: list[dict[str, Any]] = []
    for item in data.get("effects") or []:
        if isinstance(item, dict) and item.get("path"):
            effects.append(dict(item))
    requires = dict(data.get("requires") or {})
    return ActionSpec(
        id=str(data.get("id") or ""),
        category=str(data.get("category") or ""),
        intensity=float(data.get("intensity", 1) or 0),
        cooldown_turns=int(data.get("cooldown_turns", 0) or 0),
        requires=requires,
        text=str(data.get("text") or ""),
        effects=effects,
        intent=str(data.get("intent") or ""),
        locks=dict(data.get("locks") or {}),
    )


def parse_action_specs(raws: list[dict[str, Any]] | None) -> list[ActionSpec]:
    """批量解析（声明文件里的 `events:` 段）。"""
    return [parse_action_spec(item) for item in (raws or []) if isinstance(item, dict)]


def autonomy_for(autonomy: dict[str, Any] | None) -> dict[str, Any]:
    """区域自治声明的规范化形状：`{level, allow, forbid}`。

    - `level`：`low` / `mid` / `high`（**世界内容**，框架只读并据此裁剪 GM 的自由度）；
    - `allow`：允许的事件类别（空 = 不额外限制）；
    - `forbid`：禁止的事件类别（优先级高于 allow）。
    """
    data = dict(autonomy or {})
    return {
        "level": str(data.get("level") or "mid").lower(),
        "allow": [str(x) for x in (data.get("allow") or [])],
        "forbid": [str(x) for x in (data.get("forbid") or [])],
    }


def category_allowed(category: str, autonomy: dict[str, Any] | None) -> bool:
    """该类别在当前区域自治下是否允许。"""
    rule = autonomy_for(autonomy)
    if category in rule["forbid"]:
        return False
    if rule["allow"] and category not in rule["allow"]:
        return False
    return True


def _requires_met(spec: ActionSpec, *, turn: int, region: str) -> bool:
    """前置条件是否满足（`min_turn` / `regions`）。"""
    req = spec.requires or {}
    min_turn = req.get("min_turn")
    if min_turn is not None and int(turn) < int(min_turn):
        return False
    regions = [str(x) for x in (req.get("regions") or [])]
    if regions and region not in regions:
        return False
    return True


def _on_cooldown(spec: ActionSpec, *, turn: int, cooldowns: dict[str, int]) -> bool:
    """冷却中则排除（`last_turn + cooldown_turns > turn`）。"""
    if spec.cooldown_turns <= 0:
        return False
    last = cooldowns.get(spec.id)
    if last is None:
        return False
    return int(turn) <= int(last) + int(spec.cooldown_turns)


class EventScheduler:
    """纯代码事件调度器（零 LLM、可复现）。

    选谁：候选过滤（前置 / 冷却 / 区域自治）→ 按强度排序 → 强度相同用
    **由 seed 派生**的随机数做稳定 tie-break（同 seed 必得同序）。
    """

    def __init__(
        self,
        events: list[ActionSpec] | None = None,
        *,
        seed: Any = 0,
        budget: int = 1,
        blocks: dict[str, int] | None = None,
    ) -> None:
        self.events = list(events or [])
        self.seed = seed
        self.budget = max(0, int(budget))
        # 封锁状态（纯代码记账，不落状态）：类别 -> 封锁到第几回合为止
        self.blocks = dict(blocks or {})

    def blocked(self, category: str, turn: int) -> bool:
        """该类别此刻是否被封锁（到点自动解除）。"""
        until = int(self.blocks.get(str(category), 0) or 0)
        return until > 0 and int(turn) <= until

    def apply_locks(self, selected: list[ActionSpec], turn: int) -> None:
        """把选中事件的封锁声明记进纯代码状态（确定性、可复现）。"""
        for spec in selected:
            locks = dict(spec.locks or {})
            duration = int(locks.get("duration_turns") or 0)
            if duration <= 0:
                continue
            until = int(turn) + duration
            for category in locks.get("categories") or []:
                key = str(category)
                if key:
                    self.blocks[key] = max(int(self.blocks.get(key, 0) or 0), until)

    def candidates(
        self,
        *,
        turn: int,
        region: str = "",
        cooldowns: dict[str, int] | None = None,
        autonomy: dict[str, Any] | None = None,
    ) -> list[ActionSpec]:
        """通过过滤的候选（顺序稳定：按声明顺序）。"""
        cooldowns = cooldowns or {}
        out: list[ActionSpec] = []
        for spec in self.events:
            if not spec.id:
                continue
            if not _requires_met(spec, turn=int(turn), region=str(region or "")):
                continue
            if _on_cooldown(spec, turn=int(turn), cooldowns=cooldowns):
                continue
            if not category_allowed(spec.category, autonomy):
                continue
            if self.blocked(spec.category, int(turn)):
                continue
            out.append(spec)
        return out

    def select(
        self,
        *,
        turn: int,
        region: str = "",
        cooldowns: dict[str, int] | None = None,
        autonomy: dict[str, Any] | None = None,
    ) -> list[ActionSpec]:
        """选出本回合要发生的事件（最多 `budget` 个）。

        **预算为 0 → 安静回合**（什么都不发生），这是可复现的默认行为，
        不是「出错后的兜底」。
        """
        if self.budget <= 0:
            return []
        pool = self.candidates(
            turn=int(turn), region=str(region or ""), cooldowns=cooldowns, autonomy=autonomy
        )
        if not pool:
            return []
        rng = derive(self.seed, "events", int(turn))
        ranked = sorted(
            pool,
            key=lambda spec: (-float(spec.intensity), rng.random(), spec.id),
        )
        chosen = ranked[: self.budget]
        self.apply_locks(chosen, int(turn))
        return chosen

    def allowed_categories(
        self,
        *,
        turn: int,
        region: str = "",
        cooldowns: dict[str, int] | None = None,
        autonomy: dict[str, Any] | None = None,
    ) -> list[str]:
        """本回合**允许 GM 引入**的事件类别（schema 裁剪用）。

        区域自治 `level=low` 时不给任何类别（GM 只能描述既有事实）；
        `mid` 给本回合候选的类别；`high` 给全部声明过的类别。
        """
        rule = autonomy_for(autonomy)
        declared = sorted({spec.category for spec in self.events if spec.category})
        if rule["level"] == "low":
            return []
        if rule["level"] == "high":
            return [c for c in declared if c not in rule["forbid"]]
        pool = self.candidates(
            turn=int(turn), region=str(region or ""), cooldowns=cooldowns, autonomy=autonomy
        )
        return sorted({spec.category for spec in pool if spec.category})
