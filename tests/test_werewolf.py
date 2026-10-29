"""B58 狼人杀世界包：0 行代码跑通一局（阶段一的验收载体）。

这里断言的不是「好不好玩」，而是「框架的通用能力是否真的够用」：
昼夜两套流程、夜间私密、集体投票、放逐、阵营胜负，全部只由 YAML 声明驱动。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.ending import evaluate_win  # noqa: E402
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

WOLVES = 3
VILLAGERS = 5


class RecordingLLM(MockLLM):
    def __init__(self, seed: int | str) -> None:
        super().__init__(seed)
        self.seen: list[tuple[str, list[str]]] = []

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.seen.append((actor_id, list(previous or [])))
        return super().player_action(actor_id, card, state, turn, previous)


def build(tmp_path: Path, turn_flow_override: str | None = None):
    pack = load_world("werewolf", ROOT)
    cards = {}
    characters = {}
    for index in range(WOLVES + VILLAGERS):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index < WOLVES else "village"
        cards[cid] = make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id="ses_werewolf",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    llm = RecordingLLM(42)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, pack=pack), llm


def test_pack_declares_two_camps():
    pack = load_world("werewolf", ROOT)
    camps = {item["id"]: item.get("camp") for item in pack.professions}

    assert camps["狼人"] == "wolf"
    assert camps["村民"] == "village"


def test_pack_uses_side_elimination():
    """A1/A2：屠边——狼全灭 / 神职全灭 / 村民全灭，三档任一命中即终局。"""
    pack = load_world("werewolf", ROOT)

    rules = pack.win_condition["any_of"]
    assert len(rules) == 3
    assert [rule["kind"] for rule in rules] == [
        "camp_eliminated",
        "group_eliminated",
        "group_eliminated",
    ]


def test_one_turn_runs_all_declared_phases(tmp_path):
    loop, _ = build(tmp_path)

    rec = asyncio.run(loop.run(1))[0]

    assert rec.order == [
        "night",
        "night_skill",
        "night_kill",
        "dawn",
        "check_night",
        "discuss",
        "day_skill",
        "check_burst",
        "vote",
        "exile",
        "check_vote",
    ]


def test_night_consensus_eliminates_someone(tmp_path):
    """夜里的决议要真的落到人身上——狼人杀没有「夜杀」就不成立。

    这条走的是**通用能力**：组内共议（`mode: consensus`）→ `apply` 阶段
    `target: consensus_result` → `effect: eliminate`，框架里没有任何世界专属概念。
    """
    loop, _ = build(tmp_path)

    rec = asyncio.run(loop.run(1))[0]

    consensus = rec.vote_result.get("consensus") or {}
    assert consensus.get("winner"), "夜里应当形成共识决议"
    assert rec.deaths, "夜里的决议要让人出局"
    dead = rec.deaths[0]["id"]
    assert dead == consensus["winner"]
    assert loop.state.characters[dead].position == Position.dead
    # `targets: outside` 的意义：狼共议选的是**组外**的人。
    # 不配这个参数时会杀队友——真实模型跑出来过一次（夜里把 chr_p2 这只狼杀了）。
    assert loop.state.characters[dead].camp != "wolf", "狼不该在夜里杀自己人"


def test_night_is_private_and_vote_is_recorded(tmp_path):
    loop, llm = build(tmp_path)

    rec = asyncio.run(loop.run(1))[0]

    assert rec.votes, "投票与共议都应留下票型"
    assert rec.vote_result.get("counts"), "计票结果可查"
    assert any("共同决定" in line for _, briefs in llm.seen for line in briefs), (
        "狼组共议的决议要能被组员看到"
    )
    villager_briefs = [b for cid, b in llm.seen if cid in {f"chr_{i}" for i in range(4, 9)}]
    assert villager_briefs
    assert all(
        "共同决定" not in line for briefs in villager_briefs for line in briefs
    ), "夜间决议不能泄露给非狼人"


def test_judge_is_off_so_no_outcomes(tmp_path):
    loop, _ = build(tmp_path)

    rec = asyncio.run(loop.run(1))[0]

    assert rec.outcomes == [], "狼人杀不掷骰，不应有判定结论"


def test_final_verdict_when_wolves_are_dead(tmp_path):
    """狼全灭 → 村民胜（不依赖任何 LLM 行为）。"""
    loop, _ = build(tmp_path)
    for cid in list(loop.state.characters)[:WOLVES]:
        loop.state.characters[cid].position = Position.dead

    result = evaluate_win(loop.state, loop.pack)

    assert result is not None
    assert result.kind == "camp_eliminated"
    assert result.winner == "village"


def test_cli_runs_ten_turns(tmp_path):
    """出口判据 1：`run.py --world werewolf --provider mock --turns 10` 跑完整局。"""
    result = subprocess.run(
        [
            sys.executable,
            "run.py",
            "run",
            "--world",
            "werewolf",
            "--provider",
            "mock",
            "--turns",
            "10",
            "--players",
            "8",
            "--db",
            str(tmp_path / "werewolf.db"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "回合数      10" in result.stdout or "回合数" in result.stdout


def test_dawn_announces_deaths(tmp_path):
    """D5：`broadcast.source: eliminated_this_turn` 真的会公布出局名单。"""
    loop, _ = build(tmp_path)
    events: list[tuple[str, dict]] = []

    asyncio.run(loop.step(progress=lambda event, data: events.append((event, data))))

    announces = [data for name, data in events if name == "broadcast"]
    assert announces, "天亮应当公布出局名单"
    assert announces[0]["names"], "公布里应当有出局者的名字"
