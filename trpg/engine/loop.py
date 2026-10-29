"""回合状态机（S1：玩家思考阶段并行）。

每回合的实际耗时结构：
    SCENE(1 次 LLM) + DECLARE(N 次 LLM，可并行) + NARRATE(N 次 LLM，串行)
并行后：总耗时 ≈ 1 + 1 + N 次调用，而不是 1 + N + N。

S2 会把它平移成 LangGraph 的 StateGraph——每个阶段都是一个独立方法，
迁移时基本是机械替换。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import time
from dataclasses import dataclass, field

from ..models import (
    CharacterCard,
    DeltaChange,
    Enemy,
    Exit,
    GameState,
    PlayerAction,
    Quest,
    StateDelta,
)
from ..growth.hooks import MetaHooks, load_hooks
from ..models.enums import CombatMode, DeltaOp, Position
from ..models.state import can_act, is_alive, is_eliminated
from ..models.rng import derive
from ..clock import core as clock_mod
from ..map import exits_for, is_adjacent
from .weather import advance_weather, transitions_of
from ..growth import identity
from ..rules.dice import DiceEngine
from ..rules.loader import Rules
from ..combat import engine as combat_mod
from ..equipment import roll as eq_mod
from ..rules.judge import AUTO_OUTCOME, auto_result, judge
from .state_merge import DeltaValidationError, apply_delta


def tool_bonus_for(rules, card, action) -> int:
    """M08 → M02 的唯一桥：装备 tag 的 DC 修正。

    规则层（
