"""B55 人类投票：决策面板要能真的投票，不能只能看票。

链路：collective 阶段遇到人类席位 → 挂起等票 → 前端提交 → 代码计票 → 票型进回执。
人类**弃权**也是合法结果（超时/交还），但不能代投——代投会让「谁投了谁」变成假的。
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

WOLVES = 3
VILLAGERS = 5


def build_loop(human_ids: list[str] | None = None):
    pack = load_world("werewolf", ROOT)
    cards = {}
    characters = {}
    for index in range(WOLVES + VILLAGERS):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index < WOLVES else "village"
        cards[cid] = make_card(
            cid=cid, profession="狼人" if camp == "wolf" else "村民"
        )
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id="ses_vote",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    seat = HumanSeat(human_ids=list(human_ids or []), timeout=5.0)
    loop = GameLoop(
        Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack, human=seat
    )
    return loop, seat


def _drive(loop, seat, vote_target: str | None = "chr_2"):
    """跑一回合：人类遇到「行动」就随便做一个，遇到「投票」就投给 `vote_target`。"""
    seen_vote = False

    async def scenario():
        nonlocal seen_vote
        task = asyncio.create_task(loop.run(1))
        for _ in range(500):
            await asyncio.sleep(0.01)
            pending = seat.pending()
            if not pending:
                if task.done():
                    return await task
                continue
            if pending["kind"] == "vote":
                seen_vote = True
                assert "chr_2" in pending["candidates"]
                assert pending["candidates"], "投票必须给出候选"
                assert "chr_1" not in pending["candidates"], "不能投自己"
                seat.submit(
                    pending["char_id"],
                    {"target": vote_target} if vote_target else {"ghost": True},
                )
            else:
                seat.submit(pending["char_id"], {"action": "我四下看看"})
        task.cancel()
        pytest.fail("等待超时：没等到人类投票")

    records = asyncio.run(scenario())
    assert seen_vote, "collective 阶段应当向人类席位要票"
    return records[0]


def test_human_is_asked_to_vote_and_vote_is_counted():
    loop, seat = build_loop(human_ids=["chr_1"])

    rec = _drive(loop, seat, vote_target="chr_2")

    mine = [
        v for v in rec.votes if v["voter"] == "chr_1" and v.get("kind") == "vote"
    ]
    assert mine, "人类的票应出现在回执里"
    assert mine[0]["target"] == "chr_2"
    assert rec.vote_result.get("counts"), "计票结果应可查"
    # 共议票里不该有人类（不许代投）
    consensus = [
        v for v in rec.votes if v.get("kind") == "consensus" and v["voter"] == "chr_1"
    ]
    assert not consensus, "人类不能被 AI 代投共议票"


def test_human_can_abstain():
    """弃权是合法结果，但票型里要有痕迹。"""
    loop, seat = build_loop(human_ids=["chr_1"])

    rec = _drive(loop, seat, vote_target=None)

    mine = [
        v for v in rec.votes if v["voter"] == "chr_1" and v.get("kind") == "vote"
    ]
    assert mine and mine[0]["target"] is None


def test_human_vote_is_not_double_counted():
    """人类**每轮**只投一次：不能既被自动投票又被等一次。

    注意不是「总共一票」——平票会触发 PK 重投（标准规则），
    那时人类会被再问一次，属于正常行为。
    """
    loop, seat = build_loop(human_ids=["chr_1"])

    rec = _drive(loop, seat, vote_target="chr_2")

    mine = [
        v for v in rec.votes if v["voter"] == "chr_1" and v.get("kind") == "vote"
    ]
    assert mine, "人类应当投过票"
    rounds = {vote["round"] for vote in mine}
    assert len(mine) == len(rounds), f"每轮只该投一票，实际 {mine}"
