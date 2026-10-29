"""标准板子的两条硬规则：**遗言** 与 **平票 PK 重投**。

这两条在标准规则里是明确的，之前都缺：

| 规则 | 之前 | 现在 |
|---|---|---|
| 出局留遗言 | ❌ 出局即静默 | ✅ `apply.last_words: true` → 一句公开发言写进 `deaths[].last_words` |
| 平票处理 | ⚠️ `tie: runoff` 只返回「无人当选」，等于一轮就放弃 | ✅ `collective.rounds: 2` → 只让平票者再被投一轮，再平票才无人出局 |

遗言的意义不只是「多说一句话」：临死前可以指认、可以交代身份、**也可以撒谎**——
它是信息战的一轮，删掉就等于少了一整段博弈。
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
  - id: vote
    kind: collective
    rule: plurality
    tie: runoff
    eligible: alive
    rounds: 2
  - id: exile
    kind: apply
    target: collective_result
    effect: eliminate
    last_words: true
"""


class ScriptedLLM(MockLLM):
    """按固定计划投票，用来制造「一边倒」与「平票」两种局面。"""

    def __init__(self, seed, plan: dict[str, str]) -> None:
        super().__init__(seed)
        self.plan = plan

    def vote(self, actor_id, card, state, turn, candidates=None):
        return self.plan.get(actor_id)


def build(tmp_path: Path, plan: dict[str, str]) -> tuple[GameLoop, ScriptedLLM]:
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    (base / "turn_flow.yaml").write_text(FLOW, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    cards, characters = {}, {}
    for cid in ("chr_1", "chr_2", "chr_3"):
        cards[cid] = make_card(cid=cid, profession="村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10)

    state = GameState(
        session_id="ses_pk",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(),
        characters=characters,
    )
    llm = ScriptedLLM(42, plan)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, pack=pack), llm


def test_eliminated_player_leaves_last_words(tmp_path):
    """两人都投 chr_3 → 出局 → 留下遗言。"""
    loop, _ = build(tmp_path, {"chr_1": "chr_3", "chr_2": "chr_3", "chr_3": "chr_1"})

    rec = asyncio.run(loop.run(1))[0]

    assert rec.deaths, "应当有人出局"
    victim = rec.deaths[0]
    assert victim["id"] == "chr_3"
    assert victim.get("last_words"), "出局者应当留下遗言"


def test_no_last_words_when_switch_off(tmp_path):
    """不声明 `last_words` 时不产生遗言条目（保持旧行为）。"""
    flow = FLOW.replace("    last_words: true\n", "")
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    (base / "turn_flow.yaml").write_text(flow, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    cards, characters = {}, {}
    for cid in ("chr_1", "chr_2", "chr_3"):
        cards[cid] = make_card(cid=cid, profession="村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10)
    state = GameState(
        session_id="ses_off",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(),
        characters=characters,
    )
    loop = GameLoop(
        Rules(ROOT),
        cards,
        state,
        ScriptedLLM(42, {"chr_1": "chr_3", "chr_2": "chr_3"}),
        seed=42,
        pack=pack,
    )

    rec = asyncio.run(loop.run(1))[0]

    assert rec.deaths
    assert "last_words" not in rec.deaths[0]


def test_tie_triggers_revote_among_leaders(tmp_path):
    """三人各投一人 → 平票 → 第二轮只投这三人 → 仍平票 → 无人出局。"""
    loop, _ = build(
        tmp_path, {"chr_1": "chr_2", "chr_2": "chr_3", "chr_3": "chr_1"}
    )

    rec = asyncio.run(loop.run(1))[0]

    rounds = {vote["round"] for vote in rec.votes if vote.get("kind") != "consensus"}
    assert rounds == {1, 2}, f"平票应当重投一轮，实际轮次 {rounds}"
    assert rec.vote_result.get("rounds") == 2
    assert rec.deaths == [], "两轮都平票 → 本轮无人出局"
