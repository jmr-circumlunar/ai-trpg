"""推理图（方案 38 / C70）：agent 的 ReAct 循环由 **LangGraph 子图**表达。

守三件事：
1. **行为等价**：思考 → 调工具 → 观察 → 再思考 → 给出行动；
2. **步数上限**：模型一直要工具时，用尽步数就走兜底（与旧循环一致）；
3. **可观测**：节点边界留下 trace（思考 → 工具 → 观察 → 思考）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents.reasoner import (  # noqa: E402
    AgentState,
    build_reason_graph,
    run_reason,
)


def _graph(think, act, max_steps: int = 3):
    return build_reason_graph(think, act, max_steps=max_steps)


# ── 正常路径 ──────────────────────────────────────────────────


def test_graph_runs_think_act_until_final():
    """要工具 → 执行 → 再思考 → 给出行动。"""
    calls: list[str] = []

    def think(state: AgentState) -> dict:
        calls.append("think")
        if not state.scratch:
            return {"output": {"type": "tool", "tool": "recall", "args": {"query": "x"}}}
        return {"output": {"type": "final", "action": "观察四周"}, "done": True}

    def act(state: AgentState) -> dict:
        calls.append("act")
        return {"scratch": list(state.scratch) + ["recall → 想起 1 条"]}

    final = run_reason(_graph(think, act), AgentState())

    assert calls == ["think", "act", "think"]
    assert final.output["type"] == "final"
    assert final.scratch == ["recall → 想起 1 条"]


def test_graph_records_trace():
    """trace：思考 → 工具 → 观察 → 思考（复盘与调试用）。"""

    def think(state: AgentState) -> dict:
        trace = list(state.trace)
        trace.append({"step": state.steps + 1, "kind": "think"})
        if not state.scratch:
            return {
                "output": {"type": "tool", "tool": "recall", "args": {}},
                "steps": state.steps + 1,
                "trace": trace,
            }
        return {
            "output": {"type": "final"},
            "done": True,
            "steps": state.steps + 1,
            "trace": trace,
        }

    def act(state: AgentState) -> dict:
        trace = list(state.trace)
        trace.append({"step": state.steps, "kind": "act"})
        return {"scratch": list(state.scratch) + ["结果"], "trace": trace}

    final = run_reason(_graph(think, act), AgentState())

    kinds = [item["kind"] for item in final.trace]
    assert "think" in kinds and "act" in kinds
    assert kinds.index("think") < kinds.index("act")


# ── 上限与兜底 ────────────────────────────────────────────────


def test_graph_stops_at_max_steps():
    """模型一直要工具：步数用尽就停（由调用方兜底），不无限循环。"""
    think_calls = {"n": 0}

    def think(state: AgentState) -> dict:
        think_calls["n"] += 1
        return {"output": {"type": "tool", "tool": "recall", "args": {}}, "steps": state.steps + 1}

    def act(state: AgentState) -> dict:
        return {"scratch": list(state.scratch) + ["又想起一条"]}

    final = run_reason(_graph(think, act, max_steps=3), AgentState())

    assert think_calls["n"] == 3, "模型调用次数应等于步数上限"
    assert final.error == "step_limit"


def test_graph_propagates_model_error():
    def think(state: AgentState) -> dict:
        return {"done": True, "error": "model_error", "output": {}}

    def act(state: AgentState) -> dict:  # pragma: no cover - 不该被调用
        raise AssertionError("出错时不该执行工具")

    final = run_reason(_graph(think, act), AgentState())

    assert final.error == "model_error"
    assert final.done is True


# ── 与知识视图的接线（编排层）─────────────────────────────────


def test_loop_builds_knowledge_for_the_current_phase():
    """编排层按**当前阶段**取知识视图：夜里的阶段 → 首夜准则。"""
    sys.path.insert(0, str(ROOT / "tests"))
    from test_skills import build  # noqa: E402

    loop, _ = build(ROOT / "tests" / "_tmp_knowledge")
    loop.pack.knowledge = {
        "enabled": True,
        "sections": ["identity", "alive", "stage"],
        "phases": {"hidden": ["night"], "open": ["discuss"]},
        "strategies": [
            {
                "id": "first_night_facts",
                "phase": "hidden",
                "when": "cycle == 1",
                "text": "还没有人发言过，别凭空推断谁可疑。",
            }
        ],
    }

    ctx = SimpleNamespace(rec=SimpleNamespace(votes=[]), briefs=[], turn=1)
    night_spec = SimpleNamespace(id="night", banner="天黑请闭眼。")
    day_spec = SimpleNamespace(id="discuss", banner="开始白天发言。")

    night_text = loop._knowledge_for(ctx, "chr_1", night_spec)
    day_text = loop._knowledge_for(ctx, "chr_1", day_spec)

    assert "# 你现在知道的" in night_text
    assert "还没有人发言过" in night_text, "首夜阶段应注入首夜准则"
    assert "还没有人发言过" not in day_text, "白天不该注入首夜准则"
