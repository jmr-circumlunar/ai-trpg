import asyncio
import time
from pathlib import Path

from trpg.engine.loop import GameLoop
from trpg.agents import MockLLM
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.rules.loader import Rules
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]


class SlowLLM(MockLLM):
    """给玩家阶段人为加延迟，用来验证并行是否真的生效。"""

    def __init__(self, seed: int | str, delay: float = 0.1) -> None:
        super().__init__(seed)
        self.delay = delay

    def player_action(self, actor_id, card, state, turn, previous=None):
        time.sleep(self.delay)
        return super().player_action(actor_id, card, state, turn, previous)


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


def test_declare_is_sequential():
    """顺序决策：4 个玩家各 100ms，总耗时应接近 400ms（不再并行）。"""
    loop = build_loop(llm=SlowLLM(seed=9, delay=0.1), seed=9, players=4)
    recs = asyncio.run(loop.run(1))
    assert recs[0].declare_ms >= 380, f"玩家阶段仅 {recs[0].declare_ms}ms，仍是并行"


class OrderLLM(MockLLM):
    """记录每个 Agent 决策时看到的「本回合已发生」。"""

    def __init__(self, seed) -> None:
        super().__init__(seed)
        self.snapshots: list[tuple[str, list[str]]] = []

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.snapshots.append((actor_id, list(previous or [])))
        return super().player_action(actor_id, card, state, turn, previous)


def test_later_actor_sees_previous_results():
    llm = OrderLLM(seed=5)
    loop = build_loop(llm=llm, seed=5, players=3)
    asyncio.run(loop.run(1))

    assert len(llm.snapshots) == 3
    assert llm.snapshots[0][1] == [], "第一个行动者不应看到任何本回合结果"
    assert len(llm.snapshots[1][1]) == 1
    assert len(llm.snapshots[2][1]) == 2


def test_order_follows_dex():
    """先手按 dex 降序，保证可复现。"""
    loop = build_loop(seed=1, players=3)
    actors = loop._active_actors()
    ordered = loop._order_by_dex(actors)
    dexes = [c.ext.stats.get("dex", 10) for _, c in ordered]
    assert dexes == sorted(dexes, reverse=True)
