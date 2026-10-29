"""标准板的两条节奏规则：**首夜限定遗言**（A4）与**发言从死者下一位开始**（A3）。

以及顺手修掉的 D4：`turn_flow.yaml` 顶层 `declare:` 块以前「写了不生效」——
`manifest` 注释承诺「action 阶段缺省继承 declare.order」，解析器却只读 `from` / `phases`。

两条规则都落在「代码算顺序、世界包只声明」这一条线上：
框架不认识「几号位」「第几天」，只按世界包给的座位表绕圈。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.engine.pipeline import PipelineError, parse_turn_flow  # noqa: E402
from trpg.models import (  # noqa: E402
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.rules.loader import Rules  # noqa: E402
from trpg.rulespack.validate import validate_declarations  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402

BLANK = ROOT / "worlds" / "_blank"

FLOW_FIRST_TURN = """
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
    last_words_when: first_turn
"""


class TailVoter(MockLLM):
    """所有票都投「候选里 id 最大的那个」——每回合稳定淘汰一人，便于观察节奏。"""

    def vote(self, actor_id, card, state, turn, candidates=None):
        return sorted(candidates or [])[-1]


def build_world(tmp_path: Path, flow: str = FLOW_FIRST_TURN, players: int = 4):
    """临时世界 + 返回 (loop, llm)。"""
    base = tmp_path / "worlds" / "wtest"
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(BLANK, base)
    (base / "turn_flow.yaml").write_text(flow, encoding="utf-8")

    pack = load_world("wtest", tmp_path)
    cards, characters = {}, {}
    for index in range(1, players + 1):
        cid = f"chr_{index}"
        cards[cid] = make_card(cid=cid, profession="村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10)

    state = GameState(
        session_id="ses_speech",
        world=WorldRef(world_id="wtest", era="base"),
        scene=Scene(location_id="loc_01", region="main"),
        party=Party(),
        characters=characters,
    )
    return GameLoop(Rules(ROOT), cards, state, TailVoter(42), seed=42, pack=pack)


class StubSpec:
    """只带参数的行动阶段规格替身（`_order_actors` 只读参数）。"""

    def __init__(self, **params) -> None:
        self._params = params

    def param(self, name, default=None):
        return self._params.get(name, default)


def stub_ctx(deaths: list[dict] | None = None):
    return types.SimpleNamespace(rec=types.SimpleNamespace(deaths=list(deaths or [])))


# ── A4：首夜限定遗言 ───────────────────────────────────────


def test_last_words_only_on_first_turn(tmp_path):
    """第 1 回合出局者留遗言；第 2 回合起不再留（标准板：夜间从第二夜起静默）。"""
    loop = build_world(tmp_path)

    first, second = asyncio.run(loop.run(2))

    assert first.deaths and first.deaths[0].get("last_words"), "首回合出局应当有遗言"
    assert second.deaths, "第二回合应当还有人出局"
    assert "last_words" not in second.deaths[0], "第二回合起不该再有遗言"


def test_last_words_always_by_default(tmp_path):
    """不写 `last_words_when` 时保持旧行为：每次都留。"""
    flow = FLOW_FIRST_TURN.replace("    last_words_when: first_turn\n", "")
    loop = build_world(tmp_path, flow=flow)

    first, second = asyncio.run(loop.run(2))

    assert first.deaths[0].get("last_words")
    assert second.deaths[0].get("last_words")


# ── A3：发言从死者下一位开始 ─────────────────────────────────

SEATS = ["chr_1", "chr_2", "chr_3", "chr_4", "chr_5"]


def test_no_death_keeps_seat_order(tmp_path):
    loop = build_world(tmp_path)

    assert loop._rotate_from_last_death(SEATS, "forward", stub_ctx()) == SEATS


def test_forward_starts_after_victim(tmp_path):
    loop = build_world(tmp_path)

    rotated = loop._rotate_from_last_death(
        SEATS, "forward", stub_ctx([{"id": "chr_2"}])
    )

    assert rotated == ["chr_3", "chr_4", "chr_5", "chr_1", "chr_2"]


def test_forward_wraps_from_last_seat(tmp_path):
    loop = build_world(tmp_path)

    rotated = loop._rotate_from_last_death(
        SEATS, "forward", stub_ctx([{"id": "chr_5"}])
    )

    assert rotated == ["chr_1", "chr_2", "chr_3", "chr_4", "chr_5"]


def test_day_parity_reverses_on_even_day(tmp_path):
    """双数天反着数：死者 chr_3 的下一位（逆向）是 chr_2。"""
    loop = build_world(tmp_path)
    loop.state.clock.day = 2

    rotated = loop._rotate_from_last_death(
        SEATS, "by_day_parity", stub_ctx([{"id": "chr_3"}])
    )

    assert rotated == ["chr_2", "chr_1", "chr_5", "chr_4", "chr_3"]


def test_day_parity_forward_on_odd_day(tmp_path):
    loop = build_world(tmp_path)
    loop.state.clock.day = 1

    rotated = loop._rotate_from_last_death(
        SEATS, "by_day_parity", stub_ctx([{"id": "chr_3"}])
    )

    assert rotated[0] == "chr_4"


def test_unknown_victim_does_not_guess(tmp_path):
    """死者不在座位表里（NPC / 未知 id）→ 从首位开始，不瞎猜。"""
    loop = build_world(tmp_path)

    rotated = loop._rotate_from_last_death(
        SEATS, "forward", stub_ctx([{"id": "npc_ghost"}])
    )

    assert rotated == SEATS


def test_victim_from_settle_form(tmp_path):
    """濒死结算写的键是 `char_id`（另一种形态）：必须认，否则漏一个人。"""
    loop = build_world(tmp_path)

    rotated = loop._rotate_from_last_death(
        SEATS, "forward", stub_ctx([{"char_id": "chr_2"}])
    )

    assert rotated[0] == "chr_3"


def test_order_actors_uses_derived_seats(tmp_path):
    """没写 `fixed_sequence` 时，座位表 = 角色 id 升序（建卡顺序 = 入座顺序）。"""
    loop = build_world(tmp_path, players=len(SEATS))
    actors = [(cid, loop.cards[cid]) for cid in reversed(SEATS)]
    spec = StubSpec(order="fixed", start_after="last_death", direction="forward")

    ordered = loop._order_actors(actors, spec, stub_ctx([{"id": "chr_2"}]))

    assert [cid for cid, _ in ordered] == ["chr_3", "chr_4", "chr_5", "chr_1", "chr_2"]


def test_order_actors_explicit_sequence_wins(tmp_path):
    """世界包显式给了 `fixed_sequence` 就以它为准。"""
    loop = build_world(tmp_path, players=len(SEATS))
    actors = [(cid, loop.cards[cid]) for cid in SEATS]
    spec = StubSpec(order="fixed", fixed_sequence=["chr_5", "chr_4", "chr_3", "chr_2", "chr_1"])

    ordered = loop._order_actors(actors, spec, stub_ctx())

    assert [cid for cid, _ in ordered] == ["chr_5", "chr_4", "chr_3", "chr_2", "chr_1"]


# ── D4：`declare` 继承 ─────────────────────────────────────


def test_declare_is_inherited_by_action_phases():
    phases = parse_turn_flow(
        {
            "from": "minimal",
            "declare": {"order": "fixed", "visibility": "public"},
            "phases": [{"id": "talk", "kind": "action"}],
        }
    )

    assert phases[0].param("order") == "fixed"
    assert phases[0].param("visibility") == "public"


def test_phase_value_overrides_declare():
    phases = parse_turn_flow(
        {
            "declare": {"order": "fixed"},
            "phases": [{"id": "talk", "kind": "action", "order": "dex"}],
        }
    )

    assert phases[0].param("order") == "dex"


def test_declare_only_affects_action_phases():
    phases = parse_turn_flow(
        {
            "declare": {"order": "fixed"},
            "phases": [{"id": "scene", "kind": "narration"}],
        }
    )

    assert "order" not in phases[0].params


def test_declare_unknown_key_raises():
    with pytest.raises(PipelineError):
        parse_turn_flow({"declare": {"sequence": ["chr_1"]}})


def test_declare_invalid_value_raises():
    with pytest.raises(PipelineError):
        parse_turn_flow({"declare": {"order": "chaos"}})


def test_validate_reports_declare_problem(tmp_path):
    base = tmp_path / "worlds" / "wdecl"
    base.mkdir(parents=True)
    (base / "world.yaml").write_text("world_id: wdecl\nname: 测试\n", encoding="utf-8")
    (base / "turn_flow.yaml").write_text(
        "declare:\n  order: chaos\nphases: []\n", encoding="utf-8"
    )

    errors = validate_declarations(base)

    assert any("declare" in e and "chaos" in e for e in errors), errors


# ── 集成：狼人杀白天的发言确实从夜死者下一位开始 ──────────────


def build_werewolf():
    pack = load_world("werewolf", ROOT)
    cards, characters = {}, {}
    for index in range(8):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index < 3 else "village"
        cards[cid] = make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")
        characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)

    state = GameState(
        session_id="ses_wolf_speech",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    return GameLoop(Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack)


def test_werewolf_speech_starts_after_night_victim():
    """真实世界包：白天的第一位发言者 = 夜死者座位的下一位（第 1 天正向）。"""
    loop = build_werewolf()

    rec = asyncio.run(loop.run(1))[0]

    victim = str((rec.deaths[0].get("id") or "") if rec.deaths else "")
    assert victim, "第一夜应当有人出局"
    assert rec.actors, "白天应当有人发言"

    seats = sorted(f"chr_{index}" for index in range(1, 9))
    expected = seats[(seats.index(victim) + 1) % len(seats)]
    assert rec.actors[0] == expected
