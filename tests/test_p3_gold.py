"""P3-T14：金币与补给改角色级，`party` 降级为团队公共仓。

为什么不是全废掉 party：跑团里「凑钱办事」（雇人、贿赂门房、买船票）
是集体行为，需要一只公共的钱袋；但**每个人的钱应该是自己的**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trpg.engine.state_merge import DeltaValidationError, apply_delta
from trpg.game import build_cards, build_state
from trpg.models import DeltaChange, DeltaOp, StateDelta
from trpg.rules.loader import Rules
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]


def _state(players: int = 2):
    pack = load_world("longzu", ROOT)
    cards = build_cards(players, pack, seed="1")
    return build_state(pack, cards), cards


def _delta(path: str, op: DeltaOp, value) -> StateDelta:
    return StateDelta(turn=1, changes=[DeltaChange(path=path, op=op, value=value)])


def test_each_character_gets_a_share():
    state, cards = _state(2)
    assert all(rt.gold == 15 for rt in state.characters.values()), "30 金按 2 人均分"
    assert all(rt.supplies == 2 for rt in state.characters.values()), "4 补给按 2 人均分"
    assert state.party.gold == 0, "均分没有余数，公共仓为 0"
    assert len(cards) == 2


def test_remainder_stays_in_public_purse():
    state, _ = _state(4)
    assert all(rt.gold == 7 for rt in state.characters.values())
    assert state.party.gold == 2, "30 ÷ 4 = 7 余 2，余数进公共仓"


def test_character_gold_is_writable_via_delta():
    state, cards = _state(2)
    cid = next(iter(cards))
    new = apply_delta(state, _delta(f"characters.{cid}.gold", DeltaOp.sub, 5), cards)
    assert new.characters[cid].gold == 10
    assert new.party.gold == 0, "扣个人的钱不动公共仓"


def test_character_gold_cannot_go_negative():
    """钱不能变成负数：超支整包拒绝（不是静默夹到 0，否则等于凭空花掉）。

    这条曾经真的漏了：`GameState.model_validate(raw)` 不会重新校验已构造的
    `CharacterRuntime` 实例，所以 `Field(ge=0)` 兜不住，扣 999 会得到 -984。
    """
    state, cards = _state(2)
    cid = next(iter(cards))
    with pytest.raises(DeltaValidationError):
        apply_delta(state, _delta(f"characters.{cid}.gold", DeltaOp.sub, 999), cards)


def test_party_purse_still_works():
    state, cards = _state(2)
    new = apply_delta(state, _delta("party.gold", DeltaOp.add, 7), cards)
    assert new.party.gold == 7, "公共仓仍可加钱（凑钱办事）"


def test_snapshot_exposes_per_character_gold():
    from fastapi.testclient import TestClient

    from trpg.api.app import app

    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"world": "longzu", "players": 2, "provider": "mock", "seed": "3"},
        )
        assert created.status_code == 200, created.text
        char = created.json()["characters"][0]
    assert char["gold"] >= 0 and "supplies" in char, "前端要能看到每个人的钱"
