"""B53 判定可关（C55）：`systems.judge: false` 时不掷骰、不产生判定结果。

「不判定」是世界属性，「怎么判」是框架职责。关掉判定后：
- 行动照常发生（`thoughts` / `actions` 都有值）；
- `TurnRecord.outcomes` 为空列表（不是 None，也不是「自动成功」）；
- 时间兜底仍能拿到 `action_type`（所以内部仍有一个 auto 占位结果）。
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
  - id: commit
    kind: commit
"""

JUDGE_OFF = """
systems:
  judge: { enabled: false }
"""

JUDGE_ON = """
systems:
  judge: { enabled: true }
"""


def build(tmp_path: Path, systems: str, players: int = 2) -> GameLoop:
    base = tmp_path / "worlds" / "wtest"
    shutil.copytree(BLANK, base)
    (base / "systems.yaml").write_text(systems, encoding="utf-8")
    (base / "turn_flow.yaml").write_text(FLOW, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    cards = {}
    for i in range(players):
        cid = f"chr_p{i + 1}"
        cards[cid] = make_card(cid=cid, profession=["盗贼", "战士"][i % 2])

    state = GameState(
        session_id="ses_nojudge",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    return GameLoop(Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack)


def test_judge_off_produces_no_outcomes(tmp_path):
    loop = build(tmp_path, JUDGE_OFF)

    rec = asyncio.run(loop.run(1))[0]

    assert rec.outcomes == [], "关判定后不应产生任何判定结论"


def test_judge_off_still_produces_actions(tmp_path):
    loop = build(tmp_path, JUDGE_OFF)

    rec = asyncio.run(loop.run(1))[0]

    assert rec.thoughts, "行动照常发生：思考要留下"
    assert rec.actions, "行动照常发生：行动要留下"
    assert len(rec.thoughts) == len(rec.actions)


def test_judge_off_keeps_time_moving(tmp_path):
    """时间兜底依赖 action_type，占位结果必须带得动它。"""
    loop = build(tmp_path, JUDGE_OFF)
    before = (loop.state.clock.day, loop.state.clock.hour, loop.state.clock.minute)

    asyncio.run(loop.run(1))

    after = (loop.state.clock.day, loop.state.clock.hour, loop.state.clock.minute)
    assert after != before, "没有判定也不能让时间停住"


def test_judge_on_produces_outcomes(tmp_path):
    loop = build(tmp_path, JUDGE_ON)

    rec = asyncio.run(loop.run(1))[0]

    assert rec.outcomes, "判定开启时必须有结论"
    assert all(" vs " in text for text in rec.outcomes)
