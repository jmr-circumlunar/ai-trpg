"""B66 分组行动与共议（C57 的上层）：组内先投一轮，决议写进组内 briefs。

设计取舍：**不引入「组代表」**——决议只影响组内成员下一步做什么，每人仍各自行动，
这样 `TurnRecord.actors/actions/outcomes` 的平行结构不会被打破（前端与复现都依赖它）。
"""

from __future__ import annotations

import asyncio
import shutil
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
from trpg.world.pack import load_world  # noqa: E402

BLANK = ROOT / "worlds" / "_blank"

FLOW = """
phases:
  - id: scene
    kind: narration
  - id: act
    kind: action
    groups:
      - { by: camp, value: wolf, name: 狼, mode: consensus }
  - id: vote
    kind: collective
    rule: plurality
    tie: random
  - id: commit
    kind: commit
"""


class RecordingLLM(MockLLM):
    def __init__(self, seed: int | str) -> None:
        super().__init__(seed)
        self.seen: list[tuple[str, list[str]]] = []

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.seen.append((actor_id, list(previous or [])))
        return super().player_action(actor_id, card, state, turn, previous)


def build(tmp_path: Path) -> tuple[GameLoop, RecordingLLM]:
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    (base / "turn_flow.yaml").write_text(FLOW, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    camps = {"chr_1": "wolf", "chr_2": "wolf", "chr_3": "village"}
    cards = {}
    characters = {}
    for cid, camp in camps.items():
        cards[cid] = make_card(cid=cid, profession="盗贼")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id="ses_groups",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(gold=10, supplies=3),
        characters=characters,
    )
    llm = RecordingLLM(42)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, pack=pack), llm


def test_consensus_decision_reaches_group_members(tmp_path):
    loop, llm = build(tmp_path)

    asyncio.run(loop.run(1))

    wolves = [briefs for cid, briefs in llm.seen if cid in ("chr_1", "chr_2")]
    assert wolves, "狼组应当行动"
    assert any("共同决定" in line for briefs in wolves for line in briefs)


def test_consensus_and_vote_both_land_in_receipt(tmp_path):
    loop, _ = build(tmp_path)

    rec = asyncio.run(loop.run(1))[0]

    # 共议 2 张（狼内部）+ 投票 3 张（全体）
    assert len(rec.votes) == 5
    assert rec.vote_result.get("winner") in {"chr_1", "chr_2", "chr_3"}


def test_non_group_member_does_not_see_consensus(tmp_path):
    loop, llm = build(tmp_path)

    asyncio.run(loop.run(1))

    villager = [briefs for cid, briefs in llm.seen if cid == "chr_3"]
    assert villager, "村民也应当行动"
    assert all("共同决定" not in line for line in villager[0])
