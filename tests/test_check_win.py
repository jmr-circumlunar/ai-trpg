"""B57 阶段边界判胜（C58）：check 阶段 → `state.winner` → `break_if` 提前结束。

这条链是狼人杀「一方全灭即局终」的底座：
① `evaluate_win()` 判出结果 → 写 `state.winner`；
② `win_decided` 谓词因此为真；
③ `break_if` 命中 → 本回合后续阶段进 `TurnRecord.skipped`，不再执行。
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
from trpg.models.enums import Position  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402

BLANK = ROOT / "worlds" / "_blank"

FLOW_WITH_CHECK = """
phases:
  - id: scene
    kind: narration
  - id: check_win
    kind: check
    break_if: win_decided
  - id: act
    kind: action
  - id: commit
    kind: commit
"""

WIN_VILLAGE_GONE = """
win_condition:
  kind: camp_eliminated
  camp: village
"""


def build(tmp_path: Path, camps: list[tuple[str, bool]]) -> GameLoop:
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    text = (base / "world.yaml").read_text(encoding="utf-8")
    (base / "world.yaml").write_text(text + WIN_VILLAGE_GONE, encoding="utf-8")
    (base / "turn_flow.yaml").write_text(FLOW_WITH_CHECK, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    cards = {}
    characters = {}
    for index, (camp, dead) in enumerate(camps, start=1):
        cid = f"chr_{index}"
        cards[cid] = make_card(cid=cid, profession="盗贼")
        characters[cid] = CharacterRuntime(
            hp=10,
            sanity=10,
            camp=camp,
            position=Position.dead if dead else Position.scene,
        )

    state = GameState(
        session_id="ses_check",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(gold=10, supplies=3),
        characters=characters,
    )
    return GameLoop(Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack)


def test_check_stage_ends_turn_early(tmp_path):
    loop = build(tmp_path, [("wolf", False), ("village", True)])

    rec = asyncio.run(loop.run(1))[0]

    assert loop.state.winner == "wolf"
    assert rec.vote_result["win"]["kind"] == "camp_eliminated"
    assert "check_win" in rec.order
    assert "act" not in rec.order, "胜负已定后不应再让角色行动"
    assert any(item["id"] == "act" and "break_if" in item["reason"] for item in rec.skipped)


def test_no_verdict_when_both_camps_alive(tmp_path):
    loop = build(tmp_path, [("wolf", False), ("village", False)])

    rec = asyncio.run(loop.run(1))[0]

    assert loop.state.winner == ""
    assert "act" in rec.order


def test_all_dead_falls_back_to_legacy_rule(tmp_path):
    """没配 win_condition 的世界仍走旧语义：全员出局即终局。"""
    loop = build(tmp_path, [("wolf", True), ("village", True)])
    loop.pack.win_condition = {}

    asyncio.run(loop.run(1))

    assert loop.state.winner == "all"
