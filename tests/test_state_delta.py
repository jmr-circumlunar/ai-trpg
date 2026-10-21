import pytest

from trpg.engine.state_merge import DeltaValidationError, apply_delta
from trpg.models import DeltaChange, StateDelta
from trpg.models.enums import DeltaOp
from trpg.testing import make_card, make_state

CARDS = {"chr_test01": make_card()}


def delta(*changes, turn: int = 1) -> StateDelta:
    return StateDelta(turn=turn, changes=[DeltaChange(**c) for c in changes])


def test_normal_hp_sub():
    state = make_state()
    new = apply_delta(state, delta({"path": "characters.chr_test01.hp", "op": DeltaOp.sub, "value": 2}), CARDS)
    assert new.characters["chr_test01"].hp == 9
    assert new.version == state.version + 1


def test_hp_below_zero_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "characters.chr_test01.hp", "op": DeltaOp.sub, "value": 99}), CARDS)


def test_hp_drop_over_60pct_rejected():
    # hp_max=11 -> 允许最大变化 6.6，掉 8 应被拒
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "characters.chr_test01.hp", "op": DeltaOp.sub, "value": 8}), CARDS)


def test_tension_jump_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "tension", "op": DeltaOp.add, "value": 30}), CARDS)


def test_version_not_writable():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "version", "op": DeltaOp.set, "value": 99}), CARDS)


def test_world_not_writable():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "world.era", "op": DeltaOp.set, "value": "modern"}), CARDS)


def test_stage_cannot_skip():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "stage", "op": DeltaOp.add, "value": 2}), CARDS)


def test_stage_plus_one_ok():
    state = make_state()
    new = apply_delta(state, delta({"path": "stage", "op": DeltaOp.add, "value": 1}), CARDS)
    assert new.stage == state.stage + 1


def test_gold_negative_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "party.gold", "op": DeltaOp.sub, "value": 100}), CARDS)


def test_flag_add_ok():
    new = apply_delta(make_state(), delta({"path": "party.flags", "op": DeltaOp.add, "value": "heard_bell"}), CARDS)
    assert "heard_bell" in new.party.flags


def test_failed_delta_does_not_mutate_original():
    state = make_state()
    before = state.model_dump()
    with pytest.raises(DeltaValidationError):
        apply_delta(state, delta({"path": "party.gold", "op": DeltaOp.sub, "value": 100}), CARDS)
    assert state.model_dump() == before


def test_scene_move_ok():
    new = apply_delta(
        make_state(),
        delta({"path": "scene.location_id", "op": DeltaOp.set, "value": "loc_b"}),
        CARDS,
    )
    assert new.scene.location_id == "loc_b"
