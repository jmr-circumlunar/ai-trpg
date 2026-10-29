"""L2：人类席位走 LangGraph `interrupt()`。

要证明三件事：

1. 图**真的会停**（人类没提交前不往下走），而不是把人类那一轮空跑掉；
2. 停下来时**已经留档**（checkpointer 有记录，所以能恢复）；
3. 恢复后**不会重复调用模型**（`_action_one` 的幂等检查兜住重跑）。

对外契约不变：从 `run_turn` 外面看，仍然是「挂住直到人类提交」。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.graph import run_turn, thread_config  # noqa: E402
from trpg.engine.human import HumanSeat  # noqa: E402
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


class CountingLLM(MockLLM):
    """数一下 `player_action` 被调了几次——用来证明恢复后没有重复调用。"""

    def __init__(self, seed):
        super().__init__(seed)
        self.action_calls = 0

    def player_action(self, *args, **kwargs):
        self.action_calls += 1
        return super().player_action(*args, **kwargs)


def build_loop(human_ids: list[str] | None = None, seed: int = 42):
    pack = load_world("werewolf", ROOT)
    cards, characters = {}, {}
    for index in range(3):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index == 0 else "village"
        cards[cid] = make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)
    state = GameState(
        session_id="ses_int",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    seat = HumanSeat(human_ids=list(human_ids or []), timeout=5.0)
    llm = CountingLLM(seed)
    loop = GameLoop(Rules(ROOT), cards, state, llm, seed=seed, pack=pack, human=seat)
    return loop, seat, llm


def _drive(loop, seat, saver, thread_id="ses_int", submit=True):
    """在后台跑一回合；等人类待办出现就提交（或让它超时代打）。"""
    seen_pending = []
    paused_with_checkpoint = []

    async def scenario():
        task = asyncio.create_task(
            run_turn(loop, checkpointer=saver, thread_id=thread_id)
        )
        # 注意：不能因为 `task.done()` 就跳出——图暂停时 `ainvoke` 已返回，
        # 但 `run_turn` 还在 `_resume_human` 里等人类提交，任务并未结束。
        for _ in range(500):
            await asyncio.sleep(0.01)
            pending = seat.pending()
            if pending:
                seen_pending.append(pending)
                # 停在此时应当已经有档（否则恢复不了）
                paused_with_checkpoint.append(
                    len(list(saver.list(thread_config(thread_id))))
                )
                if submit:
                    seat.submit(pending["char_id"], {"action": "我假装在数星星"})
                break
        return await task

    record = asyncio.run(scenario())
    return record, seen_pending, paused_with_checkpoint


def test_graph_pauses_for_human_and_resumes():
    from langgraph.checkpoint.memory import InMemorySaver

    loop, seat, llm = build_loop(human_ids=["chr_2"])
    record, pendings, checkpoints = _drive(loop, seat, InMemorySaver())

    assert pendings, "图应当停下来等人类"
    assert pendings[0]["char_id"] == "chr_2"
    assert pendings[0]["kind"] == "action"
    assert checkpoints and checkpoints[0] >= 1, "暂停时就已经有档，才能恢复"
    assert "chr_2" in record.actors


def test_resume_does_not_duplicate_model_calls():
    """恢复时节点会从头重跑；幂等检查必须挡住重复的模型调用。"""
    from langgraph.checkpoint.memory import InMemorySaver

    loop, seat, llm = build_loop(human_ids=["chr_2"])
    record, _, _ = _drive(loop, seat, InMemorySaver())

    # 3 人局、有人类占一个 → 只剩 2 个角色需要模型代打（每个可能多步 ReAct）
    # 关键不是精确次数，而是**没有因为重跑而翻倍**
    assert llm.action_calls <= 6, f"模型被调了 {llm.action_calls} 次，像是重跑导致重复"
    assert len(record.actors) == len(set(record.actors)), "行动者不该重复"


def test_without_checkpointer_human_falls_back_to_waiting():
    """无 checkpointer 时不能 `interrupt`：退回 `HumanSeat.wait_action` 的 await 语义。"""
    loop, seat, _ = build_loop(human_ids=["chr_2"])

    seen = []

    async def scenario():
        task = asyncio.create_task(run_turn(loop))
        for _ in range(400):
            await asyncio.sleep(0.01)
            pending = seat.pending()
            if pending:
                seen.append(pending)
                seat.submit(pending["char_id"], {"action": "我先看看"})
                break
            if task.done():
                break
        return await task

    record = asyncio.run(scenario())

    assert seen, "没有 interrupt 也应当在等人类"
    assert "chr_2" in record.actors
