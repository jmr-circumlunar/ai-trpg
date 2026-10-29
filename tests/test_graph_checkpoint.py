"""L2：图路径的 checkpointer（存档 / 回放的地基）。

为什么先只做 checkpointer、不动 `interrupt`：
`interrupt()` 恢复时**节点会从头重跑**，而我们的 `_phase_action` 在一个节点里做完
「NPC 并行 + 逐个 declare」——重跑会重复调用模型。要安全用 `interrupt` 得先把它拆成
「一次处理一个角色」的循环节点，那是独立一批的改动。

checkpointer 本身已经有明确收益：每个节点边界留一档，能查中间态、能按局回滚。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.graph import run_turn, thread_config  # noqa: E402
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


def build_loop(seed: int = 42) -> GameLoop:
    pack = load_world("werewolf", ROOT)
    cards, characters = {}, {}
    for index in range(3):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index == 0 else "village"
        cards[cid] = make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)
    state = GameState(
        session_id="ses_cp",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    return GameLoop(Rules(ROOT), cards, state, MockLLM(seed), seed=seed, pack=pack)


def test_in_memory_checkpointer_records_every_node():
    from langgraph.checkpoint.memory import InMemorySaver

    loop = build_loop()
    saver = InMemorySaver()

    asyncio.run(run_turn(loop, checkpointer=saver, thread_id="ses_mem"))

    snapshots = list(saver.list(thread_config("ses_mem")))
    # director + 每声明阶段 + settle = 至少 4 个节点边界
    assert len(snapshots) >= 4, f"只在 {len(snapshots)} 个边界留了档"
    assert any(item.metadata.get("source") == "loop" for item in snapshots)


def test_checkpoint_carries_business_state():
    """节点必须把 `ctx` **回写**进 state，否则 checkpoint 里只有输入快照。

    早期版本节点返回 `{}`（靠原地修改 `ctx`），LangGraph 只记录「输入值 + 节点返回的
    channel」，于是档里 `rec.order` 恒为空。接 `interrupt` 时才暴露后果：
    **恢复时读的是旧档，之前所有节点的修改全丢**（行动记录归零、投票结果消失）。

    所以现在每个节点都 `return {"ctx": ctx}`。这条测试守住它，
    顺便确认「档里能读到这一回合的回执」。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    loop = build_loop()
    saver = InMemorySaver()
    config = thread_config("ses_rec")

    asyncio.run(run_turn(loop, checkpointer=saver, thread_id="ses_rec"))

    snapshots = list(saver.list(config))
    assert snapshots, "应当留下流程档"
    assert all(item.metadata for item in snapshots), "每档都要有 metadata"

    latest = saver.get_tuple(config)
    assert latest is not None
    ctx_in_checkpoint = latest.checkpoint["channel_values"]["ctx"]
    assert ctx_in_checkpoint.turn == 1
    assert ctx_in_checkpoint.rec.order, "回执应当随档保存（节点回写 ctx 的效果）"


def test_async_sqlite_checkpointer_survives_reopen(tmp_path):
    """真正落盘：关掉再打开，同一 thread 的历史还在。

    `run_turn` 是 async 的（`graph.ainvoke`），所以必须用 `AsyncSqliteSaver`——
    同步版 `SqliteSaver` 的异步方法直接 `NotImplementedError`。
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "checkpoints.db"
    config = thread_config("ses_disk")

    async def write() -> int:
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            await run_turn(build_loop(), checkpointer=saver, thread_id="ses_disk")
            return len([item async for item in saver.alist(config)])

    written = asyncio.run(write())
    assert written >= 4

    async def read() -> int:
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            return len([item async for item in saver.alist(config)])

    assert asyncio.run(read()) >= 4, "重开之后历史应当还在"


def test_two_sessions_do_not_share_checkpoints():
    """线程键按**局**分：两局的 checkpoint 不能串。"""
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    asyncio.run(run_turn(build_loop(), checkpointer=saver, thread_id="ses_a"))
    asyncio.run(run_turn(build_loop(), checkpointer=saver, thread_id="ses_b"))

    assert list(saver.list(thread_config("ses_a")))
    assert list(saver.list(thread_config("ses_b")))
    first = saver.get_tuple(thread_config("ses_a"))
    assert first.checkpoint["channel_values"]["ctx"].turn == 1


def test_without_checkpointer_nothing_changes():
    """不传 checkpointer 时与 L1 完全一致（CLI 与单测默认走这条）。"""
    loop = build_loop()

    rec = asyncio.run(run_turn(loop))

    assert rec.turn == 1
    assert rec.order
