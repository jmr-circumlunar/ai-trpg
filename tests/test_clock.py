"""时间系统：只能前进、有档位上下限、跨天正确，且 GM 不申报时会兜底推进。"""

import asyncio
from pathlib import Path

import pytest

from trpg.engine.loop import GameLoop
from trpg.engine.state_merge import DeltaValidationError, apply_delta
from trpg.llm import MockLLM
from trpg.models import CharacterRuntime, Clock, DeltaChange, GameState, Party, Scene, StateDelta, WorldRef
from trpg.models.enums import DeltaOp
from trpg.rules import clock as clock_mod
from trpg.rules.loader import Rules
from trpg.testing import make_card

ROOT = Path(__file__).resolve().parents[1]
CFG = Rules(ROOT).clock


# ── 纯函数 ────────────────────────────────────────────────
def test_minutes_roundtrip():
    total = clock_mod.to_minutes(2, 13, 40)
    assert clock_mod.from_minutes(total) == (2, 13, 40)


def test_advance_crosses_midnight():
    assert clock_mod.advance(1, 23, 30, 60) == (2, 0, 30)


def test_advance_rejects_negative():
    with pytest.raises(ValueError):
        clock_mod.advance(1, 10, 0, -5)


def test_phase_lookup():
    phases = CFG["phases"]
    assert clock_mod.phase_of(9, phases) == "morning"
    assert clock_mod.phase_of(21, phases) == "night"
    assert clock_mod.phase_of(2, phases) == "deep_night"  # 跨午夜区间
    assert clock_mod.phase_of(6, phases) == "dawn"


def test_skill_mod_by_phase():
    phases = CFG["phases"]
    assert clock_mod.skill_mod("night", "潜行", phases) == -2
    assert clock_mod.skill_mod("night", "交涉", phases) == 2
    assert clock_mod.skill_mod("morning", "潜行", phases) == 0
    assert clock_mod.skill_mod("night", None, phases) == 0


# ── StateDelta ───────────────────────────────────────────
def _state() -> GameState:
    card = make_card()
    return GameState(
        session_id="ses_clock",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=2),
        characters={"chr_test01": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )


def _advance_delta(minutes: int, times: int = 1) -> StateDelta:
    return StateDelta(
        turn=1,
        changes=[
            DeltaChange(path="clock.advance", op=DeltaOp.add, value=minutes)
            for _ in range(times)
        ],
    )


def test_advance_ok():
    new = apply_delta(_state(), _advance_delta(30), None, CFG)
    assert (new.clock.hour, new.clock.minute) == (20, 30)


def test_advance_cross_day():
    state = _state()
    state.clock = Clock(day=1, hour=23, minute=50)
    new = apply_delta(state, _advance_delta(30), None, CFG)
    assert (new.clock.day, new.clock.hour, new.clock.minute) == (2, 0, 20)


def test_advance_too_large_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(_state(), _advance_delta(9999), None, CFG)


def test_advance_too_small_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(_state(), _advance_delta(1), None, CFG)


def test_cumulative_over_limit_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(_state(), _advance_delta(300, times=2), None, CFG)


def test_direct_hour_set_rejected():
    delta = StateDelta(
        turn=1,
        changes=[DeltaChange(path="clock.hour", op=DeltaOp.set, value=3)],
    )
    with pytest.raises(DeltaValidationError):
        apply_delta(_state(), delta, None, CFG)


def test_weather_still_allowed():
    delta = StateDelta(
        turn=1,
        changes=[DeltaChange(path="clock.weather", op=DeltaOp.set, value="rain")],
    )
    new = apply_delta(_state(), delta, None, CFG)
    assert new.clock.weather == "rain"


# ── 回合兜底 ──────────────────────────────────────────────
def test_loop_advances_time_even_if_gm_forgets():
    rules = Rules(ROOT)
    cards = {"chr_p1": make_card(), "chr_p2": make_card(profession="战士")}
    state = _state()
    state.characters = {
        cid: CharacterRuntime(hp=c.ext.hp, sanity=8) for cid, c in cards.items()
    }
    loop = GameLoop(rules, cards, state, MockLLM(42), seed=42)

    before = clock_mod.to_minutes(state.clock.day, state.clock.hour, state.clock.minute)
    asyncio.run(loop.run(1))
    after = clock_mod.to_minutes(
        loop.state.clock.day, loop.state.clock.hour, loop.state.clock.minute
    )
    assert after > before, "一回合后时间必须前进"
