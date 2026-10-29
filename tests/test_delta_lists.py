"""GM 输出容错：列表字段被写成数组时不应整包回滚。"""

import pytest

from trpg.engine.state_merge import DeltaValidationError, apply_delta
from trpg.models import CharacterRuntime, DeltaChange, GameState, Party, Scene, StateDelta, WorldRef
from trpg.models.enums import DeltaOp
from support.builders import make_card


def _state() -> GameState:
    card = make_card()
    return GameState(
        session_id="ses_list",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_test01": CharacterRuntime(hp=card.ext.hp, sanity=8)},
    )


def _delta(value) -> StateDelta:
    return StateDelta(
        turn=1,
        changes=[
            DeltaChange(
                path="characters.chr_test01.conditions", op=DeltaOp.add, value=value
            )
        ],
    )


def test_add_scalar_to_list():
    new = apply_delta(_state(), _delta("exposed"))
    assert new.characters["chr_test01"].conditions == ["exposed"]


def test_add_list_is_flattened():
    """GM 写成 ["exposed"] 时，不应产生嵌套列表。"""
    new = apply_delta(_state(), _delta(["exposed"]))
    assert new.characters["chr_test01"].conditions == ["exposed"]


def test_add_multi_list_flattened():
    new = apply_delta(_state(), _delta(["a", "b"]))
    assert new.characters["chr_test01"].conditions == ["a", "b"]


def test_append_accumulates():
    state = _state()
    state.characters["chr_test01"].conditions = ["tired"]
    new = apply_delta(state, _delta(["exposed"]))
    assert new.characters["chr_test01"].conditions == ["tired", "exposed"]
