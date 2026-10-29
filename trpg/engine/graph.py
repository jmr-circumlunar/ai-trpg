"""LangGraph 编排层：把「世界包声明的阶段」编译成 StateGraph。

## 为什么用图（而不是 for 循环）

手写路径（`loop.step()`）的调度是 `for spec in phases`，`when` / `break_if` 是循环里的
`if`。这在「跑一次」时没问题，但三件事会越来越难：

| 需求 | for 循环 | StateGraph |
|---|---|---|
| 存档 / 回放某一回合的中间态 | 自己做快照 | **checkpointer** |
| 人类接管后从中断处继续 | 自己造 Future（`HumanSeat`） | **`interrupt()` + `Command(resume=...)`** |
| 看清「这一回合走了哪条路」 | 打日志 | **图本身可导出/可视化** |

## 两条路径必须等价

`loop.step()`（for 循环）保留为默认路径，`run_turn()`（图）是并行实现。
两者共用 `GameLoop._open_turn()` 与同一批 `_phase_*` 执行体，差别只在**调度机制**：

- `when` 不命中 → 节点内部跳过（记 `skipped`，不写 `order`）
- `break_if` 命中 → **条件边**路由到 `settle`，后续阶段全部记 `break_if` 跳过

L1 阶段的验收标准就是「两条路径的 `TurnRecord` 逐字段相同」，
见 `tests/test_graph_engine.py`。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .phases import KIND_TO_STEP

# 图里传的东西刻意很小：`ctx` 是对象引用（原地修改），不走 state 通道。
# 把整个 GameState 塞进 state 会让每一步都做序列化，纯属浪费。
class TurnState(TypedDict, total=False):
    ctx: Any
    visited: list[str]
    broken: str
    # `action` 展开成循环节点后，当前处理到第几个角色
    cursor: int
    # 该阶段被 `when` / `break_if` 跳过（action 的三个节点共享这个标记）
    skip_action: bool
    # 上一个**实际执行**的执行体名：相邻同类只跑一次（`declare`+`resolve` 合并），
    # 不相邻的同类各跑一次（夜里行动 vs 白天发言）
    prev_step: str


def _gate(loop, spec, state: TurnState) -> str:
    """阶段的准入判断。返回 `''` = 可以执行；非空 = 跳过原因（并已记账）。

    记账走 `phases/record.py`——与手写路径**同一个函数**，保证 `skipped` 的结构
    不会因为调度器不同而漂移。
    """
    from .phases.record import record_skip, reason_break, reason_when

    rec = state["ctx"].rec
    if state.get("broken"):
        reason = reason_break(state["broken"])
        record_skip(rec, spec, reason)
        return reason
    if not loop._when_holds(spec.when, state["ctx"], spec):
        reason = reason_when(spec.when)
        record_skip(rec, spec, reason)
        return reason
    return ""


def _phase_node(loop, spec):
    """一个声明阶段 = 一个节点（`action` 以外的 kind）。"""

    async def run(state: TurnState) -> dict:
        if _gate(loop, spec, state):
            # 跳过了也要回写 ctx：checkpoint 里存的是「节点返回的 channel」，
            # 不返回的话恢复时会读到旧副本，之前累积的修改全丢
            return {"ctx": state["ctx"]}

        ctx = state["ctx"]
        # 主持人播报：每个**声明阶段**各发一条，不受「相邻同类合并」影响
        # （`declare` + `resolve` 是同一行动的两半，但台词是两句）
        loop._announce(ctx, spec)
        step_name = KIND_TO_STEP.get(spec.kind)
        prev_step = str(state.get("prev_step") or "")
        # 相邻同类只跑一次：`declare` + `resolve` 是同一行动的两半
        if step_name and step_name != prev_step:
            ctx.current_spec = spec
            await getattr(loop, f"_phase_{step_name}")(ctx)
            prev_step = step_name

        ctx.rec.order.append(spec.id)
        return {"prev_step": prev_step, "ctx": ctx}

    return run


def _action_prepare_node(loop, spec):
    """`action` 的第一段：共议 + NPC 并行 + 排序。"""

    async def run(state: TurnState) -> dict:
        if _gate(loop, spec, state):
            return {"skip_action": True, "ctx": state["ctx"]}

        ctx = state["ctx"]
        loop._announce(ctx, spec)
        prev_step = str(state.get("prev_step") or "")
        if prev_step == "action":
            # 紧挨着的上一个阶段也是 action（`declare` + `resolve`）：这一行动已经做完了。
            # 但要记 order —— 世界声明的每个阶段都该出现在回执里。
            ctx.rec.order.append(spec.id)
            return {"skip_action": True, "prev_step": prev_step, "ctx": ctx}

        ctx.current_spec = spec
        await loop._action_prepare(ctx)
        ctx.rec.order.append(spec.id)
        return {"skip_action": False, "cursor": 0, "prev_step": "action", "ctx": ctx}

    return run


def _action_one_node(loop, spec, enable_interrupt: bool = False):
    """`action` 的循环体：一次只处理一个角色。

    **幂等**靠 `GameLoop._action_one` 里的 `len(ctx.actions)` 检查：
    `interrupt()` 恢复时节点会从头重跑，没有它就会重复调用模型、把同一句话写两遍 briefs。

    `enable_interrupt` 为真时人类席位走 `interrupt()`（暂停整张图，等 API 层喂回提交）。
    **没有 checkpointer 时必须关掉**：LangGraph 不允许无档中断，会直接抛异常；
    这时人类退回 `HumanSeat.wait_action` 的 await 语义（与改造前一致）。
    """

    async def run(state: TurnState) -> dict:
        ctx = state["ctx"]
        if state.get("skip_action"):
            return {"cursor": len(ctx.ordered), "ctx": ctx}

        index = int(state.get("cursor", 0))
        if index >= len(ctx.ordered):
            return {"ctx": ctx}

        cid, card = ctx.ordered[index]
        if (
            enable_interrupt
            and loop.human is not None
            and loop.human.is_human(cid)
        ):
            from langgraph.types import interrupt

            raw = interrupt(
                {
                    "kind": "action",
                    "char_id": cid,
                    "name": card.name,
                    "briefs": list(ctx.briefs),
                }
            )
            if isinstance(raw, dict):
                # 存进槽位，让 `_declare_one` 直接取用（恢复后重跑时读得到）
                loop.human.preload(cid, raw)

        ctx.current_spec = spec
        await loop._action_one(ctx, index)
        return {"cursor": index + 1, "ctx": ctx}

    return run


def _action_finish_node(loop):
    """`action` 的收尾：记 declare 段耗时。"""

    async def run(state: TurnState) -> dict:
        if not state.get("skip_action"):
            loop._action_finish(state["ctx"])
        return {"ctx": state["ctx"]}

    return run


def _route_action(one_name: str, finish_name: str):
    """`action` 循环的条件边：还有角色就回自己，否则进收尾。"""

    def choose(state: TurnState) -> str:
        if state.get("broken") or state.get("skip_action"):
            return finish_name
        return one_name if int(state.get("cursor", 0)) < len(state["ctx"].ordered) else finish_name

    return choose


def _after(loop, spec, nxt: str, remaining: list | None = None):
    """条件边：`break_if` 命中就跳去 settle，否则走下一个阶段。

    `remaining` 是这条边之后**所有还没跑的阶段**：命中 `break_if` 时要逐条记 `skipped`。
    图会直接跳走、后面的节点根本不执行，不主动记账的话回执里就会「少了一段」——
    手写路径是循环里逐条记的，两边必须一致。
    """

    def choose(state: TurnState) -> str:
        from .phases.record import record_skip, reason_break

        if spec.break_if and loop._when_holds(spec.break_if, state["ctx"], spec):
            for other in remaining or []:
                record_skip(state["ctx"].rec, other, reason_break(spec.break_if))
            return "settle"
        return nxt

    return choose


def build_turn_graph(loop, phases, checkpointer=None):
    """按世界包声明的阶段动态构图。**图是编译产物，不是手写代码。**

    加一个阶段 = 在 `turn_flow.yaml` 里多写几行；框架里不需要改任何东西。

    `action` 阶段会**展开成三个节点**（准备 → 逐角色循环 → 收尾）。
    为什么不让它当一个节点：`interrupt()` 恢复时节点从头重跑，一个节点里做完所有角色
    就会重复调用模型。拆开后重跑只影响**当前这一个角色**，再由 `_action_one` 的幂等
    检查兜住。

    `checkpointer` 给了就在每个节点边界存一次状态——这是「存档 / 回放 / 人类接管」
    的基础设施。
    """
    builder = StateGraph(TurnState)
    # 无档不能中断：LangGraph 的 `interrupt()` 要求有 checkpointer
    enable_interrupt = checkpointer is not None

    async def node_director(state: TurnState) -> dict:
        await loop._phase_director(state["ctx"])
        return {"ctx": state["ctx"]}

    async def node_settle(state: TurnState) -> dict:
        await loop._phase_settle(state["ctx"])
        return {"ctx": state["ctx"]}

    builder.add_node("director", node_director)
    builder.add_node("settle", node_settle)

    # 先建节点，同时记下每个阶段的「头节点」与「尾节点」
    layout: list[tuple] = []
    for index, spec in enumerate(phases):
        step_name = KIND_TO_STEP.get(spec.kind)
        if step_name == "action":
            prep, one, fin = f"p{index}_prep", f"p{index}_one", f"p{index}_fin"
            builder.add_node(prep, _action_prepare_node(loop, spec))
            builder.add_node(one, _action_one_node(loop, spec, enable_interrupt))
            builder.add_node(fin, _action_finish_node(loop))
            layout.append((spec, prep, one, fin))
        else:
            name = f"p{index}"
            builder.add_node(name, _phase_node(loop, spec))
            layout.append((spec, name, None, name))

    # 再连边。
    # **不要**额外加 `add_edge(prev_tail, head)`：条件边已经负责「去下一阶段或去 settle」，
    # 再加一条无条件边会在 `break_if` 命中时让「下一阶段」与「settle」**同一个超步并行**，
    # 两边都写 `ctx` → `InvalidUpdateError: Can receive only one value per step`。
    builder.set_entry_point("director")
    if layout:
        builder.add_edge("director", layout[0][1])
    else:
        builder.add_edge("director", "settle")

    for index, (spec, head, one, tail) in enumerate(layout):
        if one is not None:
            builder.add_edge(head, one)
            builder.add_conditional_edges(
                one, _route_action(one, tail), {one: one, tail: tail}
            )
        nxt = layout[index + 1][1] if index + 1 < len(layout) else "settle"
        remaining = [item[0] for item in layout[index + 1 :]]
        builder.add_conditional_edges(
            tail,
            _after(loop, spec, nxt, remaining),
            {nxt: nxt, "settle": "settle"},
        )

    builder.add_edge("settle", END)
    return builder.compile(checkpointer=checkpointer)


def thread_config(session_id: str) -> dict:
    """checkpointer 的线程键：**一局一个 thread**。

    用 `session_id` 而不是 `turn`：同一局的所有回合属于同一条时间线，
    这样 `get_state` 拿到的是「这局到目前为止」的最新快照，回滚也按局来。
    """
    return {"configurable": {"thread_id": str(session_id)}}


async def _resume_human(loop, ctx, event):
    """中断时等人类提交，返回 `Command(resume=...)`。

    **为什么在这里等而不是把控制权交回 API**：为了保持 `/advance` 的
    「挂起直到人类提交」契约——前端与 `/pending`、`/action` 的交互一行都不用改。
    """
    from langgraph.types import Command

    value = getattr(event, "value", None)
    payload = value if isinstance(value, dict) else {}
    char_id = str(payload.get("char_id") or "")
    seat = getattr(loop, "human", None)

    if seat is None or not char_id:
        # 没有可等的人：按「交还给 AI」恢复，不让整局卡死
        return Command(resume={"ghost": True})

    raw = await seat.wait_action(
        char_id,
        str(payload.get("name") or char_id),
        ctx.turn,
        list(payload.get("briefs") or []),
        [],
        kind=str(payload.get("kind") or "action"),
    )
    return Command(resume=raw or {"ghost": True})


async def run_turn(
    loop,
    turn: int | None = None,
    progress=None,
    checkpointer=None,
    thread_id: str | None = None,
):
    """用图跑一回合。与 `loop.step()` 行为等价（L1 验收：`TurnRecord` 逐字段相同）。

    传了 `checkpointer` 与 `thread_id` 就会在每个节点边界留档，并**支持人类中断**：
    图停在 `interrupt()`，这里等人类提交后用 `Command(resume=...)` 继续。
    从外面看仍然是「`advance` 挂住直到人类提交」，对外契约不变。
    """
    from .loop import _make_notify

    loop._notify = _make_notify(progress)
    ctx = loop._open_turn(turn)

    graph = build_turn_graph(loop, ctx.phases, checkpointer=checkpointer)
    config = thread_config(thread_id) if (checkpointer is not None and thread_id) else None

    payload: object = {
        "ctx": ctx,
        "visited": [],
        "broken": "",
        "cursor": 0,
        "skip_action": False,
        "prev_step": "",
    }
    while True:
        result = await graph.ainvoke(payload, config)
        current = result.get("ctx") if isinstance(result, dict) else None
        if current is not None:
            # 恢复后 state 里的是**反序列化副本**，后续节点改的是它，不是前面的闭包对象
            ctx = current
        pending = result.get("__interrupt__") if isinstance(result, dict) else None
        if not pending:
            break
        payload = await _resume_human(loop, ctx, pending[0])

    ctx.rec.elapsed_ms = int((time.perf_counter() - ctx.started) * 1000)
    return ctx.rec
