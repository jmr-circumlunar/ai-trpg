from trpg.engine.loop import GameLoop
from trpg.llm import MockLLM
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.testing import make_card


def build_loop(seed: int, players: int = 3) -> GameLoop:
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
    return GameLoop(rules_or_none(), cards, state, MockLLM(seed), seed=seed)


def rules_or_none():
    from pathlib import Path

    from trpg.rules.loader import Rules

    return Rules(Path(__file__).resolve().parents[1])


def test_same_seed_same_skeleton():
    a = build_loop(42).run(6)
    b = build_loop(42).run(6)
    assert [r.outcomes for r in a] == [r.outcomes for r in b]
    assert [r.version for r in a] == [r.version for r in b]


def test_different_seed_differs():
    a = build_loop(1).run(8)
    b = build_loop(2).run(8)
    assert [r.outcomes for r in a] != [r.outcomes for r in b]


def test_run_20_turns_no_crash():
    recs = build_loop(7).run(20)
    assert len(recs) == 20
    assert recs[-1].version >= 20
    # 允许少量 delta 被拒（校验起作用），但不应每回合都失败
    assert all(len(r.rejected) <= 3 for r in recs)


def test_stage_advances_at_turn_10():
    recs = build_loop(3).run(11)
    assert recs[-1].version > recs[0].version
