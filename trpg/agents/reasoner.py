"""M12 · 推理图：把 agent 的 ReAct 循环用 **LangGraph** 表达。

以前是手写 `for step in range(max_steps)`；现在是一张图：

```
START → think ──(要调工具)──→ act ──→ think ──→ …
            └──(直接给行动)──→ END
```

为什么值得这么做（框架学习的核心诉求）：

- **可观测**：节点边界产出 trace（思考 → 工具 → 观察 → 思考），复盘与调试能看；
- **可中断 / 可回放**：checkpointer 是图的一等公民（`MemorySaver`），
  以后要做「人类介入 agent 推理」不必再手写状态机；
- **和编排层同款**：回合编排已经用 LangGraph（`trpg/engine/graph.py`），
  两处一致，少一种心智模型。

不变的部分（**行为等价**是硬要求）：
模型调用、工具集合、结构化输出 schema、步数上限、超限兜底——全部复用既有实现，
本模块只负责**编排**。

doc: 03-模块/M12-智能体/00-模块总述.md
module: M12
layer: L3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph


@dataclass
class AgentState:
    """一个 agent 推理过程的状态（图的状态节点）。"""

    steps: int = 0
    #: 工具调用的观察结果（拼进下一次提示的「你已经查到的」）
    scratch: list[str] = field(default_factory=list)
    #: 记忆类工具召回的内容（进 `pc_user` 的 memories 段）
    recalled: list[str] = field(default_factory=list)
    #: 模型最后一次输出（工具调用或最终行动）
    output: dict[str, Any] = field(default_factory=dict)
    #: 结束原因："" = 正常给出行动；"step_limit" = 步数用尽；"error" = 模型调用失败
    error: str = ""
    #: 逐步轨迹：思考 → 工具 → 观察 → 思考（复盘与调试用）
    trace: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False


def build_reason_graph(
    think: Callable[[AgentState], dict[str, Any]],
    act: Callable[[AgentState], dict[str, Any]],
    max_steps: int,
    checkpointer: Any = None,
):
    """搭出推理图。

    - `think(state)`：调模型，返回 `{output, scratch?, recalled?, trace?, done?, error?}`；
      若判定「要调工具」就返回 `{"output": {...type: tool...}}`（不置 done）；
      若判定「直接给行动」就返回 `{"output": {...}, "done": True}`。
    - `act(state)`：执行工具，把观察结果写进 `scratch`，返回 `{scratch, recalled, trace}`。
    - `max_steps`：模型调用次数上限（与旧的 `memory.max_steps` 同口径）。
    """
    limit = max(1, int(max_steps or 1))

    def think_node(state: AgentState) -> dict[str, Any]:
        if state.steps >= limit:
            # 步数用尽：与旧循环一致——用最后一次判断兜底（由调用方处理）
            return {"done": True, "error": "step_limit"}
        return think(state)

    def route(state: AgentState) -> str:
        if state.done:
            return "end"
        kind = str((state.output or {}).get("type") or "").strip().lower()
        return "act" if kind == "tool" else "end"

    graph = StateGraph(AgentState)
    graph.add_node("think", think_node)
    graph.add_node("act", act)
    graph.add_edge(START, "think")
    graph.add_conditional_edges("think", route, {"act": "act", "end": END})
    graph.add_edge("act", "think")
    return graph.compile(checkpointer=checkpointer)


def run_reason(graph, state: AgentState) -> AgentState:
    """跑推理图并把结果规整成 `AgentState`。

    LangGraph 的 `invoke` 依版本返回 dict 或状态实例——**两种都收**，
    调用方不必各写一次转换（写两次就一定会分叉）。
    """
    final = graph.invoke(state)
    if isinstance(final, AgentState):
        return final
    if isinstance(final, dict):
        return AgentState(
            **{
                key: value
                for key, value in final.items()
                if key in AgentState.__dataclass_fields__
            }
        )
    return state
