"""一个回合内跨阶段流转的数据。

为什么要有它：`step()` 拆成 6 个执行体之后，阶段之间共享的中间量（本回合的 plan、
场景返回、NPC 行动、玩家行动与判定结果、已发生事项摘要、叙事、提交前后的时钟与位置）
必须显式传递，而不是靠一个 240 行的函数体里的局部变量。

**这里只放数据，不放行为**：执行体在 `engine/loop.py` 的 `_phase_*` 方法里
（它们要用 `_declare_one` / `apply_delta` 等私有能力，放在 loop 内是合理的）。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnContext:
    """本回合的执行上下文。字段顺序与流转顺序一致，便于排查。"""

    # ── 回合骨架（prologue 填）──────────────────────────────
    turn: int
    started: float = 0.0
    turn_day: int = 1
    turn_hour: int = 0
    # 提交前的时钟（分钟）与位置：settle 段要用它们判断「时间有没有推进」「有没有瞬移」
    clock_before: int = 0
    prev_loc: str = ""

    # ── 回执（prologue 建，各段填）───────────────────────────
    rec: Any = None

    # 本回合每个执行体对应的声明规格（`PhaseSpec`），执行体从中读参数：
    # 例如 action 执行体读 `visibility` / `order` / `groups`。
    step_specs: dict = field(default_factory=dict)

    # 本回合的声明阶段（`PhaseSpec` 元组）：LangGraph 编排按它建节点，
    # 手写路径按它跑 for 循环——两条路径吃的是同一份声明。
    phases: tuple = ()

    # `action` 执行体起跑时刻（`_action_prepare` 记，`_action_finish` 算 declare_ms）。
    # 拆成三个节点后它得跨节点传递，不能再用局部变量。
    declare_started: float = 0.0

    # **当前正在执行**的阶段声明。一个执行体可能对应多个声明阶段
    # （`declare` + `resolve` 同属 action；夜里的行动与白天的发言也同属 action），
    # 执行体必须读这个，而不是 `step_specs`（那只保留每个执行体的第一个声明，
    # 会让第二段行动误用第一段的 `groups` / `visibility`）。
    current_spec: Any = None

    # ── director ───────────────────────────────────────────
    plan: dict = field(default_factory=dict)
    forced: list[str] = field(default_factory=list)

    # ── narration ──────────────────────────────────────────
    scene: dict = field(default_factory=dict)

    # ── action ─────────────────────────────────────────────
    present: list[str] = field(default_factory=list)
    npc_moves: list[dict] = field(default_factory=list)
    ordered: list = field(default_factory=list)
    # 同时行动的一组人（S7-3，boardgame.io 的 `activePlayers` 语义）：
    # 这批人**一起行动**，收齐后才统一结算。空 = 不是同时行动（缺省行为不变）。
    active_players: list[str] = field(default_factory=list)
    actions: list = field(default_factory=list)
    # 本回合**已提交的动作**登记（S4-1 统一入口）：`{actor_id: PlayerAction}`。
    # 用途是幂等——同一行动者重复提交时返回既有动作，不产生第二次行动。
    submissions: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    briefs: list[str] = field(default_factory=list)
    # 分组共议的决议（B66）：cid → 该角色所在组的结论。只给组内成员看，
    # 所以不混进 briefs（briefs 会被所有人都看到）。
    consensus: dict[str, list[str]] = field(default_factory=dict)
    # 本回合被技能救下的人（A2）。**私密**：不写进回执——「谁被救了」等于公开
    # 施术者的行动与刀口，属于标准规则里不该公开的信息。公开信号走「本轮无人出局」。
    healed: list[str] = field(default_factory=list)

    # ── broadcast ──────────────────────────────────────────
    out: dict = field(default_factory=dict)
    narrations: dict[str, str] = field(default_factory=dict)
