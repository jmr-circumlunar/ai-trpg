"""LangGraph 编排层：图路径与手写路径必须**逐字段等价**。

这是引入 LangGraph 的第一道闸门——先证明「换调度器不改行为」，再往上加
checkpointer / interrupt / Store 才有意义。等价性一旦破了，后面的东西都建在沙上。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.graph import build_turn_graph, run_turn  # noqa: E402
from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.models import (  # noqa: E402
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.rules.loader import Rules  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402

WOLVES = 3
VILLAGERS = 5


def build_loop(world: str = "werewolf", players: int = 8, seed: int = 42) -> GameLoop:
    """造一局。**每次调用都是独立实例**（独立的 llm / state / 骰子），两条路径各用一份。"""
    pack = load_world(world, ROOT)
    cards = {}
    characters = {}
    for index in range(min(players, WOLVES + VILLAGERS)):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index < WOLVES else "village"
        from support.builders import make_card

        cards[cid] = make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id=f"ses_graph_{seed}",
        world=WorldRef(world_id=world, era="base"),
        scene=Scene(location_id="loc_square" if world == "werewolf" else "loc_gate",
                    region="village" if world == "werewolf" else "hub"),
        party=Party(),
        characters=characters,
    )
    return GameLoop(Rules(ROOT), cards, state, MockLLM(seed), seed=seed, pack=pack)


FIELDS = (
    "turn",
    "scene",
    "order",
    "skipped",
    "actors",
    "thoughts",
    "actions",
    "outcomes",
    "narration",
    "deaths",
    "votes",
    "vote_result",
)


def _snapshot(rec) -> dict:
    return {name: getattr(rec, name) for name in FIELDS}


def test_graph_engine_matches_handwritten_step():
    """同一 seed、同一世界：图路径 3 回合的输出与手写路径逐字段相同。"""
    manual = build_loop()
    graph = build_loop()

    for _ in range(3):
        expected = asyncio.run(manual.step())
        actual = asyncio.run(run_turn(graph))

        assert _snapshot(actual) == _snapshot(expected)


def test_graph_engine_carries_state_forward():
    """图路径不只是「跑一次」：状态、回合号、胜负都要往前推。"""
    graph = build_loop()

    first = asyncio.run(run_turn(graph))
    second = asyncio.run(run_turn(graph))

    assert first.turn == 1
    assert second.turn == 2
    assert graph.next_turn == 3


def test_graph_has_a_node_per_declared_phase():
    """图是**声明编译出来的**：世界包写几个阶段，图就有几个节点。"""
    graph_loop = build_loop()
    ctx = graph_loop._open_turn(1)

    compiled = build_turn_graph(graph_loop, ctx.phases)

    nodes = set(compiled.get_graph().nodes)
    assert "director" in nodes
    assert "settle" in nodes
    # `action` 展开成 3 个节点（准备 / 逐角色循环 / 收尾），其余阶段各 1 个
    action_phases = sum(1 for spec in ctx.phases if spec.kind == "action")
    others = len(ctx.phases) - action_phases
    assert len([n for n in nodes if n.startswith("p")]) == others + action_phases * 3


def test_graph_skips_when_predicate_fails():
    """`when` 不命中的阶段不产生 order，只留 skipped（与手写路径一致）。"""
    graph_loop = build_loop()
    ctx = graph_loop._open_turn(1)

    # 把第一个阶段的 when 改成一个必然为假的谓词
    from trpg.engine.pipeline import PhaseSpec

    ctx.phases = (
        PhaseSpec(id="never", kind="narration", when="enemies_present"),
        *ctx.phases[1:],
    )

    compiled = build_turn_graph(graph_loop, ctx.phases)
    asyncio.run(compiled.ainvoke({"ctx": ctx, "visited": [], "broken": ""}))

    assert "never" not in ctx.rec.order
    assert any(item["id"] == "never" for item in ctx.rec.skipped)
