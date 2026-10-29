"""生死与「能不能动」是**两件事**。

## 三个概念，别混

| 函数 | 判据 | 用途 |
|---|---|---|
| `is_eliminated` | `position == dead` | 生死的**唯一**真相 |
| `is_alive` | `not is_eliminated`（**与 hp 无关**） | 阵营人数、全灭判定、阵亡名单 |
| `can_act` | 未出局 **且** 能动能说 | 行动名单、投票资格、能否发言 |

## 为什么 `is_alive` 不能看 hp

`hp == 0` 在本项目里是**濒死**，不是死亡：`conditions` 加 `dying`、记 `dying_since`，
`position` 仍是 `scene`（人还在场上，宽限回合内可救）。

曾经把 `alive_ids` / `_alive_camps` 写成 `hp > 0`，后果是**濒死的队友被踢出阵营人数**——
一个阵营全员濒死但没人真正出局时，`camp_superiority` 会判成「全灭」。

## 为什么 `can_act` 要留豁免口子

有些世界有「清零不死」的技能（不死之身 / 狂化 / 刚复活还在硬撑）。
框架不替世界决定「归零 = 失能」，只给默认值 + `STILL_ACTING` 这个标记集合。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.engine.ending import _alive_camps, alive_ids, is_wipe  # noqa: E402
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef  # noqa: E402
from trpg.models.enums import Position  # noqa: E402
from trpg.models.state import can_act, is_alive, is_eliminated  # noqa: E402


def _rt(
    hp: int = 10,
    position: Position = Position.scene,
    conditions: list[str] | None = None,
) -> CharacterRuntime:
    return CharacterRuntime(
        hp=hp, sanity=10, position=position, conditions=list(conditions or [])
    )


def _state(**characters) -> GameState:
    return GameState(
        session_id="s",
        world=WorldRef(world_id="w", era="base"),
        scene=Scene(location_id="loc_1", region="r"),
        party=Party(),
        characters=characters,
    )


def test_is_eliminated_only_looks_at_position():
    assert is_eliminated(_rt(position=Position.dead)) is True
    assert is_eliminated(_rt(hp=0)) is False, "濒死还没出局（宽限期内可以救）"


def test_is_alive_ignores_hp():
    """`is_alive` 是「还在场上」，与血量无关。"""
    assert is_alive(_rt()) is True
    assert is_alive(_rt(hp=0)) is True, "0 血 = 濒死，人还在场上"
    assert is_alive(_rt(position=Position.dead)) is False


def test_can_act_defaults_to_hp_positive():
    assert can_act(_rt()) is True
    assert can_act(_rt(hp=0)) is False, "默认：濒死躺地上，这一回合动不了"
    assert can_act(_rt(position=Position.dead)) is False


def test_can_act_honours_undying_conditions():
    """「清零不死」的技能可以照常行动——框架不替世界决定失能。"""
    for flag in ("undying", "berserk", "revived"):
        assert can_act(_rt(hp=0, conditions=[flag])) is True, flag
    assert can_act(_rt(hp=0, conditions=["bleeding"])) is False, "无关标记不豁免"


def test_none_is_treated_as_gone():
    assert is_alive(None) is False
    assert can_act(None) is False
    assert is_eliminated(None) is True


def test_wipe_needs_real_elimination():
    """「全员出局」只看正式出局：全员濒死但没人转 dead，不算全灭。"""
    state = _state(a=_rt(hp=0), b=_rt(hp=0))
    assert is_wipe(state) is False

    state.characters["a"].position = Position.dead
    assert is_wipe(state) is False

    state.characters["b"].position = Position.dead
    assert is_wipe(state) is True


def test_alive_ids_keeps_dying_players():
    """濒死者仍算「在场上」，只有正式出局才消失。"""
    state = _state(a=_rt(), b=_rt(hp=0), c=_rt(position=Position.dead))

    assert alive_ids(state) == ["a", "b"]


def test_camp_counts_keep_dying_players():
    """阵营人数是**胜负判定**的输入，必须按「谁还属于这个阵营」算。

    濒死的狼还是狼——只是这一回合动不了。按 hp 剔除会让残血阵营少算人数，
    从而把「全员濒死」误判成「全灭」。
    """
    state = _state(
        w1=CharacterRuntime(hp=10, sanity=10, camp="wolf"),
        w2=CharacterRuntime(hp=0, sanity=10, camp="wolf"),
        v1=CharacterRuntime(hp=10, sanity=10, camp="village"),
        v2=CharacterRuntime(hp=0, sanity=10, camp="village"),
    )

    assert _alive_camps(state) == {"wolf": 2, "village": 2}