ules/judge.py）不认识装备模块，编排层把数值算好传进去。
    """
    skill = action.skill if rules.valid_skill(action.skill) else None
    return eq_mod.tool_dc_bonus(eq_mod.equipment_tags(card), rules.equipment, skill)


def _default_store():
    """默认存储门面：storage.db。

    延迟导入：编排层只在**用到时**才绑定具体存储实现，
    装配层可以注入替身（测试/换后端），实现 I5。
    """
    from ..storage import db

    return db


def normalize_options(raw) -> list[dict]:
    """把 GM 给的选项统一成 `[{id,label,hint}]`。

    兼容三种写法：旧的 `hooks: ["把复测单折进袖口"]`（字符串）、
    半结构化 `[{"label": "..."}]`、完整结构。坏数据一律丢弃，不报错。
    """
    out: list[dict] = []
    for i, item in enumerate(raw or []):
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"id": f"opt_{i + 1}", "label": text[:60], "hint": ""})
            continue
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("text") or "").strip()
        if not label:
            continue
        out.append(
            {
                "id": str(item.get("id") or f"opt_{i + 1}"),
                "label": label[:60],
                "hint": str(item.get("hint") or "")[:40],
            }
        )
    return out[:4]


@dataclass
class TurnRecord:
    turn: int
    scene: str
    # 本回合被代码强制拉进来的 authority NPC（P5 介入规则）
    intervention: list[str] = field(default_factory=list)
    # 本回合的可选行动（结构化）：GM 给的是"能立刻做的事"，不是叙事装饰
    options: list[dict] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    narration: list[str] = field(default_factory=list)
    npc_moves: list[dict] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    path_changes: list[dict] = field(default_factory=list)
    loot: list[dict] = field(default_factory=list)
    combat: list[dict] = field(default_factory=list)
    deaths: list[dict] = field(default_factory=list)
    earned: list[dict] = field(default_factory=list)   # 三期：局外发点明细
    version: int = 0
    elapsed_ms: int = 0
    declare_ms: int = 0
    # ── 阶段管线回执（B45，C54/C57/C58）─────────────────────
    # order：本回合**实际执行**的阶段 id 顺序（所有 kind 都记，复现可比对）
    # skipped：被跳过的阶段及原因（when 未命中 / break_if 命中），写入即留痕
    # votes / vote_result：集体决策的票型与计票结果（B55）
    order: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    skipped_phases: list[dict] = field(default_factory=list)
    votes: list[dict] = field(default_factory=list)
    vote_result: dict = field(default_factory=dict)


def _make_notify(progress):
    """把 SSE 回调包成 `notify(event, data)`。异常一律吞掉——前端断开不能把回合搞崩。

    手写路径与 LangGraph 路径共用，保证两条路径推出去的事件序列一致。
    """

    def notify(event: str, data: dict | None = None) -> None:
        if progress is None:
            return
        try:
            progress(event, data or {})
        except Exception:  # noqa: BLE001
            pass

    return notify


def _step_specs_from(phases) -> dict:
    """每个执行体 → 第一个匹配的声明规格（执行体从这里读 visibility / order 等参数）。"""
    from .phases import KIND_TO_STEP

    specs: dict = {}
    for phase in phases:
        step_name = KIND_TO_STEP.get(phase.kind)
        if step_name and step_name not in specs:
            specs[step_name] = phase
    return specs


def _default_plan() -> tuple[dict[str, int], dict]:
    """standard preset 的配比与规格（`turn_flow` 声明坏掉时的兜底）。"""
    from .phases import KIND_TO_STEP
    from .pipeline import parse_turn_flow

    default = parse_turn_flow({})
    counts: dict[str, int] = {}
    for phase in default:
        step_name = KIND_TO_STEP.get(phase.kind)
        if step_name:
            counts[step_name] = counts.get(step_name, 0) + 1
    return counts, _step_specs_from(default)


class GameLoop:
    def __init__(
        self,
        rules: Rules,
        cards: dict[str, CharacterCard],
        state: GameState,
        llm,
        seed: int | str = 0,
        conn: sqlite3.Connection | None = None,
        events=None,
        vectors=None,
        pack=None,
        director=None,
        force_seed: str = "",
        human=None,
        region_map: dict | None = None,
        store=None,
        engine: str = "graph",
        checkpointer=None,
        thread_id: str = "",
    ) -> None:
        self.rules = rules
        self.cards = cards
        self.state = state
        self.llm = llm
        self.dice = DiceEngine(seed)
        self.conn = conn
        # 存储门面（M03）：默认 storage.db，可由装配层注入替身
        self.store = store if store is not None else _default_store()
        # B25：摘要落库（有连接才注入；mock 局与单测保持纯内存）
        if conn is not None:
            setter = getattr(llm, "set_summary_sink", None)
            if callable(setter):
                try:
                    setter(self._persist_summary)
                except Exception:  # noqa: BLE001  摘要落库是附加能力
                    pass
        self.seed = seed
        self.next_turn = 1
        self.events = events
        self.vectors = vectors
        self.pack = pack
        self.director = director
        # 调试用：第 1 回合强制抽指定种子（验证战斗 / 战利品链路）
        self.force_seed_id = force_seed
        # 人类席位（None = 全 Agent 局）
        self.human = human
        # 调度器：`graph` = LangGraph（默认），`manual` = 手写 for 循环。
        # 两条路径逐字段等价（`tests/test_graph_engine.py` 守），保留 manual 是为了
        # 出问题能一键切回，也方便对照排查。
        self.engine = str(engine or "graph")
        # 图的存档器；`None` 时走无状态图（不能 `interrupt`，人类退回 await 语义）
        self.checkpointer = checkpointer
        # checkpointer 的线程键，空则回落 `state.session_id`（一局一条时间线）
        self.thread_id = str(thread_id or getattr(state, "session_id", "") or "")
        # 本局的地图（开局生成一次落库；None = 没有地图数据，跳过空间校验）
        self.region_map = region_map
        # 局外成长（meta）：价目表来自世界包，引擎只发事件
        self.meta_hooks = (
            load_hooks(pack.root, getattr(pack, "world_id", "")) if pack else MetaHooks()
        )
        self.meta_events: list[str] = []
        # 记忆写入/召回失败留痕（trace 可看）：静默失败是「记不住」的头号原因
        self.memory_errors: list[str] = []
        # 本回合待写入的记忆（回合末合并成一次 embedding 调用）
        self._pending_vectors: dict[str, list[str]] = {}
        self._done_quests: set[str] = set()
        # P5：介入规则的状态（近几回合打过架 / 上次派人是什么时候）
        self._combat_turns: list[int] = []
        self._last_response_turn: int = 0
        # SSE 事件出口（step 内会覆盖成真实回调）
        self._notify = lambda event, data=None: None
        self._opened = False
        # 技能写下的「回合标记」（如自爆后跳过当日剩余阶段），回合结束自动清掉
        self._skill_flags: set[str] = set()
        # 技能资源初始化（A2）：世界包声明几次就给几次；读档时用 setdefault 保留剩余量
        self._init_skill_charges()

    # ── 主循环 ──────────────────────────────────────────────
    async def run(self, turns: int) -> list[TurnRecord]:
        records: list[TurnRecord] = []
        for _ in range(turns):
            records.append(await self.step())
        return records

    def _open_turn(self, turn: int | None):
        """建本回合上下文。**手写路径与 LangGraph 路径共用**——这是两条路径等价的基础。

        只做「准备」，不跑任何阶段：`step()` 用 for 循环调度，`graph.run_turn()` 用条件边调度，
        两者拿到的是同一个 `TurnContext`。
        """
        from .phases import TurnContext

        t = turn if turn is not None else self.next_turn
        self.next_turn = t + 1
        # 回执先建：narration 执行体只往里填内容，这样它被跳过也不会没有 rec
        rec = TurnRecord(turn=t, scene="")
        phases, step_specs = self._turn_specs(rec, t)

        ctx = TurnContext(
            turn=t,
            started=time.perf_counter(),
            turn_day=self.state.clock.day,
            turn_hour=self.state.clock.hour,
            # 提交前的时钟与位置：settle 段判断「时间没推进」「有没有瞬移」用
            clock_before=clock_mod.to_minutes(
                self.state.clock.day, self.state.clock.hour, self.state.clock.minute
            ),
            prev_loc=self.state.scene.location_id,
            rec=rec,
            step_specs=step_specs,
        )
        ctx.phases = tuple(phases)
        return ctx

    async def step(self, turn: int | None = None, progress=None) -> TurnRecord:
        """推进一个回合。**默认走 LangGraph**（`engine="graph"`）。

        `progress(event, data)` 用于 SSE：每过一个阶段回调一次。回调异常一律吞掉，
        绝不能因为前端断开就把一回合搞崩。

        回合由 6 个执行体组成（`engine/phases/__init__.py: PHASE_STEPS`）：
        `director`（固定）→ 世界声明的阶段 → `settle`（固定）。差别只在调度：

        | engine | 调度 | 中断 | 存档 |
        |---|---|---|---|
        | `graph`（默认） | `StateGraph` + 条件边 | `interrupt()` | checkpointer |
        | `manual` | for 循环 | `HumanSeat.wait_action` | 无 |

        两条路径**逐字段等价**（`tests/test_graph_engine.py` 守），保留 manual 是为了
        出问题能一键切回。
        """
        if self.engine == "graph":
            from .graph import run_turn

            return await run_turn(
                self,
                turn=turn,
                progress=progress,
                checkpointer=self.checkpointer,
                thread_id=self.thread_id,
            )
        return await self._step_manual(turn, progress)

    async def _step_manual(self, turn: int | None = None, progress=None) -> TurnRecord:
        """**手写状态机**路径：for 循环调度，无存档、无 interrupt。"""
        # 子方法（_declare_one / _phase_* 等）也要能推事件
        self._notify = _make_notify(progress)

        ctx = self._open_turn(turn)

        # 固定段 ①：回合前的骨架（开场主线、导演决策、强制到场）
        await self._phase_director(ctx)
        # 世界声明的阶段：`when` / `break_if` 逐阶段运行时求值
        await self._run_phases(ctx.phases, ctx)
        # 固定段 ②：回合后的骨架（空间 / 时间 / 死亡 / 落库 / 收尾）
        await self._phase_settle(ctx)

        ctx.rec.elapsed_ms = int((time.perf_counter() - ctx.started) * 1000)
        return ctx.rec


    # ── 各阶段 ──────────────────────────────────────────────
    # ── 阶段执行体（顺序与注册见 engine/phases/__init__.py） ────────────────
    #
    # 这里只做「搬出 step()」的机械拆分：每个执行体的代码与拆分前逐行等价，
    # 跨阶段共享的中间量通过 `TurnContext` 传递，不再靠 240 行函数体里的局部变量。

    async def _phase_director(self, ctx: TurnContext) -> None:
        """回合前（固定段）：开场主线 + 本回合结构决策 + 强制到场名单。"""
        self._notify("stage", {"phase": "director", "turn": ctx.turn})
        self._ensure_opening()
        plan = (
            self.director.plan_turn(self.state, ctx.turn)
            if self.director is not None
            else {}
        )
        ctx.plan = plan
        if plan.get("seed") is not None:
            self._force_seed(plan["seed"])

        # 调试入口：--force-seed 指定第 1 回合的种子（优先于导演的选择）
        if self.force_seed_id and ctx.turn == 1:
            forced_seed = next(
                (
                    s
                    for s in (getattr(self.pack, "seeds", None) or [])
                    if str(s.get("id")) == self.force_seed_id
                ),
                None,
            )
            if forced_seed is not None:
                self._force_seed(forced_seed)

        # P5：谁该来，由代码算（有秩序的区域出事后必须有人到场，不靠 GM 自觉）
        ctx.forced = self._responders_for(ctx.turn)

    async def _phase_narration(self, ctx: TurnContext) -> None:
        """生成场景并申报敌人（C25 唯一入口）。"""
        self._notify("stage", {"phase": "scene"})
        scene = await asyncio.to_thread(self.llm.describe_scene, self.state, ctx.turn)
        ctx.scene = scene
        ctx.rec.scene = scene.get("narration", "")
        # options 是新协议；hooks 是旧字段，一并兼容，不让旧世界包/旧 prompt 失效
        ctx.rec.options = normalize_options(
            scene.get("options") or scene.get("hooks")
        )
        # 发完整叙事：前端用打字机逐字渲染，不再截断（否则只能看到半句）
        self._notify("scene", {"text": ctx.rec.scene})

        # 场景里申报的威胁立刻进硬状态：玩家在行动阶段必须能看到它，
        # 否则「先叙事、后申报敌人」等于敌人永远晚一步到场。
        self._apply_enemies(scene.get("enemies"), ctx.rec, ctx.turn)

    async def _phase_action(self, ctx: TurnContext) -> None:
        """NPC 并行行动 + 玩家逐个行动并判定（默认路径：三步连跑）。

        DECLARE 与 RESOLVE 必须在同一个循环里：后者能看到前者「做了什么、结果如何」，
        这是跑团的核心，拆成两个阶段就会破坏 briefs 的语义。

        拆成 prepare / one / finish 三步是为了**支持中断**：`interrupt()` 恢复时节点会从头
        重跑，所以单个角色的处理必须能独立重入（见 `_action_one` 的幂等检查）。
        这里三步连跑，行为与拆分前逐行等价。
        """
        await self._action_prepare(ctx)
        for index in range(len(ctx.ordered)):
            await self._action_one(ctx, index)
        self._action_finish(ctx)

    async def _action_prepare(self, ctx: TurnContext) -> None:
        """共议 + NPC 并行 + 排序 + 清空累加器。**只做一次**，不参与重入。"""
        # 分组共议（B66）：先让各组内部对齐，把结论写进 briefs，之后照常逐个行动
        action_spec = self._spec_for(ctx, "action")
        groups_decl = list(action_spec.param("groups") or []) if action_spec is not None else []

        # 谁有资格行动（D6）：夜里不该让「没有夜间能力的人」也申报一次行动。
        actors_mode = str(action_spec.param("actors", "all")) if action_spec is not None else "all"
        actors = self._active_actors(actors_mode, groups=groups_decl)

        for group in groups_decl:
            mode = str(group.get("mode") or "single").strip().lower()
            if mode not in ("consensus", "council"):
                continue
            members = self._group_members(actors, group)
            if not members:
                continue
            if mode == "council":
                await self._council_round(ctx, members, group)
            else:
                await self._consensus_round(ctx, members, group)

        # ⚡ NPC：在场 NPC 各自按自己的目标行动（独立 Agent，并行）
        ctx.present = list(ctx.scene.get("present_npcs") or [])
        # 介入是**强制**的：GM 没申报也照样到场（它接下来会按自己的目标行动并进叙事）
        if ctx.forced:
            for nid in ctx.forced:
                if nid not in ctx.present:
                    ctx.present.append(nid)
            ctx.rec.intervention = list(ctx.forced)
            self._last_response_turn = ctx.turn
            self._notify("intervention", {"names": ctx.forced})

        ctx.npc_moves = []
        npc_hook = getattr(self.llm, "npc_action", None)
        if ctx.present and callable(npc_hook):
            ctx.npc_moves = list(
                await asyncio.gather(
                    *[
                        asyncio.to_thread(npc_hook, npc, self.state, ctx.turn)
                        for npc in ctx.present
                    ]
                )
            )
            ctx.rec.npc_moves = ctx.npc_moves
            self._notify(
                "npc",
                {
                    "count": len(ctx.npc_moves),
                    "names": [m.get("name") for m in ctx.npc_moves],
                },
            )

        ctx.declare_started = time.perf_counter()
        ctx.ordered = self._order_actors(actors, action_spec, ctx)
        ctx.actions = []
        ctx.results = []
        ctx.briefs = []

    async def _action_one(self, ctx: TurnContext, index: int) -> None:
        """处理 `ctx.ordered[index]`：declare → judge → briefs → notify。

        **幂等**：该角色已经声明过（`len(ctx.actions) > index`）就直接返回。
        这是 `interrupt()` 恢复时「节点从头重跑」不产生重复模型调用的前提——
        没有它会重复花一次调用，还会把同一句话写两遍 briefs。
        """
        if index < len(ctx.actions):
            return
        if index >= len(ctx.ordered):
            return

        cid, card = ctx.ordered[index]
        spec = self._spec_for(ctx, "action")
        visibility = (
            str(spec.param("visibility", "previous")) if spec is not None else "previous"
        )
        # 阶段级可见性（C48 / B67）：
        #   previous / public → 后手能看到前手做了什么（现状）
        #   none / private    → 不累积，每个角色都在「不知道别人做了什么」的情况下决策
        #   group             → 需要分组信息（B66）；本批先按 private 处理，不静默当 public
        share_briefs = visibility in ("previous", "public")
        # 判定开关（C55）：世界可以关掉掷骰——行动照常发生、照常叙事，只是不判定
        judge_on = self._judge_enabled()

        shared = ctx.briefs if share_briefs else []

        # 私有信息（A2）：本人「已经确认」的事（查验结果、被谁救过）永远只给他自己。
        # 这些不进 `ctx.briefs`——那是组内/公共通道，写进去就等于公开。
        runtime = self.state.characters.get(cid)
        notes = [
            f"（已知）{str(item)}"
            for item in (getattr(runtime, "notes", []) or [])
        ]
        # 组内决议只给组员看（B66），不混进公共 briefs
        action = await self._declare_one(
            cid,
            card,
            ctx.turn,
            [*notes, *shared, *ctx.consensus.get(cid, [])],
            self._knowledge_for(ctx, cid, getattr(ctx, "current_spec", None)),
            getattr(ctx, "submissions", None),
        )
        if judge_on:
            result = judge(
                action,
                card,
                self.state,
                self.rules,
                self.dice,
                tool_bonus=tool_bonus_for(self.rules, card, action),
                # B2：态度分由编排层合成，M02 只拿整数查表
                attitude_score=self._attitude_score(action),
            )
        else:
            # 不掷骰：给一个占位结果（时间兜底要用 action_type），但不写 outcomes
            result = auto_result(action, self.rules)
        ctx.actions.append(action)
        ctx.results.append(result)
        ctx.briefs.append(
            f"{card.name} {action.skill or '行动'}·{result.outcome}：{action.action[:30]}"
        )
        # 发言进 briefs：谁能看到由阶段可见性决定（私密阶段 briefs 不传给后手）
        spoken = getattr(self.llm, "spoken", None) or []
        for said in spoken:
            ctx.briefs.append(f"{said['name']} 说：{said['text'][:40]}")
        if spoken:
            spoken.clear()
        payload = {
            "id": cid,
            "name": card.name,
            "skill": action.skill,
            "outcome": result.outcome,
            "total": result.total,
            "dc": result.dc,
            "action": action.action[:40],
            # 心理活动（上帝视角的心声流用它；是否下发由 API 层按
            # `visibility.thoughts` 裁剪，见 `routes/play.py::emit`）
            "thought": action.thought or "",
        }
        # 分组私密阶段（`visibility: group`）：**「谁在夜里动了手」本身就是信息**
        # （等于把阵营标出来）。所以这条事件只发给本阶段的参与者，观察者照常可见。
        spec = ctx.current_spec
        visibility = str(spec.param("visibility", "previous")) if spec is not None else "previous"
        if visibility == "group":
            payload["audience"] = [item[0] for item in ctx.ordered]
        self._notify("actor", payload)

    def _action_finish(self, ctx: TurnContext) -> None:
        """收尾：记录 declare 段耗时。"""
        ctx.rec.declare_ms = int((time.perf_counter() - ctx.declare_started) * 1000)

    async def _phase_collective(self, ctx: TurnContext) -> None:
        """集体决策（B55 / C57）：收集票 → 代码计票 → 写回执。

        LLM 只提交「投给谁」，谁票多由 `tally()` 算，平票用派生 RNG（同 seed 可复现）。
        出局者不参与（`eligible: alive`），这是 B56 在投票上的体现。
        """
        from .phases.collective import tally

        spec = self._spec_for(ctx, "collective")
        rule = str(spec.param("rule", "plurality")) if spec is not None else "plurality"
        tie = str(spec.param("tie", "random")) if spec is not None else "random"
        eligible = str(spec.param("eligible", "alive")) if spec is not None else "alive"

        voters = self._eligible_voters(eligible)
        if not voters:
            return

        from .phases.collective import ABSTAIN, VoteRecord

        # 人类席位要**顺序**等票（同一时刻只能等一个人），其余角色并发自动投票
        human_ids = set(self.human.human_ids) if self.human is not None else set()
        auto_voters = [(cid, card) for cid, card in voters if cid not in human_ids]

        # 平票重投（标准规则）：第一次平票时，只让**平票的几个人**再被投一轮；
        # 再平票才算「本轮无人出局」。`rounds` 控制最多投几轮。
        max_rounds = max(1, int(spec.param("rounds", 2))) if spec is not None else 1
        targets = voters
        result = None

        for round_no in range(1, max_rounds + 1):
            collected = await asyncio.gather(
                *[
                    self._collect_vote(cid, card, ctx.turn, targets)
                    for cid, card in auto_voters
                ]
            )
            votes = [vote for vote in collected if vote is not None]

            for cid, card in voters:
                if cid not in human_ids:
                    continue
                votes.append(
                    VoteRecord(
                        voter=cid,
                        target=await self._ask_human_vote(cid, card, ctx, targets),
                        round=round_no,
                    )
                )

            # `VoteRecord` 是 frozen dataclass：标轮次只能重建，不能赋值
            votes = [
                VoteRecord(
                    voter=vote.voter,
                    target=vote.target,
                    round=round_no,
                    kind=vote.kind,
                    reason=vote.reason,
                    suspect=vote.suspect,
                )
                for vote in votes
            ]
            result = tally(votes, rule=rule, tie=tie, rng=self._vote_rng(ctx.turn))
            # 与组内共议的票合并（按声明顺序累积），不要覆盖
            ctx.rec.votes.extend(vote.as_dict() for vote in votes)
            result.rounds = round_no

            if not result.tie or tie != "runoff" or round_no >= max_rounds:
                break

            valid = {
                key: value for key, value in result.counts.items() if key != ABSTAIN
            }
            if not valid:
                break
            top = max(valid.values())
            leaders = [key for key, value in valid.items() if value == top]
            if len(leaders) < 2:
                break
            # 只让平票者成为下一轮的候选（投票人仍是全体）
            targets = [(cid, card) for cid, card in voters if cid in leaders]

        if result is None:
            return
        # 用 update 而不是赋值：前面的组内共议（consensus）也在 vote_result 里，
        # 覆盖掉会让「夜里商量好杀谁」的结果丢失。
        ctx.rec.vote_result.update(result.as_dict())

    def _eligible_voters(self, eligible: str):
        """有投票资格的人：`alive` 排除出局者（B56），`all` 含所有人。"""
        from ..models.enums import Position

        voters = []
        for cid, card in self.cards.items():
            rt = self.state.characters.get(cid)
            if rt is None:
                continue
            if eligible == "alive" and not can_act(rt):
                continue
            voters.append((cid, card))
        return self._order_by_dex(voters)

    async def _collect_vote(self, cid, card, turn: int, voters):
        """收一张票：优先用模型的 `vote_detail()`（带理由），没有就退回 `vote()`/普通行动。

        S2-4：票要**带理由**，并且能跟发言里的怀疑**对齐**——
        模型声明的怀疑优先；没声明就用该角色信念里最可疑的那位（S2-3 的产物）。
        """
        from .phases.collective import VoteRecord

        candidates = [other for other, _ in voters if other != cid]
        if not candidates:
            return None

        detail: dict = {}
        hook = getattr(self.llm, "vote_detail", None)
        if callable(hook):
            detail = (
                await asyncio.to_thread(hook, cid, card, self.state, turn, candidates)
            ) or {}
            target = detail.get("target")
        else:
            hook = getattr(self.llm, "vote", None)
            if callable(hook):
                target = await asyncio.to_thread(
                    hook, cid, card, self.state, turn, candidates
                )
            else:
                action = await asyncio.to_thread(
                    self.llm.player_action, cid, card, self.state, turn, []
                )
                target = getattr(action, "target", None)

        suspect = str(detail.get("suspect") or "") or self._top_suspicion(cid)
        reason = str(detail.get("reason") or "") or self._suspicion_reason(cid, suspect)

        return VoteRecord(
            voter=cid,
            target=target if target in candidates else None,
            reason=reason,
            suspect=suspect,
        )

    def _top_suspicion(self, cid: str) -> str:
        """该角色当前**最可疑**的对象（来自信念状态 S2-3）；没有信念 = 空。"""
        runtime = (self.state.characters or {}).get(str(cid))
        mindset = getattr(runtime, "mindset", None)
        suspicions = dict(getattr(mindset, "suspicions", None) or {})
        if not suspicions:
            return ""
        best = max(
            suspicions.items(),
            key=lambda kv: float(getattr(kv[1], "confidence", 0.0) or 0.0),
        )
        return str(best[0])

    def _suspicion_reason(self, cid: str, target: str) -> str:
        """怀疑对象的**依据**（发言里的怀疑要有依据；没有依据就不用它当票理由）。"""
        if not target:
            return ""
        runtime = (self.state.characters or {}).get(str(cid))
        mindset = getattr(runtime, "mindset", None)
        item = (getattr(mindset, "suspicions", None) or {}).get(str(target))
        return str(getattr(item, "reason", "") or "")

    async def _ask_human_vote(self, cid, card, ctx, voters) -> str | None:
        """等人类投票（B55 / C57）。

        候选是其他存活者；超时或交还给 AI 都算**弃权**（`target=None`），
        不猜票、不代投——代投会让「谁投了谁」变成假的。
        """
        candidates = [other for other, _ in voters if other != cid]
        if not candidates:
            return None

        raw = await self.human.wait_action(
            cid,
            card.name,
            ctx.turn,
            [],
            [],
            kind="vote",
            candidates=candidates,
        )
        target = str((raw or {}).get("target") or "")
        return target if target in candidates else None

    def _vote_rng(self, turn: int) -> random.Random:
        """平票用的派生 RNG（C57）：同 seed + 同回合 → 同结果。"""
        return derive(self.seed, turn, "vote")

    def _consensus_targets(self, ctx, members, group: dict) -> list[str]:
        """共议的候选人池（按 `groups[].targets` 决定，缺省组内）。"""
        mode = str(group.get("targets") or "inside").strip().lower()
        inside = [cid for cid, _ in members]
        if mode == "inside":
            return inside

        alive = [cid for cid, _ in self._active_actors()]
        if mode == "all":
            return alive
        # outside：组外存活者
        member_ids = set(inside)
        return [cid for cid in alive if cid not in member_ids]

    def _group_members(self, actors, group: dict):
        """按组的筛选条件取成员：`by` camp / profession，或显式 `ids`。"""
        by = str(group.get("by") or "")
        value = str(group.get("value") or "")
        ids = set(group.get("ids") or [])

        members = []
        for cid, card in actors:
            if ids and cid not in ids:
                continue
            rt = self.state.characters.get(cid)
            if by == "camp" and str(getattr(rt, "camp", "") or "") != value:
                continue
            if by == "profession" and str(card.ext.profession or "") != value:
                continue
            members.append((cid, card))
        return members

    async def _council_round(self, ctx: TurnContext, members, group: dict) -> None:
        """组内**有限轮对话**（`mode: council`）：先说话，再说定不定。

        与 `consensus` 的区别：

        - `consensus` = 各投一票，一轮出结果，**没有交流**；
        - `council` = 先轮流发言 `rounds` 轮，把对话写进组内 briefs，再（可选）投票。

        为什么必须有上限：**开放式多轮对话会把一回合的耗时和费用拉爆**。
        所以两道闸——`rounds`（轮次）与 `budget_s`（真实秒数，兜底防止模型啰嗦），
        任一先到就停。这也是「交流应该有约束」的落地方式。

        `then` 决定说完之后做什么：`vote`（缺省，再投一轮）或 `none`（只交流）。
        """
        rounds = max(1, int(group.get("rounds") or 2))
        budget = float(group.get("budget_s") or 0)
        hook = getattr(self.llm, "council_say", None)

        # 模型没有对话能力：退回一轮投票，不让整段流程空转
        if not callable(hook):
            await self._consensus_round(ctx, members, group)
            return

        human_ids = set(self.human.human_ids) if self.human is not None else set()
        label = str(group.get("name") or group.get("value") or "小组")
        # 事件的可见范围（C21）：组内成员 id。**出站裁剪归 API 层**——
        # 这里只如实申报「这次交流发生在谁之间」，不判断谁在看。
        audience = [cid for cid, _ in members]
        transcript: list[str] = []
        started = time.monotonic()

        for _ in range(rounds):
            if budget and time.monotonic() - started >= budget:
                break
            spoke = 0
            for cid, card in members:
                # 人类不自动发言：要有话说得由人类自己提交
                if cid in human_ids:
                    continue
                if budget and time.monotonic() - started >= budget:
                    break
                try:
                    line = await asyncio.to_thread(
                        self._call_with_knowledge,
                        hook,
                        self._knowledge_for(ctx, cid, getattr(ctx, "current_spec", None)),
                        cid,
                        card,
                        self.state,
                        ctx.turn,
                        list(transcript),
                    )
                except Exception:  # noqa: BLE001  一个人说不出来不该拖垮整组
                    continue
                text = str(line or "").strip()
                if not text:
                    continue
                transcript.append(f"{card.name}：{text[:80]}")
                spoke += 1
                self._notify(
                    "council",
                    {
                        "group": label,
                        "name": card.name,
                        "text": text[:60],
                        # 空列表 = 不限（API 层据此决定是否按查看者丢弃）
                        "audience": audience,
                    },
                )
            if not spoke:
                # 整轮没人开口（都弃权/都失败）：再聊也是空转
                break

        if not transcript:
            return

        # 对话只进**组内**成员的 briefs：组外不该知道他们商量了什么
        block = f"【{label}的商议】\n" + "\n".join(f"- {line}" for line in transcript)
        for cid, _ in members:
            ctx.consensus.setdefault(cid, []).append(block)

        if str(group.get("then") or "vote").strip().lower() == "vote":
            await self._consensus_round(ctx, members, group)

    async def _consensus_round(self, ctx: TurnContext, members, group: dict) -> None:
        """组内共议（B66）：先内部投一轮，把结论写进组内所有人的 briefs。

        **不引入「组代表」**：决议只影响组内成员下一步做什么，每人仍各自行动，
        这样 `TurnRecord.actors/actions/outcomes` 的平行结构不会被打破。

        `targets` 决定「这一票是选谁」：

        | 取值 | 候选人 | 用于 |
        |---|---|---|
        | `inside`（缺省） | 组内其他人 | 内部推举（谁去谈、谁先开口） |
        | `outside` | 组外存活者 | 对外行动（选谁出局） |
        | `all` | 所有存活者 | 不限阵营的表决 |

        缺省 `inside` 保持旧行为。真实模型跑狼人杀时暴露过：不配 `outside` 的话，
        狼人夜里会把票投给自己人——**把队友杀了**。
        """
        from .phases.collective import VoteRecord, tally

        candidates = self._consensus_targets(ctx, members, group)
        if not candidates:
            return
        human_ids = set(self.human.human_ids) if self.human is not None else set()
        proposals = []
        for cid, card in members:
            # 人类不参与共议票：代投会让「谁投了谁」变成假的
            if cid in human_ids:
                continue
            others = [other for other in candidates if other != cid]
            if not others:
                continue
            hook = getattr(self.llm, "vote", None)
            target = (
                await asyncio.to_thread(hook, cid, card, self.state, ctx.turn, others)
                if callable(hook)
                else None
            )
            proposals.append(
                VoteRecord(
                    voter=cid,
                    target=target if target in others else None,
                    kind="consensus",
                )
            )

        if not proposals:
            return

        result = tally(proposals, tie="random", rng=self._vote_rng(ctx.turn))
        label = str(group.get("name") or group.get("value") or "小组")
        decision = result.winner or "未达成一致"
        line = f"【{label}的共同决定】{decision}"
        for cid, _ in members:
            ctx.consensus.setdefault(cid, []).append(line)
        ctx.rec.votes.extend(vote.as_dict() for vote in proposals)
        # 共识决议单独记一份：`apply` 阶段要靠它把结果落实成状态变更（通用能力，不绑定任何世界）
        ctx.rec.vote_result["consensus"] = {
            "group": label,
            "winner": result.winner,
            "counts": dict(result.counts),
            "tie": result.tie,
        }

    async def _phase_apply(self, ctx: TurnContext) -> None:
        """把上一阶段的结果落实为状态变更（B56 / C58）。

        目前只实现 `effect: eliminate`：把集体决策的胜出者置为出局。
        其它 effect（mark / flag / item_shift）留到需要时再加，不预置空壳。
        """
        from ..models.enums import Position

        spec = self._spec_for(ctx, "apply")
        effect = (
            str(spec.param("effect", "eliminate")) if spec is not None else "eliminate"
        )
        if effect != "eliminate":
            return

        # 目标来源：`collective_result` = 集体决策的胜出者；
        # `consensus_result` = 组内共议的决议（夜里商量好的一致目标）。
        target_kind = (
            str(spec.param("target", "collective_result")) if spec is not None
            else "collective_result"
        )
        if target_kind == "consensus_result":
            consensus = ctx.rec.vote_result.get("consensus") or {}
            target = str(consensus.get("winner") or "")
        else:
            target = str(ctx.rec.vote_result.get("winner") or "")
        if not target:
            return

        rt = self.state.characters.get(target)
        if rt is None or rt.position == Position.dead:
            return

        # 被救下（女巫解药那类效果）：本回合不淘汰。**「谁被救了」是私密信息**
        # （等于公开刀口 + 施术者），所以只记在 `ctx.healed`，不进回执；
        # 对外只有标准规则允许的那句「本轮无人出局」（由广播阶段发）。
        if target in ctx.healed:
            return

        if not self._mark_dead(ctx, target, target_kind):
            return
        # 遗言（标准规则）：刚出局的人说一句，**公开**。写进 deaths 条目本身，
        # 不新增表结构——`deaths` 已经是 list[dict]，加个键就能随回执下发。
        #
        # 时机由世界包声明：`always`（缺省，保持旧行为）或 `first_turn`（只有第 1 回合）。
        # 为什么不用 `when`：`when` 跳的是**整个阶段**，连「落实淘汰」一起跳掉，
        # 表现成「没人出局」——比没有遗言严重得多。
        if spec is not None and bool(spec.param("last_words", False)):
            when = str(spec.param("last_words_when", "always"))
            if when == "always" or (when == "first_turn" and ctx.turn == 1):
                await self._say_last_words(ctx, target, target_kind)

        # 出局触发的技能（如开枪）。被毒出局走的是 `_mark_dead(poison)` 分支，
        # 不经这里——标准规则：被毒不能开枪。
        await self._run_death_skills(ctx, target, target_kind)

    async def _say_last_words(self, ctx: TurnContext, cid: str, cause: str) -> None:
        """让刚出局的人留一句遗言（失败就当没说，不影响收尾）。"""
        hook = getattr(self.llm, "last_words", None)
        card = self.cards.get(cid)
        if not callable(hook) or card is None:
            return

        try:
            words = await asyncio.to_thread(
                self._call_with_knowledge,
                hook,
                self._knowledge_for(ctx, cid, getattr(ctx, "current_spec", None)),
                cid,
                card,
                self.state,
                ctx.turn,
                cause,
            )
        except Exception:  # noqa: BLE001
            return

        text = str(words or "").strip()
        if not text:
            return
        if ctx.rec.deaths:
            ctx.rec.deaths[-1]["last_words"] = text[:160]
        self._notify(
            "eulogy", {"id": cid, "name": card.data.name, "text": text[:80]}
        )

    async def _phase_check(self, ctx: TurnContext) -> None:
        """阶段边界判胜负（C58）：纯代码，LLM 不参与。

        判出来就写 `state.winner`，后续阶段的 `break_if: win_decided` 才会命中。
        """
        self._decide_win(ctx.rec)

    def _decide_win(self, rec: TurnRecord) -> None:
        """判胜负并写进 state（C58）。已分出胜负就不再改。"""
        from .ending import evaluate_win, is_wipe

        if self.state.winner:
            return

        # 身份表必须传：按身份分组的胜负规则（`group_eliminated`）离不开它，
        # 缺了会抛错——判胜不能靠猜。
        result = evaluate_win(self.state, self.pack, self.cards)
        if result is not None:
            self.state.winner = result.winner or result.kind
            rec.vote_result["win"] = {
                "kind": result.kind,
                "winner": result.winner,
                "detail": result.detail,
            }
            return

        # 没配 win_condition 的世界：沿用旧语义（全员出局即终局），行为不变
        if is_wipe(self.state):
            self.state.winner = "all"
            rec.vote_result["win"] = {
                "kind": "all_eliminated",
                "winner": "all",
                "detail": "legacy",
            }

    async def _phase_broadcast(self, ctx: TurnContext) -> None:
        """叙事 +（可选）代码侧公布。

        `source: eliminated_this_turn` 时，**代码**把本回合出局名单写进公共 briefs
        并发 `broadcast` 事件——这条路以前没实现，世界包写了也不生效（D5）。
        叙事仍由 GM 负责：公布是事实，怎么说归模型。
        """
        spec = self._spec_for(ctx, "broadcast")
        source = str(spec.param("source", "static")) if spec is not None else "static"
        if source == "eliminated_this_turn":
            names = [
                self._name_of(str(item.get("id") or item.get("char_id") or ""))
                for item in ctx.rec.deaths
            ]
            names = [name for name in names if name]
            if names:
                ctx.briefs.append(f"【公布】本回合出局：{'、'.join(names)}")
            else:
                # 「无人出局」也是公开信息（标准板会报平安夜）；但只说没人出局，
                # 不透露是谁被救的——那是私密行动。
                ctx.briefs.append("【公布】本回合无人出局")
            self._notify("broadcast", {"source": source, "names": names})

        ctx.out = await asyncio.to_thread(
            self.llm.narrate_batch,
            self.state,
            ctx.actions,
            ctx.results,
            ctx.turn,
            ctx.npc_moves,
        )
        ctx.narrations = ctx.out.get("narrations") or {}
        self._notify("narrate", {"count": len(ctx.narrations)})

    async def _phase_commit(self, ctx: TurnContext) -> None:
        """整回合的 delta 一次合并，并结算掉落与战斗。"""
        delta = ctx.out.get("state_delta")
        if delta is not None:
            try:
                self.state = apply_delta(
                    self.state,
                    delta,
                    self.cards,
                    self.rules.clock,
                    self.pack,
                    # 只有 GM 的 delta 需要词表过滤（其余内部 delta 不碰 conditions）
                    condition_vocab=self.rules.condition_vocab,
                )
            except DeltaValidationError as exc:
                ctx.rec.rejected.append("; ".join(exc.errors[:3]))
        self._sync_paths(ctx.rec)
        self._apply_loot(ctx.out.get("loot"), ctx.rec, ctx.turn)
        # 敌人只有 SCENE 一个申报入口（C25）：叙事已过玩家决策，此处不再收敌人
        self._resolve_combat(
            ctx.rec, ctx.turn, ctx.ordered, ctx.actions, ctx.results
        )
        self._notify(
            "commit",
            {
                "version": self.state.version,
                "loot": len(ctx.rec.loot),
                "combat": len(ctx.rec.combat),
            },
        )

    async def _phase_settle(self, ctx: TurnContext) -> None:
        """回合后（固定段）：空间校验、时间兜底、死亡结算、落库与收尾。"""
        rec = ctx.rec
        t = ctx.turn

        # 空间校验：移动必须沿边，出口由代码按地图重算（GM 不得凭空造路）
        self._guard_scene(ctx.prev_loc, rec)

        # 时间兜底：GM 忘了申报也要往前走，否则宵禁/限时永远不会发生
        clock_after = clock_mod.to_minutes(
            self.state.clock.day, self.state.clock.hour, self.state.clock.minute
        )
        if clock_after <= ctx.clock_before:
            fallback = self.rules.fallback_minutes(
                [r.action_type for r in ctx.results]
            )
            try:
                self.state = apply_delta(
                    self.state,
                    StateDelta(
                        turn=t,
                        changes=[
                            DeltaChange(
                                path="clock.advance",
                                op=DeltaOp.add,
                                value=fallback,
                            )
                        ],
                    ),
                    self.cards,
                    self.rules.clock,
                )
            except DeltaValidationError as exc:
                rec.rejected.append("time: " + "; ".join(exc.errors[:2]))

        # 天气演化（S5-5）：框架只给钩子，过渡声明为空 = 不演化（缺省零变化）
        try:
            transitions = transitions_of(getattr(self.rules, "clock", None) or {})
            if transitions:
                new_weather, changed = advance_weather(
                    self.state.clock.weather,
                    transitions,
                    turn=int(t),
                    seed=getattr(self, "seed", "") or self.state.session_id,
                )
                if changed:
                    self.state.clock.weather = new_weather
        except Exception:  # noqa: BLE001  天气演化失败不该拦住回合
            pass

        # 死亡结算：先处理濒死（含本回合没行动的人，比如刚被敌人打到的）
        for cid in list(self.state.characters):
            self._resolve_death(cid, t, rec)

        for (cid, card), action, result in zip(
            ctx.ordered, ctx.actions, ctx.results
        ):
            rec.actors.append(cid)
            rec.thoughts.append(action.thought)
            rec.actions.append(action.action)
            # C55：关判定时不产生 outcome（列表为空），前端按「未判定」渲染
            if result.outcome != AUTO_OUTCOME:
                rec.outcomes.append(f"{result.outcome} ({result.total} vs {result.dc})")
            text = ctx.narrations.get(cid, "")
            rec.narration.append(text)
            self._persist(t, cid, action, result, {"narration": text}, ctx.scene)
            self._record_event(cid, action, result, t, ctx.turn_day, ctx.turn_hour)

        if rec.combat:
            self._combat_turns.append(t)
            self._combat_turns = self._combat_turns[-10:]

        self._flush_vectors(t)

        # 任务完成 → 局外事件（同一个 quest 只发一次）
        for q in self.state.quests:
            status = getattr(q.status, "value", q.status)
            if status == "done" and q.id not in self._done_quests:
                self._done_quests.add(q.id)
                self.meta_events.append("quest_done")

        self._maybe_advance_stage(t, bool(ctx.plan.get("advance_stage")))

        # 上下文压缩：每 N 回合把旧原文压成一条回顾
        hook = getattr(self.llm, "maybe_summarize", None)
        if callable(hook):
            try:
                hook(t)
            except Exception:  # noqa: BLE001
                pass

        # 终局兜底（C58）：有 check 阶段时已在阶段边界判过；没有 check 的世界
        # （ironvale）在这里判一次，保证「全员出局即终局」的行为不变。
        self._decide_win(rec)

        # 发言配额按回合重置（C53：quota=per_actor_per_turn）
        if self.state.comms_used:
            self.state.comms_used = {}

        # 指标落库（B27）：本回合全部模型调用，一行一条
        self._persist_calls(t)
        # 回合级回执（B45）：order / skipped / votes
        self._persist_receipt(t, rec)
        rec.version = self.state.version
        self._settle_meta(rec)
        # 技能写的回合标记只对本回合有效（自爆的「跳过当日剩余阶段」不该永久生效）
        self._clear_skill_flags()

    def _active_actors(
        self,
        actors_mode: str = "all",
        trigger: str = "turn",
        groups: list[dict] | None = None,
        window: str = "",
    ) -> list[tuple[str, CharacterCard]]:
        """本阶段有资格行动的人（D6）。

        - `all`（缺省）：所有能动的人——与改造前完全一致；
        - `groups`：只含 `groups` 里声明的组成员（世界包用它表达「只有某一组在行动」）；
        - `skill_holders`：只含「本时机有可用技能」的人（技能阶段用它）。

        判据仍然只看 `can_act`：濒死者算在场（进阵营人数），但这一回合动不了。
        """
        result = []
        for cid, card in self.cards.items():
            rt = self.state.characters.get(cid)
            if rt is None:
                continue
            if not can_act(rt):
                continue
            result.append((cid, card))

        mode = str(actors_mode or "all")
        if mode == "skill_holders":
            return [
                (cid, card)
                for cid, card in result
                if self._available_skills(cid, card, trigger, window)
            ]
        if mode == "groups":
            allowed: set[str] = set()
            for group in list(groups or []):
                allowed |= {cid for cid, _ in self._group_members(result, group)}
            return [(cid, card) for cid, card in result if cid in allowed]
        return result

    # ── 主动技能（A2）────────────────────────────────────────────
    #
    # 世界包只声明「谁有什么技能、什么时机、什么效果」；效果原语封闭在框架
    # （`manifest.skill_effects`）。这里既不知道「预言家」也不知道「女巫」——
    # 只按 `effect` 分派，所以任何世界都能用同一套技能层。

    def _init_skill_charges(self) -> None:
        """按 `skills.yaml` 给每个角色发初始资源（药瓶那类）。

        `setdefault`：读档恢复时保留「已经用掉几瓶」的事实，不会被重置成满瓶。
        世界包没有 `skills.yaml` → 直接返回，存量世界零改动。
        """
        if self.pack is None or not getattr(self.pack, "skills", None):
            return
        for cid, card in self.cards.items():
            rt = self.state.characters.get(cid)
            if rt is None:
                continue
            profession = str(getattr(card.ext, "profession", "") or "")
            charges = dict(getattr(rt, "skill_charges", {}) or {})
            for item in self.pack.skills_for(profession):
                resource = item.get("resource") or {}
                key = str(resource.get("key") or "")
                if not key:
                    continue
                charges.setdefault(key, int(resource.get("charges", 1) or 1))
            if charges:
                rt.skill_charges = charges

    def _name_of(self, cid: str) -> str:
        card = self.cards.get(cid)
        return card.data.name if card is not None else cid

    def _available_skills(
        self, cid: str, card: CharacterCard, trigger: str, window: str = ""
    ) -> list[dict]:
        """该角色此刻能发动的技能：声明有、时机对、窗口对、资源还够。"""
        if self.pack is None:
            return []
        profession = str(getattr(card.ext, "profession", "") or "")
        declared = [
            item
            for item in self.pack.skills_for(profession, trigger)
            # 空 `window` 的技能任何窗口都能用；写了就必须与阶段声明的窗口一致
            if not window or str(item.get("window") or "") in ("", window)
        ]
        if not declared:
            return []

        rt = self.state.characters.get(cid)
        charges = dict(getattr(rt, "skill_charges", {}) or {}) if rt is not None else {}
        out: list[dict] = []
        for item in declared:
            resource = item.get("resource") or {}
            key = str(resource.get("key") or "")
            if key and int(charges.get(key, 0)) <= 0:
                continue
            out.append(item)
        return out

    def _skill_targets(self, cid: str, skill: dict) -> list[str]:
        """技能的合法目标（按声明的 `target`）。"""
        rule = str(skill.get("target") or "other_alive")
        alive = [
            other
            for other, rt in self.state.characters.items()
            if is_alive(rt)
        ]
        if rule == "self":
            return [cid] if cid in alive else []
        if rule == "any_alive":
            return alive
        return [other for other in alive if other != cid]

    def _skill_candidates(self, cid: str, skills: list[dict]) -> dict[str, list[str]]:
        """技能 id → 合法目标；没目标的技能不进候选。"""
        out: dict[str, list[str]] = {}
        for item in skills:
            targets = self._skill_targets(cid, item)
            if targets:
                out[str(item.get("id"))] = targets
        return out

    async def _phase_skill(self, ctx: TurnContext) -> None:
        """主动技能阶段（A2）：结算「回合内触发」的技能（`trigger: turn`）。

        出局触发的技能（`trigger: death`）不走这个阶段——它挂在出局结算上，
        见 `_run_death_skills`。分开是因为前者有固定位置，后者是事件驱动的。
        """
        spec = self._spec_for(ctx, "skill")
        trigger = "turn"
        window = str(spec.param("window", "")) if spec is not None else ""

        actors = self._active_actors("skill_holders", trigger, window=window)
        if not actors:
            return

        hook = getattr(self.llm, "skill_choice", None)
        used_groups: set[str] = set()
        for cid, card in self._order_actors(actors, spec, ctx):
            # 互斥组（例如「同一晚只能用一瓶药」）：本回合已经用过的组不再提供
            skills = [
                item
                for item in self._available_skills(cid, card, trigger, window)
                if str(item.get("exclusive_group") or "") not in used_groups
            ]
            if not skills:
                continue

            choice = None
            candidates = self._skill_candidates(cid, skills)
            if callable(hook) and candidates:
                try:
                    choice = await asyncio.to_thread(
                        hook, cid, card, self.state, ctx.turn, skills, candidates
                    )
                except Exception:  # noqa: BLE001  一个人用不出技能不该拖垮回合
                    choice = None

            payload = await self._apply_skill(ctx, cid, card, skills, choice)
            if payload is None:
                continue
            group = str(payload.pop("_exclusive_group", "") or "")
            if group:
                used_groups.add(group)
            self._notify("skill", payload)

    async def _run_death_skills(self, ctx: TurnContext, cid: str, cause: str) -> None:
        """出局触发的技能（如开枪）。`forbidden_if` 命中死因则不发。"""
        card = self.cards.get(cid)
        if card is None:
            return

        skills = [
            item
            for item in self._available_skills(cid, card, "death")
            if cause not in (item.get("forbidden_if") or [])
        ]
        if not skills:
            return

        hook = getattr(self.llm, "skill_choice", None)
        choice = None
        candidates = self._skill_candidates(cid, skills)
        if callable(hook) and candidates:
            try:
                choice = await asyncio.to_thread(
                    self._call_with_knowledge,
                    hook,
                    self._knowledge_for(ctx, cid, getattr(ctx, "current_spec", None)),
                    cid,
                    card,
                    self.state,
                    ctx.turn,
                    skills,
                    candidates,
                )
            except Exception:  # noqa: BLE001
                choice = None

        payload = await self._apply_skill(ctx, cid, card, skills, choice)
        if payload is not None:
            payload.pop("_exclusive_group", None)
            self._notify("skill", payload)

    async def _apply_skill(
        self,
        ctx: TurnContext,
        cid: str,
        card: CharacterCard,
        skills: list[dict],
        choice,
    ) -> dict | None:
        """结算一次技能选择。返回事件载荷；`None` = 没发动（不扣资源）。"""
        if not isinstance(choice, dict):
            return None

        skill_id = str(choice.get("skill") or "")
        spec = next((item for item in skills if str(item.get("id")) == skill_id), None)
        if spec is None:
            return None

        target = str(choice.get("target") or "")
        if target not in self._skill_targets(cid, spec):
            return None

        effect = str(spec.get("effect") or "")
        rt = self.state.characters.get(cid)

        # 扣资源（世界包声明几个就扣几个；没声明就不扣）
        resource = spec.get("resource") or {}
        key = str(resource.get("key") or "")
        if key and rt is not None:
            charges = dict(rt.skill_charges or {})
            charges[key] = max(0, int(charges.get(key, 0)) - 1)
            rt.skill_charges = charges

        payload = {
            "id": cid,
            "name": card.data.name,
            "skill": skill_id,
            "label": str(spec.get("label") or skill_id),
            "target": target,
            "target_name": self._name_of(target),
            "effect": effect,
            "_exclusive_group": str(spec.get("exclusive_group") or ""),
        }

        if effect == "reveal_camp":
            target_rt = self.state.characters.get(target)
            camp = str(getattr(target_rt, "camp", "") or "")
            note = f"第 {ctx.turn} 回合：{self._name_of(target)} 的阵营是 {camp or '不明'}"
            if rt is not None:
                rt.notes = [*rt.notes, note]
            # 私有信息只走「本人」通道，不进公共 briefs
            ctx.consensus.setdefault(cid, []).append(f"【{payload['label']}】{note}")
            payload["result"] = note
        elif effect == "heal":
            if target not in ctx.healed:
                ctx.healed.append(target)
            note = f"第 {ctx.turn} 回合：你救下了 {self._name_of(target)}"
            if rt is not None:
                rt.notes = [*rt.notes, note]
            payload["result"] = note
        elif effect == "poison":
            # 标准规则：被毒出局不留遗言、也不触发出局技能（不能开枪）
            if self._mark_dead(ctx, target, "poison"):
                payload["result"] = f"{self._name_of(target)} 中毒出局"
            else:
                return None
        elif effect == "shoot":
            if self._mark_dead(ctx, target, "shoot"):
                payload["result"] = f"{self._name_of(target)} 被带走"
                await self._say_last_words(ctx, target, "shoot")
            else:
                return None
        elif effect == "self_eliminate":
            # 自己出局，并写一个「回合标记」：世界包再用 `break_if: flag_set` 跳过当日
            # 剩余阶段（自爆直接入夜那类）。标记由框架在回合结束时自动清掉。
            if not self._mark_dead(ctx, cid, "self_destruct"):
                return None
            payload["result"] = f"{card.data.name} 自己出局"
            flag = str(spec.get("flag") or "")
            if flag:
                self._set_skill_flag(flag)
        else:
            return None

        # 事件可见范围（C21）：技能动作本身不是全都公开的。
        # - 查验 / 救人 / 毒杀发生在私下，若全量推送，等于当众宣布「谁是神职、
        #   谁验了谁、谁被毒了」——信息对抗当场作废；
        # - 开枪与自爆是**公开**事件（枪声、翻牌自爆），保持广播。
        if effect in ("reveal_camp", "heal", "poison"):
            payload["audience"] = [cid]

        return payload

    def _set_skill_flag(self, flag: str) -> None:
        """写一个「技能产生的回合标记」，并记账以便回合结束时清掉。

        为什么用 `party.flags`：它已经是状态里的字符串标记位，`flag_set` 谓词也读它；
        新增一个只为自爆存在的字段不划算（五点契约的成本高于收益）。
        """
        name = str(flag or "")
        if not name:
            return
        if name not in self.state.party.flags:
            self.state.party.flags.append(name)
        self._skill_flags.add(name)

    def _clear_skill_flags(self) -> None:
        """回合结束清掉技能写的标记——它们是「本回合的事」，不是永久状态。"""
        if not self._skill_flags:
            return
        self.state.party.flags = [
            item for item in self.state.party.flags if item not in self._skill_flags
        ]
        self._skill_flags.clear()

    def _mark_dead(self, ctx: TurnContext, cid: str, cause: str) -> bool:
        """把一个人直接置为出局并记账（技能用）。已经是死的返回 False。"""
        rt = self.state.characters.get(cid)
        if rt is None or rt.position == Position.dead:
            return False
        rt.position = Position.dead
        ctx.rec.deaths.append(
                {"id": cid, "cause": cause, "cause_label": self._death_label(cause)}
            )
        return True

    def _responders_for(self, turn: int) -> list[str]:
        """本回合该强制到场的 authority NPC（区域秩序 + 近期冲突 + 张力）。"""
        from ..rules import immersion

        meta = (getattr(self.pack, "region_meta", {}) or {}).get(self.state.scene.region) or {}
        cfg = (getattr(self.rules, "immersion", {}) or {}).get("authority_response") or {}
        return immersion.responders(
            turn=turn,
            combat_turns=self._combat_turns,
            tension=self.state.tension,
            order=str(meta.get("order") or "none"),
            authority=list(meta.get("authority") or []),
            cfg=cfg,
            last_response_turn=self._last_response_turn,
        )

    def _attitude_score(self, action) -> int:
        """社交判定的态度分（B2）：势力基础态度 + 声望折算。

        合成放编排层，是因为 M02（L1）不许反向依赖 M05（L2）——
        世界包只提供查询，判定层只接收一个整数。
        """
        if self.pack is None:
            return 0
        faction = self.pack.social_faction(
            str(getattr(action, "target", "") or ""), self.state.scene.region
        )
        if not faction:
            return 0
        base = self.pack.faction_attitude(faction)
        label = self.pack.faction_label(faction)
        rep = int((self.state.party.reputation or {}).get(label) or 0)
        step = self.rules.attitude_reputation_step
        # 声望按档折算（截断取整，不四舍五入）：攒得越多越能把敌对拉回中立
        return base + int(rep / step)

    def _guard_scene(self, prev_loc: str, rec) -> None:
        """空间校验 + 出口重算。

        - 目标不在当前出口里 → 回退并记 `rejected`（不再允许瞬移）
        - 每次位置落定后，按地图重算 `scene.exits`（GM 已无权写这个字段）
        """
        from ..models import Exit as _Exit
        
        cur = self.state.scene.location_id
        rmap = self.region_map
        if rmap:
            if cur != prev_loc and not is_adjacent(rmap, prev_loc, cur):
                self.state.scene.location_id = prev_loc
                rec.rejected.append(f"move: {prev_loc} → {cur} 不相邻，已拒绝")
                cur = prev_loc
            self.state.scene.exits = [Exit(**e) for e in exits_for(rmap, cur)]

    def _flush_vectors(self, turn: int) -> None:
        """回合末把本回合的记忆一次性写进向量库。

        以前是每个角色每回合单独一次 embedding HTTP（约 405ms）串在回合热路径上；
        合并后每回合只剩 1 次调用（实测 16 条 0.66s）。
        """
        if self.vectors is None or not self._pending_vectors:
            self._pending_vectors = {}
            return
        groups = self._pending_vectors
        self._pending_vectors = {}
        try:
            writer = getattr(self.vectors, "add_grouped", None)
            if callable(writer):
                writer(groups, turn=turn, kind="event")
            else:  # 后端没实现批量：逐桶写，行为不变
                for key, texts in groups.items():
                    self.vectors.add_many(key, texts, turn=turn, kind="event")
        except Exception as exc:  # noqa: BLE001
            # 不吞：失败要能在 trace 里看见（以前 pass，表现为「想不起来」）
            self.memory_errors.append(
                f"turn{turn} 批量写入失败 {type(exc).__name__}: {exc}"[:160]
            )
            self.memory_errors = self.memory_errors[-20:]

    def _record_event(self, cid: str, action, result, turn: int, day: int, hour: int) -> None:
        """把本回合的经历写进该角色的记忆（结构化，供后续回忆）。"""
        if self.events is None:
            return
        from ..models.memory import IMPORTANCE_BY_OUTCOME, CharacterEvent

        skill = action.skill or "行动"
        # 记忆正文以「做了什么」开头：模板前缀（第N回合在X：技能·结果：）会主导向量相似度，
        # 把同地点不同事的差异压平——前缀信息本来就有结构化列，不需要写进正文。
        body = (action.action or "").strip()[:60]
        summary = f"{body}（{skill}·{result.outcome}）" if body else f"{skill}·{result.outcome}"
        entities = [self.state.scene.location_id]
        if getattr(action, "target", None):
            entities.append(str(action.target))

        self.events.add(
            CharacterEvent(
                session_id=self.state.session_id,
                char_id=cid,
                turn=turn,
                day=day,
                hour=hour,
                kind="action",
                summary=summary,
                entities=entities,
                importance=IMPORTANCE_BY_OUTCOME.get(result.outcome, 5),
            )
        )

        # 同时写入向量库：字面相似度之外，为"情境联想"留通道
        if self.vectors is not None:
            # 先进待写队列：回合末合并成**一次** embedding 调用
            self._pending_vectors.setdefault(cid, []).append(summary)

    async def _declare_one(
        self,
        cid: str,
        card: CharacterCard,
        turn: int,
        briefs: list[str],
        knowledge: str = "",
        registry: dict | None = None,
    ) -> PlayerAction:
        """DECLARE 阶段取一个角色的行动：人类席位优先，超时/弃权回落 LLM 代打。

        **人类与 AI 走同一个提交入口**（S4-1 / E-1）：两者都过 `submit_action()`，
        只是来源标记不同（`human` / `ai` / `ghost`）。
        """
        from .actions import submit_action

        if self.human is not None and self.human.is_human(cid):
            # 图路径（`interrupt`）已经收上来的提交：直接用，不再等一次
            preloaded = self.human.take_preloaded(cid)
            if preloaded is not None and not preloaded.get("ghost"):
                self._notify("human_done", {"id": cid, "ghost": False})
                return submit_action(
                    cid, preloaded, source="human", turn=turn, registry=registry
                ).action
            skills = [
                name
                for name, _ in sorted(
                    (card.ext.skills or {}).items(), key=lambda kv: -kv[1]
                )[:6]
            ]
            timeout = float(getattr(self.human, "timeout", 120.0) or 120.0)
            self._notify(
                "human_wait",
                {
                    "id": cid,
                    "name": card.name,
                    "turn": turn,
                    "skills": skills,
                    "timeout_ms": int(timeout * 1000),
                },
            )
            raw = await self.human.wait_action(cid, card.name, turn, list(briefs), skills)
            if raw is not None and not raw.get("ghost"):
                self._notify("human_done", {"id": cid, "ghost": False})
                return submit_action(
                    cid, raw, source="human", turn=turn, registry=registry
                ).action
            # 超时或显式交还：LLM 代打，本回合标记 ghost
            self._notify("human_done", {"id": cid, "ghost": True})

        ai_action = await asyncio.to_thread(
            self._call_with_knowledge,
            self.llm.player_action,
            knowledge,
            cid,
            card,
            self.state,
            turn,
            list(briefs),
        )
        return submit_action(
            cid, ai_action, source="ai", turn=turn, registry=registry
        ).action

    def _capabilities(self):
        """本局能力集合；没有 pack（离线/单测）时返回 None = 按现状全开。"""
        if getattr(self, "pack", None) is None:
            return None
        try:
            from ..rulespack import assemble

            return assemble(self.pack)
        except Exception:  # noqa: BLE001
            return None

    def _spec_for(self, ctx, step_name: str):
        """当前正在执行的阶段规格；没有就回落到该执行体的第一个声明。

        为什么不能直接用 `ctx.step_specs[step]`：它只保留**第一个**同 kind 的声明。
        一个动作类阶段在一个回合里可能出现两次（例如「分组私密行动」与「公开轮流发言」），
        用错就会让后一段沿用前一段的 `groups`（共议）配置——第二个行动阶段会莫名再投一次票。
        """
        from .phases import KIND_TO_STEP

        current = getattr(ctx, "current_spec", None)
        if current is not None and KIND_TO_STEP.get(current.kind) == step_name:
            return current
        return ctx.step_specs.get(step_name)

    def _judge_enabled(self) -> bool:
        """`systems.judge` 是否开启；没有 pack 时视为开启（与改造前一致）。"""
        caps = self._capabilities()
        return True if caps is None else caps.has("judge")

    def _turn_specs(self, rec: TurnRecord, turn: int) -> tuple[tuple, dict]:
        """解析本回合的阶段声明；解析失败退回 standard preset。

        **不在这里求值 `when` / `break_if`**：它们依赖回合执行中的状态
        （例如 check 阶段判出胜负后 `win_decided` 才为真），必须逐阶段运行时求值。
        声明写坏了不允许把整局卡死：记一条原因后按 standard 继续跑。
        """
        from .pipeline import PipelineError, parse_turn_flow

        declaration = (
            self.pack.turn_flow_declaration() if self.pack is not None else {}
        )
        try:
            phases = parse_turn_flow(declaration)
        except PipelineError as exc:
            rec.skipped.append(
                {"id": "*", "kind": "*", "reason": f"turn_flow: {exc}"}
            )
            phases = parse_turn_flow({})
        return phases, _step_specs_from(phases)

    def _announce(self, ctx, spec) -> None:
        """把世界包写在 `phases[].banner` 的台词当「主持人播报」发出去。

        文案全在世界包（"天黑请闭眼""狼人请睁眼"这类），框架只负责说出来——
        写进代码会被 `check_generic` 拦（框架里不许出现世界词汇）。
        """
        text = str(getattr(spec, "banner", "") or "").strip()
        if not text:
            return
        self._notify(
            "banner",
            {
                "id": str(getattr(spec, "id", "") or ""),
                "kind": str(getattr(spec, "kind", "") or ""),
                "text": text,
                "turn": int(getattr(ctx, "turn", 0) or 0),
            },
        )




    def _death_label(self, cause: str) -> str:
        """死因的显示文案（世界包给，框架不写死）。

        原因码是**内部标识**（`consensus_result` 之类），直接显示给玩家很难懂；
        显示文案属于世界内容（`world.yaml: death_labels`）。
        """
        labels = dict(getattr(getattr(self, "pack", None), "death_labels", {}) or {})
        return str(labels.get(str(cause or ""), "") or "")

    def _call_with_knowledge(self, fn, knowledge: str, *args):
        """调用 agent 钩子；**只有签名接受 knowledge 时才传**。

        为什么要有这个探测：LLM 实现是开放的（mock、测试替身、第三方 provider），
        多塞一个位置参数会让它们直接 TypeError——「给老实现加可选参数」不能破坏兼容。
        """
        if not knowledge:
            return fn(*args)
        try:
            import inspect

            if "knowledge" in inspect.signature(fn).parameters:
                return fn(*args, knowledge=knowledge)
        except (TypeError, ValueError):
            pass
        return fn(*args)

    def _knowledge_for(self, ctx: TurnContext, actor_id: str, spec=None) -> str:
        """这个角色此刻的知识视图（提示词段）。

        世界包没声明知识视图 → 返回空串 → 提示词与改造前**逐字节一致**
        （`ironvale` / `longzu` 因此零变化）。
        """
        pack = getattr(self, "pack", None)
        if pack is None:
            return ""
        declaration = dict(getattr(pack, "knowledge", None) or {})
        if not declaration or not declaration.get("enabled"):
            return ""

        from .knowledge import build_knowledge

        phase_id = str(getattr(spec, "id", "") or "")
        groups = declaration.get("phases") or {}
        phase = ""
        if phase_id in set(groups.get("hidden") or []):
            phase = "hidden"
        elif phase_id in set(groups.get("open") or []):
            phase = "open"

        votes: dict[str, int] = {}
        for vote in getattr(ctx.rec, "votes", None) or []:
            target = str(getattr(vote, "target", "") or "")
            if not target:
                continue
            name = self._name_of(target)
            votes[name] = votes.get(name, 0) + 1

        speeches = [str(item) for item in (getattr(ctx, "briefs", None) or [])][-6:]
        turn = int(getattr(ctx, "turn", 0) or 0)
        # 「一个公开期算几个回合」——周期长度由世界包声明（缺省 2）
        stride = int(declaration.get("cycle_stride") or 2)
        knowledge = build_knowledge(
            self.state,
            self.cards,
            pack,
            actor_id,
            phase=phase,
            stage=str(getattr(spec, "banner", "") or ""),
            votes=votes,
            speeches=speeches,
            turn=turn,
            cycle=(turn // stride) + 1 if phase == "hidden" else 0,
        )
        return knowledge.render()

    async def _run_phases(self, phases, ctx) -> None:
        """按声明顺序执行阶段：`when` 不命中就跳过，`break_if` 命中则后续全跳。

        一个执行体可能承接多个声明阶段（`declare` + `resolve` 都是 `action`），
        同一个执行体一回合只跑一次——它们本来就是同一段逻辑。
        """
        from .phases import KIND_TO_STEP
        from .phases.record import record_skip, reason_break, reason_when

        rec = ctx.rec
        # **相邻**同类才合并：`declare` + `resolve` 是同一行动的两半（必须只跑一次），
        # 而两个不相邻的 `action`（例如夜里的行动与白天的发言）是两个独立阶段，各跑一次。
        # 用集合去重会把第二个静默吃掉——这正是狼人杀白天发言阶段长期没执行的原因。
        prev_step: str | None = None
        broken: str | None = None

        for spec in phases:
            if broken is not None:
                record_skip(rec, spec, reason_break(broken))
                continue

            if not self._when_holds(spec.when, ctx, spec):
                record_skip(rec, spec, reason_when(spec.when))
                continue

            # 主持人播报（`phases[].banner`）：每个声明阶段各发一条，
            # 不受「相邻同类合并」影响——台词是给玩家看的，不是执行体的。
            self._announce(ctx, spec)

            step_name = KIND_TO_STEP.get(spec.kind)
            if step_name and step_name != prev_step:
                # 执行体读 `current_spec`：同类多阶段时才知道自己现在跑的是哪一段
                ctx.current_spec = spec
                await getattr(self, f"_phase_{step_name}")(ctx)
                prev_step = step_name

            rec.order.append(spec.id)

            if spec.break_if and self._when_holds(spec.break_if, ctx, spec):
                broken = spec.break_if

    def _when_holds(self, name: str, ctx, spec=None) -> bool:
        """求值一个谓词。未知谓词按「执行」处理——不静默少跑阶段。"""
        from ..rulespack.predicates import PredicateContext, PredicateError, evaluate

        clock_cfg = getattr(self.rules, "clock", None) or {}
        phases_cfg = clock_cfg.get("phases", {}) if isinstance(clock_cfg, dict) else {}
        try:
            return evaluate(
                name,
                PredicateContext(
                    state=self.state,
                    turn=ctx.turn,
                    # 谓词参数（`flag` / `n` / `stage` / `quest` / `tag` …）来自阶段的
                    # `with:` 块。以前**没有**传：`flag_set` 这类带参谓词永远拿不到参数，
                    # 求值抛 PredicateError → 被下面的 `return True` 吞成「条件成立」，
                    # 于是「写了参数却没生效」。
                    params=dict(getattr(spec, "with_params", {}) or {}) if spec is not None else {},
                    phases=phases_cfg,
                    human_seat=getattr(self, "human", None) is not None,
                ),
            )
        except PredicateError:
            return True

    def _persist_receipt(self, turn: int, rec: TurnRecord) -> None:
        """回合级回执落库（B45）。失败不影响回合，只留一条原因。"""
        if self.conn is None:
            return
        try:
            from ..storage.turn import save_receipt

            save_receipt(self.conn, self.state.session_id, turn, rec)
        except Exception as exc:  # noqa: BLE001
            rec.skipped.append(
                {"id": "*", "kind": "*", "reason": f"回执落库失败：{type(exc).__name__}"}
            )

    def _order_actors(self, actors: list[tuple[str, CharacterCard]], spec=None, ctx=None):
        """按声明决定行动顺序（C48）。缺省 `dex` = 与改造前完全一致。

        - `fixed`：按 `fixed_sequence`（座位表）里的 id 顺序，未列出的排在最后；
          可再加 `start_after: last_death` 与 `direction`——这就是标准回合制里
          「发言从死者下一位开始、单/双日方向相反」的落地方式。
        - `dex`：按敏捷值（缺省，与改造前完全一致）。
        - 并发/自由顺序（`simultaneous` / `free`）与轮转（`rotating`）尚未实现，
          **已从清单里移除**：宁可报「不认识这个取值」，也不静默退化成 dex。
        """
        order = str(spec.param("order", "dex")) if spec is not None else "dex"
        if order != "fixed":
            return self._order_by_dex(actors)

        # 座位表：世界包显式给了就用它；没给就按**角色 id 升序**（建卡顺序 = 入座顺序）。
        # 不强制世界包硬编码 id——同一个包在 API（chr_pN）/ CLI / 单测（chr_N）里都能跑。
        sequence = [str(cid) for cid in (spec.param("fixed_sequence") or [])]
        if not sequence:
            sequence = sorted({cid for cid, _ in actors})
        start_after = str(spec.param("start_after", "none")) if spec is not None else "none"
        direction = str(spec.param("direction", "forward")) if spec is not None else "forward"
        if sequence and start_after == "last_death":
            sequence = self._rotate_from_last_death(sequence, direction, ctx)

        position = {cid: index for index, cid in enumerate(sequence)}
        return sorted(
            actors, key=lambda item: (position.get(item[0], 10**6), item[0])
        )

    def _rotate_from_last_death(self, sequence: list[str], direction: str, ctx) -> list[str]:
        """把座位表旋转到「本回合最后一名出局者的下一位」。

        三条口径（都不掷骰，保证同 seed 可复现）：

        - 本回合没人出局 → 从首位开始；
        - 死者不在座位表里（NPC / 未知 id）→ 同样从首位开始，**不猜**；
        - `direction: by_day_parity` → 单数天正向、双数天反向（用 `clock.day`，
          不用回合号：回合号与「第几天」在世界包之间不等价）。
        """
        dead = ""
        if ctx is not None and ctx.rec.deaths:
            last = ctx.rec.deaths[-1]
            dead = str(last.get("id") or last.get("char_id") or "")
        if not dead or dead not in sequence:
            return list(sequence)

        backwards = direction == "reverse" or (
            direction == "by_day_parity" and int(self.state.clock.day) % 2 == 0
        )
        total = len(sequence)
        index = sequence.index(dead)
        step = -1 if backwards else 1
        start = (index + step) % total
        if step == 1:
            return sequence[start:] + sequence[:start]
        return [sequence[(start - offset) % total] for offset in range(total)]

    def _order_by_dex(self, actors: list[tuple[str, CharacterCard]]) -> list[tuple[str, CharacterCard]]:
        """先手由 dex 决定；平手按 chr_id 保证可复现。"""
        return sorted(
            actors,
            key=lambda item: (-item[1].ext.stats.get("dex", 10), item[0]),
        )

    def _settle_meta(self, rec: TurnRecord) -> None:
        """把本回合的局外事件按世界包价目表发点（金额由世界包决定）。"""
        events = list(self.meta_events)
        self.meta_events = []
        if not events or self.conn is None:
            return
        try:
            from ..growth import earn as meta_earn
            from ..growth.wallet import Wallet

            detail = meta_earn.apply_events(
                self.meta_hooks,
                Wallet(self.conn),
                events,
                session_id=self.state.session_id,
                world_id=self.meta_hooks.world_id,
            )
            if detail:
                rec.earned = detail
        except Exception:  # noqa: BLE001
            pass

    def _sync_paths(self, rec: TurnRecord) -> None:
        """身份变更落到卡上：grants 生效，卡上的 `path` 跟随（卡存初始，运行时存当前）。

        以「卡上的 path 与运行时不同」为唯一触发条件，因此重复 set 同一身份不会二次加成。
        """
        for cid, rt in self.state.characters.items():
            card = self.cards.get(cid)
            if card is None or not rt.path or card.ext.path == rt.path:
                continue
            applied = (
                identity.apply_grants(self.pack, card, rt, rt.path) if self.pack else {}
            )
            card.ext.path = rt.path
            rec.path_changes.append(
                {
                    "char_id": cid,
                    "path": rt.path,
                    "label": self.pack.path_label(rt.path) if self.pack else rt.path,
                    "grants": applied,
                }
            )
            self.meta_events.append("identity_granted")

    DYING = "dying"

    def _resolve_death(self, cid: str, turn: int, rec: TurnRecord | None = None) -> None:
        """hp 归 0 → DYING（宽限 2 回合可救）；超时 → DEAD，此后不再入场。

        濒死用 `conditions` 里的 `dying` 表达，位置仍是 `scene`（人还在场上，只是不能动），
        死亡才把 `position` 改成 `dead` —— 这样"救人"只需求把 hp / 移除 dying，不用改位置。
        """
        rt = self.state.characters.get(cid)
        if rt is None:
            return

        if rt.hp <= 0:
            if self.DYING not in rt.conditions:
                rt.conditions.append(self.DYING)
            if rt.dying_since <= 0:
                rt.dying_since = max(1, turn)
            if (
                rt.position != Position.dead
                and turn - rt.dying_since >= self.rules.death_grace_turns
            ):
                rt.position = Position.dead
                rt.conditions = [c for c in rt.conditions if c != self.DYING]
                if rec is not None:
                    rec.deaths.append({"char_id": cid, "turn": turn, "kind": "dead"})
                self.meta_events.append("death")
            return

        # 救回来了
        if self.DYING in rt.conditions:
            rt.conditions = [c for c in rt.conditions if c != self.DYING]
        rt.dying_since = 0

    def _apply_loot(self, raw, rec: TurnRecord, turn: int) -> None:
        """GM 申报「拿到什么」→ 代码掷「多强」（知识库 02.2 §十四）。

        模型只给名字/slot/tag/大概档位；tier、成色、属性、价格全部由 `rules/equipment.yaml` 决定。
        """
        if not isinstance(raw, list) or not raw:
            return
        eqcfg = self.rules.equipment or {}
        vocab = set(eqcfg.get("tag_vocab") or [])
        era = self.pack.era(self.state.world.era) if self.pack is not None else None
        alive = self._active_actors()
        if not alive:
            return

        changes: list[DeltaChange] = []
        for idx, item in enumerate(raw[:4]):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            owner = str(item.get("owner") or item.get("owner_id") or "")
            card = self.cards.get(owner)
            if card is None:
                owner, card = alive[0]
            tags = [str(t) for t in (item.get("tags") or []) if str(t) in vocab][:3]
            try:
                tier_hint = int(item.get("tier_hint")) if item.get("tier_hint") is not None else None
            except (TypeError, ValueError):
                tier_hint = None

            inst = eq_mod.roll_loot(
                name=name,
                eqcfg=eqcfg,
                rng=derive(self.seed, "loot", owner, turn, idx),
                era_cfg=era,
                stage=self.state.stage,
                tension=self.state.tension,
                slot=str(item.get("slot") or "trinket"),
                tags=tags,
                tier_hint=tier_hint,
                owner_id=owner,
                session_id=self.state.session_id,
                turn=turn,
            )
            card.ext.equipment.append(inst)
            changes.append(
                DeltaChange(
                    path=f"characters.{owner}.inventory",
                    op=DeltaOp.add,
                    value=inst.id,
                )
            )
            rec.loot.append(
                {
                    "owner": owner,
                    "id": inst.id,
                    "name": inst.name_flavor,
                    "tier": inst.tier,
                    "quality": inst.quality.value,
                    "attrs": inst.attrs,
                    "tags": inst.tags,
                }
            )

        if not changes:
            return
        try:
            self.state = apply_delta(
                self.state,
                StateDelta(turn=turn, changes=changes),
                self.cards,
                self.rules.clock,
                self.pack,
            )
        except DeltaValidationError as exc:
            rec.rejected.append("loot: " + "; ".join(exc.errors[:2]))

    def _apply_enemies(self, raw, rec: TurnRecord, turn: int) -> None:
        """GM 申报场上出现谁。已有 id 只更新描述，不覆盖 hp（血量只能由战斗结算改）。"""
        if not isinstance(raw, list) or not raw:
            return
        merged = {eid: enemy.model_copy() for eid, enemy in self.state.enemies.items()}
        changed = False
        for item in raw[:4]:
            if not isinstance(item, dict):
                continue
            eid = str(item.get("id") or "").strip()
            if not eid:
                continue
            tags = [str(t) for t in (item.get("tags") or [])][:3]
            if eid in merged:
                merged[eid].name = str(item.get("name") or merged[eid].name)
                if tags:
                    merged[eid].tags = tags
                changed = True
                continue
            enemy = combat_mod.enemy_from_declaration(
                {**item, "id": eid, "tags": tags},
                self.rules.combat,
                self.state.stage,
            )
            if enemy is None:
                continue
            merged[eid] = enemy
            changed = True
            rec.combat.append(
                {
                    "kind": "spawn",
                    "enemy": eid,
                    "name": enemy.name,
                    "hp": enemy.hp,
                    "atk": enemy.atk,
                    "def": enemy.defense,
                    "threat": str(item.get("threat") or "normal"),
                }
            )
        if changed:
            self._set_enemies(merged, rec, turn)

    def _set_enemies(self, merged: dict, rec: TurnRecord, turn: int) -> None:
        try:
            self.state = apply_delta(
                self.state,
                StateDelta(
                    turn=turn,
                    changes=[
                        DeltaChange(
                            path="enemies",
                            op=DeltaOp.set,
                            # 直接放 Enemy 对象：放 dict 会被 pydantic 当"类型不符"警报
                            value={eid: enemy.model_copy(deep=True) for eid, enemy in merged.items()},
                        )
                    ],
                ),
                self.cards,
                self.rules.clock,
                self.pack,
            )
        except DeltaValidationError as exc:
            rec.rejected.append("enemies: " + "; ".join(exc.errors[:2]))

    def _resolve_combat(
        self,
        rec: TurnRecord,
        turn: int,
        actors: list[tuple[str, CharacterCard]],
        actions: list,
        results: list,
    ) -> None:
        """玩家战斗意图 → 敌人 hp；然后敌人反击 → 玩家 hp。全部走 apply_delta。"""
        # 只攻击却没填 target、场上又只有一个敌人时，默认指向它：
        # 否则这一刀会判成「目标不存在」，玩家/人类都会觉得莫名其妙。
        if len(self.state.enemies) == 1:
            only = next(iter(self.state.enemies))
            for i, act in enumerate(actions):
                if act.mode == CombatMode.attack and not act.target:
                    actions[i] = act.model_copy(update={"target": only})

        merged = {eid: enemy.model_copy() for eid, enemy in self.state.enemies.items()}
        rows: list[dict] = []
        fled: set[str] = set()
        defending: set[str] = set()
        enemy_changed = False

        for (cid, card), action, result in zip(actors, actions, results):
            row = combat_mod.resolve(
                action, card, result, self.state, self.dice, self.rules.combat
            )
            if row is None:
                continue
            row["char_id"] = cid
            rows.append(row)
            if row["kind"] == "defend":
                defending.add(cid)
            elif row["kind"] == "flee" and row.get("escaped"):
                fled.add(cid)
            elif row["kind"] == "attack" and row.get("hit"):
                enemy = merged.get(str(row.get("target") or ""))
                if enemy is not None:
                    enemy.hp = int(row.get("enemy_hp") or 0)
                    enemy_changed = True
                    if enemy.hp <= 0:
                        merged.pop(enemy.id, None)
                        row["killed"] = True
                        self.meta_events.append("combat_won")

        # 敌人反击：每个敌人打一个还站着、没逃的角色（用共享骰保证可复现）
        if merged:
            alive = [(cid, card) for cid, card in actors if cid not in fled]
            for eid, enemy in list(merged.items()):
                if not alive:
                    break
                # 只有一个活人时不必掷骰（骰子最小面数是 2）
                pick = 0 if len(alive) == 1 else self.dice.d(len(alive)) - 1
                cid, card = alive[pick]
                strike = combat_mod.enemy_strike(
                    enemy, card, self.dice, defending=cid in defending, rules=self.rules.combat
                )
                strike["char_id"] = cid
                rows.append(strike)

        # 超杀截断到 0：hp 不允许为负，剩余血量按顺序分配给每次打击
        left = {
            cid: max(0, (self.state.characters.get(cid).hp if cid in self.state.characters else 0))
            for cid in {row["char_id"] for row in rows if row["kind"] == "enemy_strike"}
        }
        changes: list[DeltaChange] = []
        for row in rows:
            if row["kind"] != "enemy_strike":
                continue
            cid = row["char_id"]
            real = max(0, min(int(row.get("damage") or 0), left.get(cid, 0)))
            row["damage"] = real
            left[cid] = left.get(cid, 0) - real
            if real > 0:
                changes.append(
                    DeltaChange(path=f"characters.{cid}.hp", op=DeltaOp.sub, value=real)
                )
        if changes:
            try:
                # 战斗伤害不受"单回合 60%"限制：一刀砍掉半管血是正常的
                self.state = apply_delta(
                    self.state,
                    StateDelta(turn=turn, changes=changes),
                    self.cards,
                    self.rules.clock,
                    self.pack,
                    hp_cap=None,
                )
            except DeltaValidationError as exc:
                rec.rejected.append("combat: " + "; ".join(exc.errors[:3]))

        if enemy_changed:
            self._set_enemies(merged, rec, turn)

        if rows:
            rec.combat.extend(rows)

    def _ensure_opening(self) -> None:
        """开局主线只生成一次（Director 的 open_session 幂等化）。"""
        if self.director is None or self._opened:
            return
        self._opened = True
        try:
            quests = self.director.open_session(self.state, self.cards)
        except Exception:  # noqa: BLE001
            return
        if not quests:
            return
        try:
            self.state = apply_delta(
                self.state,
                StateDelta(
                    turn=0,
                    changes=[
                        DeltaChange(
                            path="quests",
                            op=DeltaOp.set,
                            # 放 Quest 对象：放 dict 会触发 pydantic 类型警告
                            value=[Quest(**q) if isinstance(q, dict) else q for q in quests],
                        )
                    ],
                ),
                self.cards,
                self.rules.clock,
                self.pack,
            )
        except DeltaValidationError:
            pass

    def _force_seed(self, seed: dict) -> None:
        """把 Director 指定的固定事件塞给 GM（模型无法篡改时机）。"""
        hook = getattr(self.llm, "force_seed", None)
        if callable(hook):
            try:
                hook(seed)
            except Exception:  # noqa: BLE001
                pass

    def _maybe_advance_stage(self, turn: int, force: bool = False) -> None:
        if (force or turn % 10 == 0) and self.state.stage < 4:
            self.state = apply_delta(
                self.state,
                StateDelta(
                    turn=turn,
                    changes=[DeltaChange(path="stage", op=DeltaOp.add, value=1)],
                ),
                self.cards,
                self.rules.clock,
            )

    # ── 落库 ────────────────────────────────────────────────
    def _persist(self, turn: int, cid: str, action, result, out: dict, scene: dict) -> None:
        if self.conn is None:
            return
        import json as _json

        sid = self.state.session_id
        options = normalize_options(scene.get("options") or scene.get("hooks"))
        self.store.save_turn(
            self.conn,
            sid,
            turn,
            cid,
            action.thought,
            action.action,
            out.get("narration", ""),
            _json.dumps(options, ensure_ascii=False),
        )
        self.store.save_dice(self.conn, sid, turn, result)
        self.store.save_state(self.conn, self.state)
        # S3-1：状态迁移日志与状态快照**同点**写入（回滚 = 快照 + 日志重放）。
        # 只记公开信息：行动 / 判定 / 叙事摘要 / 状态版本；私密心声不进日志。
        try:
            from ..storage.trace import save_transition

            save_transition(
                self.conn,
                sid,
                turn,
                "action",
                {
                    "action": str(getattr(action, "action", "") or "")[:200],
                    "outcome": str(getattr(result, "outcome", "") or ""),
                    "total": int(getattr(result, "total", 0) or 0),
                    "dc": int(getattr(result, "dc", 0) or 0),
                    "narration": str(out.get("narration", "") or "")[:200],
                    "state_version": int(getattr(self.state, "version", 0) or 0),
                },
                actor_id=str(cid),
            )
        except Exception:  # noqa: BLE001
            # 迁移日志写失败**不影响对局**（它是可观测资产，不是状态）
            pass
        self.store.commit(self.conn)

    def _persist_summary(self, text: str, turn: int) -> None:
        """章节摘要落库（B25）。失败只记 `memory_errors`，不影响本回合。"""
        if self.conn is None:
            return
        try:
            self.store.save_summary(self.conn, self.state.session_id, int(turn), str(text))
            self.store.commit(self.conn)
        except Exception as exc:  # noqa: BLE001
            self.memory_errors.append(
                f"第{turn}回合摘要落库失败 {type(exc).__name__}: {exc}"[:160]
            )
            self.memory_errors = self.memory_errors[-20:]

    def _persist_calls(self, turn: int) -> None:
        """把本回合的模型调用一次性落库（B27）。

        记账点在 `AgentBase._chat_json`（唯一出口），这里只负责 drain + 写 `agent_calls`：
        一行 = 一次真实调用，`agent` 是角色（gm/npc/summary/ending），`task` 是任务
        （scene/narrate/player/summary/ending）。不在这里记账就一定会漏。
        """
        if self.conn is None:
            return
        drain = getattr(self.llm, "drain_calls", None)
        if not callable(drain):
            return
        records = drain() or []
        if not records:
            return
        sid = self.state.session_id
        for item in records:
            self.store.save_agent_call(
                self.conn,
                sid,
                turn,
                str(item.get("agent") or "gm"),
                str(item.get("model") or getattr(self.llm, "model", "unknown")),
                {"task": item.get("task")},
                {"tokens_out": item.get("tokens_out")},
                tokens_in=int(item.get("tokens_in") or 0),
                tokens_out=int(item.get("tokens_out") or 0),
                latency_ms=int(item.get("latency_ms") or 0),
                retry=int(item.get("retry") or 0),
                task=str(item.get("task") or ""),
            )
        self.store.commit(self.conn)
