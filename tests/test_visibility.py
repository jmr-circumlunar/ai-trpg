"""B67 阶段级可见性：`phases[].visibility` 真正决定「后手能不能看到前手做了什么」。

默认 `previous` = 与改造前一致（后者看到前者的行动与结果）；
`private` / `none` = 每个角色都在不知道别人做了什么的情况下决策（信息不对称局的基石）。
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


class RecordingLLM(MockLLM):
    """记录每个角色拿到的 `previous`（= 前手留下的 briefs）。"""

    def __init__(self, seed: int | str) -> None:
        super().__init__(seed)
        self.seen: list[tuple[str, list[str]]] = []

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.seen.append((actor_id, list(previous or [])))
        return super().player_action(actor_id, card, state, turn, previous)


def make_world(tmp_path: Path, turn_flow: str) -> Path:
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    (base / "turn_flow.yaml").write_text(turn_flow, encoding="utf-8")
    return base


def build(tmp_path: Path, turn_flow: str, players: int = 3) -> tuple[GameLoop, RecordingLLM]:
    make_world(tmp_path, turn_flow)
    pack = load_world("wtest", tmp_path)
    cards = {}
    for i in range(players):
        cid = f"chr_p{i + 1}"
        cards[cid] = make_card(cid=cid, profession=["盗贼", "战士", "学者"][i % 3])

    state = GameState(
        session_id="ses_vis01",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    llm = RecordingLLM(42)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, pack=pack), llm


FLOW_PREVIOUS = """
phases:
  - id: scene
    kind: narration
  - id: talk
    kind: action
    visibility: previous
  - id: commit
    kind: commit
"""

FLOW_PRIVATE = """
phases:
  - id: scene
    kind: narration
  - id: night
    kind: action
    visibility: private
  - id: commit
    kind: commit
"""

FLOW_NONE = """
phases:
  - id: scene
    kind: narration
  - id: night
    kind: action
    visibility: none
  - id: commit
    kind: commit
"""


def test_previous_lets_later_actors_see_earlier(tmp_path):
    loop, llm = build(tmp_path, FLOW_PREVIOUS)

    asyncio.run(loop.run(1))

    assert llm.seen, "应当有玩家行动"
    assert llm.seen[0][1] == [], "第一个行动者看不到任何东西"
    assert any(briefs for _, briefs in llm.seen[1:]), "后续行动者应看到前手的结果"


def test_private_hides_everything_from_everyone(tmp_path):
    loop, llm = build(tmp_path, FLOW_PRIVATE)

    asyncio.run(loop.run(1))

    assert llm.seen
    assert all(briefs == [] for _, briefs in llm.seen), "私密阶段里谁都不该看到别人做了什么"


def test_none_behaves_like_private(tmp_path):
    loop, llm = build(tmp_path, FLOW_NONE)

    asyncio.run(loop.run(1))

    assert all(briefs == [] for _, briefs in llm.seen)


def test_visibility_does_not_change_order(tmp_path):
    """可见性只影响「能看到什么」，不影响「谁先动」。"""
    loop_prev, llm_prev = build(tmp_path / "a", FLOW_PREVIOUS)
    loop_priv, llm_priv = build(tmp_path / "b", FLOW_PRIVATE)

    asyncio.run(loop_prev.run(1))
    asyncio.run(loop_priv.run(1))

    assert [cid for cid, _ in llm_prev.seen] == [cid for cid, _ in llm_priv.seen]
