"""L3：图路径接管默认，`step()` 可一键退回手写路径。

验收三件事：

1. **默认是 graph**——不显式指定就走 LangGraph；
2. **切回 manual 行为不变**——两条引擎跑同一局，回执逐字段相同；
3. **`step()` 真的在用图**——有 checkpointer 时会留档（手写路径不会）。

保留 manual 不是为了兼容旧代码，是为了出问题时能在**同一份世界包、同一个 seed**
下做对照：如果 graph 出错而 manual 正常，问题一定在调度层；反之说明问题在阶段执行体。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.graph import thread_config  # noqa: E402
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

FIELDS = (
    "turn",
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


def build_loop(engine: str = "graph", checkpointer=None, seed: int = 42) -> GameLoop:
    pack = load_world("werewolf", ROOT)
    cards, characters = {}, {}
    for index in range(3):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index == 0 else "village"
        cards[cid] = make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)
    state = GameState(
        session_id="ses_engine",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    return GameLoop(
        Rules(ROOT),
        cards,
        state,
        MockLLM(seed),
        seed=seed,
        pack=pack,
        engine=engine,
        checkpointer=checkpointer,
    )


def _snapshot(rec) -> dict:
    return {name: getattr(rec, name) for name in FIELDS}


def test_default_engine_is_graph():
    assert build_loop().engine == "graph"


def test_manual_engine_matches_graph():
    """同一 seed 下两条引擎逐字段相同——这是保留 manual 的唯一价值。"""
    graph_loop = build_loop(engine="graph")
    manual_loop = build_loop(engine="manual")

    for _ in range(2):
        by_graph = asyncio.run(graph_loop.step())
        by_manual = asyncio.run(manual_loop.step())

        assert _snapshot(by_manual) == _snapshot(by_graph)


def test_step_actually_uses_graph():
    """默认走图才会留档；手写路径没有 checkpointer 可言。"""
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    loop = build_loop(checkpointer=saver)

    asyncio.run(loop.step())

    assert list(saver.list(thread_config(loop.thread_id))), "默认路径应当留档"


def test_manual_engine_writes_no_checkpoint():
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    loop = build_loop(engine="manual", checkpointer=saver)

    asyncio.run(loop.step())

    assert list(saver.list(thread_config(loop.thread_id))) == []
