"""B54 阵营与隐藏身份（C56）：`camp` 是局设定，模型不可写。

三条硬约束：
① `camp` / `camp_revealed` 有默认值（旧快照能反序列化）；
② 不在 StateDelta 白名单（模型改不了自己的阵营）；
③ 世界没配 `secrets` 时行为完全不变（`ironvale` 不受影响）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_state  # noqa: E402
from trpg.engine.state_merge import (  # noqa: E402
    DeltaValidationError,
    _path_allowed,
    apply_delta,
)
from trpg.models import DeltaChange, DeltaOp, StateDelta  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402


def test_camp_defaults_are_empty():
    rt = next(iter(make_state().characters.values()))

    assert rt.camp == ""
    assert rt.camp_revealed is False


def test_camp_paths_are_not_writable():
    assert not _path_allowed("characters.chr_1.camp")
    assert not _path_allowed("characters.chr_1.camp_revealed")


def test_delta_writing_camp_is_rejected():
    state = make_state()
    cid = next(iter(state.characters))
    delta = StateDelta(
        turn=1,
        changes=[
            DeltaChange(
                path=f"characters.{cid}.camp", op=DeltaOp.set, value="wolf"
            )
        ],
    )

    with pytest.raises(DeltaValidationError):
        apply_delta(state, delta, {})


def test_ironvale_has_no_secrets_config():
    pack = load_world("ironvale", ROOT)

    assert pack.secrets == {}
    assert pack.win_condition == {}
