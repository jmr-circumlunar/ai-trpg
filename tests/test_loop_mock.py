import asyncio
import time
from pathlib import Path

from trpg.engine.loop import GameLoop
from trpg.llm import MockLLM
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.rules.loader import Rules
from trpg.testing import make_card

ROOT = Path(__file__).resolve().parents[1]


class SlowLLM(MockLLM):
    """给玩家阶段人为加延迟，用来验证并行是否真的生效。"""

    def __init__(self, seed: int | str, delay: float = 0.1) -> None:
        super().__init__(seed)
        self.delay = delay

    def player_action(self, actor_id, card, state, turn):
        time.sleep(self.delay)
        return super().player_action(actor_id, card, state, turn)


def build_loop(llm=None, seed: int = 42, players: int = 3) -> GameLoop:
    cards = {}
    for i in range(players):
        cid = f"chr_p{i + 1}"
        cards[cid] = make_card(profession=["盗贼", "战士", "学者", "医师"][i % 4])

    state = GameState(
        session_id="ses_loop01",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    return GameLoop(Rules(ROOT), cards, state, llm or MockLLM(seed), seed=seed)


def test_same_seed_same_skeleton():
    a = asyncio.run(build_loop(seed=42).run(6))
    b = asyncio.run(build_loop(seed=42).run(6))
    assert [r.outcomes for r in a] == [r.outcomes for r in b]
    assert [r.version for r in a] == [r.version for r in b]


def test_different_seed_differs():
    a = asyncio.run(build_loop(seed=1).run(8))
    b = asyncio.run(build_loop(seed=2).run(8))
    assert [r.outcomes for r in a] != [r.outcomes for r in b]


def test_run_20_turns_no_crash():
    recs = asyncio.run(build_loop(seed=7).run(20))
    assert len(recs) == 20
    assert recs[-1].version >= 20
    assert all(len(r.rejected) <= 3 for r in recs)


def test_stage_advances_at_turn_10():
    recs = asyncio.run(build_loop(seed=3).run(11))
    assert recs[-1].version > recs[0].version


def test_declare_stage_is_parallel():
    """4 个玩家各 100ms：串行 ≥400ms，并行应明显更短。"""
    loop = build_loop(llm=SlowLLM(seed=9, delay=0.1), seed=9, players=4)
    recs = asyncio.run(loop.run(1))
    assert recs[0].declare_ms < 300, f"玩家阶段耗时 {recs[0].declare_ms}ms，并行未生效"
