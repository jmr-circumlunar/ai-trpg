"""C54：`TurnRecord.order` 必须记录本回合**实际执行**的阶段顺序。

这一层是 pipeline 与 loop 的接缝：计划由 `engine/pipeline.py` 算，回执写在 `TurnRecord`。
当前只记录不改变执行顺序，因此 `ironvale` 的行为必须与改造前完全一致（golden 守）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.models import (  # noqa: E402
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.rules.loader import Rules  # noqa: E402

STANDARD_ORDER = ["scene", "declare", "resolve", "narrate", "commit"]


def build_loop(world_id: str = "ironvale", era: str = "medieval", region: str = "north_port",
               location: str = "loc_a", seed: int = 42, players: int = 2) -> GameLoop:
    cards = {}
    for i in range(players):
        cid = f"chr_p{i + 1}"
        cards[cid] = make_card(cid=cid, profession=["盗贼", "战士"][i % 2])

    state = GameState(
        session_id=f"ses_order_{world_id}",
        world=WorldRef(world_id=world_id, era=era),
        scene=Scene(location_id=location, region=region),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    return GameLoop(Rules(ROOT), cards, state, MockLLM(seed), seed=seed)


def test_ironvale_records_standard_order():
    """无 turn_flow.yaml 的世界走 standard preset：5 个阶段，全部执行。"""
    loop = build_loop()

    records = asyncio.run(loop.run(1))

    assert records[0].order == STANDARD_ORDER
    assert records[0].skipped == []


def test_blank_world_records_its_declared_order():
    """`_blank` 显式声明了同样 5 个阶段，顺序应一致（id 由世界包自己取）。"""
    loop = build_loop(
        world_id="_blank", era="base", region="main", location="loc_01"
    )

    records = asyncio.run(loop.run(1))

    assert records[0].order == STANDARD_ORDER


def test_order_is_stable_across_turns():
    loop = build_loop()

    records = asyncio.run(loop.run(2))

    assert [rec.order for rec in records] == [STANDARD_ORDER, STANDARD_ORDER]


def test_record_with_order_is_serialisable():
    loop = build_loop()

    rec = asyncio.run(loop.run(1))[0]

    payload = json.dumps(dataclasses.asdict(rec), ensure_ascii=False, default=str)
    assert '"order"' in payload
