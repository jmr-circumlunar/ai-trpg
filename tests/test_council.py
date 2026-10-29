"""组内**有限轮对话**（`groups[].mode: council`）。

`consensus` 是「各投一票」，没有交流；`council` 是「先说话再说定不定」。
信息对抗类玩法里，组内商量本身就是核心体验——夜里不让人开口，等于把最重要的
一段博弈删掉了。

但**开放式对话会把一回合的耗时与费用拉爆**，所以有两道闸：

| 闸 | 参数 | 作用 |
|---|---|---|
| 轮次 | `rounds` | 最多几轮发言 |
| 真实秒数 | `budget_s` | 兜底，防止模型啰嗦 |

任一先到就停。这两条是「交流应该有约束」的落地方式——不依赖模型自觉。
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
      - { by: camp, value: wolf, name: 狼, mode: council, rounds: 2, chained: false }
"""


class CouncilLLM(MockLLM):
    """记录每次组内发言，用来断言轮次上限与可见性。"""

    def __init__(self, seed: int | str) -> None:
        super().__init__(seed)
        self.said: list[tuple[str, int]] = []  # (char_id, 已发言条数)
        self.seen: list[tuple[str, list[str]]] = []

    def council_say(self, actor_id, card, state, turn, transcript=None) -> str:
        self.said.append((actor_id, len(transcript or [])))
        return super().council_say(actor_id, card, state, turn, transcript)

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.seen.append((actor_id, list(previous or [])))
        return super().player_action(actor_id, card, state, turn, previous)


def build(tmp_path: Path, flow: str = FLOW) -> tuple[GameLoop, CouncilLLM]:
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    (base / "turn_flow.yaml").write_text(flow, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    camps = {"chr_1": "wolf", "chr_2": "wolf", "chr_3": "village"}
    cards = {}
    characters = {}
    for cid, camp in camps.items():
        cards[cid] = make_card(cid=cid, profession="盗贼")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id="ses_council",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(gold=10, supplies=3),
        characters=characters,
    )
    llm = CouncilLLM(42)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, pack=pack), llm


def test_council_speaks_within_round_limit(tmp_path):
    """3 人组里 2 个 AI 成员 × 2 轮 = 最多 4 次发言。"""
    loop, llm = build(tmp_path)

    asyncio.run(loop.run(1))

    assert llm.said, "组内应当有发言"
    assert len(llm.said) <= 4, f"超过轮次上限：{len(llm.said)}"
    assert {cid for cid, _ in llm.said} == {"chr_1", "chr_2"}


def test_council_transcript_is_private_to_group(tmp_path):
    """商议内容只进组内 briefs，组外看不到。"""
    loop, llm = build(tmp_path)

    asyncio.run(loop.run(1))

    wolves = [briefs for cid, briefs in llm.seen if cid in ("chr_1", "chr_2")]
    villager = [briefs for cid, briefs in llm.seen if cid == "chr_3"]

    assert any("商议" in line for briefs in wolves for line in briefs), "组内应当看到商议"
    assert villager, "村民也应当行动"
    assert all("商议" not in line for line in villager[0]), "组外不该看到商议"


def test_council_respects_time_budget(tmp_path):
    """`budget_s` 到点就停——这是防止「模型啰嗦拖死一回合」的兜底。"""
    flow = FLOW.replace("rounds: 2", "rounds: 99").replace(
        "chained: false", "budget_s: 0.001"
    )
    loop, llm = build(tmp_path, flow)

    asyncio.run(loop.run(1))

    assert len(llm.said) < 99 * 2, "时间预算应当先到"


def test_council_falls_back_to_vote_without_speaker(tmp_path):
    """模型没有 `council_say` 时退回一轮投票，不让整段流程空转。"""
    flow = """
phases:
  - id: scene
    kind: narration
  - id: act
    kind: action
    groups:
      - { by: camp, value: wolf, name: 狼, mode: council, rounds: 2 }
"""
    loop, llm = build(tmp_path, flow)
    # 模拟「只有 vote、不会说话」的模型
    llm.council_say = None  # type: ignore[assignment]

    rec = asyncio.run(loop.run(1))[0]

    assert rec.votes, "退回投票后仍应产生共议票"
