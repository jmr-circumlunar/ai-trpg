"""B57 / A1 通用胜负（C58）：纯函数判胜，LLM 不参与，无配置即不判。

四档语义：
- `all_eliminated`：全员出局
- `camp_eliminated`：指定阵营全灭
- `camp_superiority`：指定阵营人数 ≥ 其他阵营总和
- `group_eliminated`：指定**身份组**全灭（屠边；本局没发这类身份时不判）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card, make_state  # noqa: E402
from trpg.engine.ending import evaluate_win  # noqa: E402
from trpg.models import CharacterRuntime  # noqa: E402
from trpg.models.enums import Position  # noqa: E402


class FakePack:
    def __init__(self, config: dict) -> None:
        self.win_condition = config


def build_state(camps: list[str]):
    state = make_state()
    state.characters = {}
    for index, camp in enumerate(camps, start=1):
        cid = f"chr_{index}"
        state.characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)
    return state


def kill(state, camp: str) -> None:
    for rt in state.characters.values():
        if rt.camp == camp:
            rt.position = Position.dead


def test_no_config_means_no_verdict():
    assert evaluate_win(build_state(["wolf", "village"]), FakePack({})) is None
    assert evaluate_win(build_state(["wolf"]), None) is None


def test_all_eliminated():
    state = build_state(["wolf", "village"])
    for rt in state.characters.values():
        rt.position = Position.dead

    result = evaluate_win(state, FakePack({"kind": "all_eliminated"}))

    assert result is not None
    assert result.kind == "all_eliminated"


def test_camp_eliminated_reports_survivor_camp():
    state = build_state(["wolf", "village", "village"])
    kill(state, "village")

    result = evaluate_win(
        state, FakePack({"kind": "camp_eliminated", "camp": "village"})
    )

    assert result is not None
    assert result.winner == "wolf"


def test_camp_superiority_when_not_outnumbered():
    state = build_state(["wolf", "wolf", "village"])

    result = evaluate_win(
        state, FakePack({"kind": "camp_superiority", "camp": "wolf"})
    )

    assert result is not None
    assert result.winner == "wolf"
    assert "2 vs 1" in result.detail


def test_camp_superiority_not_yet_met():
    state = build_state(["wolf", "village", "village"])

    assert (
        evaluate_win(state, FakePack({"kind": "camp_superiority", "camp": "wolf"}))
        is None
    )


def test_unknown_kind_returns_none():
    assert evaluate_win(build_state(["wolf"]), FakePack({"kind": "moon_phase"})) is None


# ── A1：某类身份全灭（屠边） ────────────────────────────────
#
# `camp_eliminated` 只按阵营判：有神职时「杀光村民但神还在」不算阵营全灭，
# 游戏会一直不结束。屠边看的是**身份组**，所以必须拿到身份表（`cards`）。

VILLAGER_GROUP = {
    "kind": "group_eliminated",
    "group": ["村民"],
    "winner": "wolf",
    "label": "村民",
}


def build_identity_case():
    """两狼 + 两民 + 一神：camp 与 profession 都对得上真实建卡结果。"""
    state = make_state()
    state.characters = {}
    cards = {}
    roster = [
        ("chr_w1", "wolf", "狼人"),
        ("chr_w2", "wolf", "狼人"),
        ("chr_v1", "village", "村民"),
        ("chr_v2", "village", "村民"),
        ("chr_s1", "village", "预言家"),
    ]
    for cid, camp, profession in roster:
        state.characters[cid] = CharacterRuntime(hp=10, sanity=10, camp=camp)
        cards[cid] = make_card(cid=cid, profession=profession)
    return state, cards


def test_group_eliminated_when_no_member_survives():
    state, cards = build_identity_case()
    for cid in ("chr_v1", "chr_v2"):
        state.characters[cid].position = Position.dead

    result = evaluate_win(state, FakePack(VILLAGER_GROUP), cards)

    assert result is not None
    assert result.kind == "group_eliminated"
    assert result.winner == "wolf"
    assert "村民" in result.detail


def test_group_eliminated_waits_for_last_member():
    """只死掉一部分成员时不能判胜——同阵营其他人还在。"""
    state, cards = build_identity_case()
    state.characters["chr_v1"].position = Position.dead

    assert evaluate_win(state, FakePack(VILLAGER_GROUP), cards) is None


def test_group_eliminated_ignores_group_absent_this_game():
    """本局没发这类身份：不能因为「空组全灭」就第一回合判胜。"""
    state, cards = build_identity_case()
    config = {"kind": "group_eliminated", "group": ["女巫"], "winner": "wolf"}

    assert evaluate_win(state, FakePack(config), cards) is None


def test_group_eliminated_requires_identity_table():
    """身份分组规则缺 `cards` 必须抛错，不能伪装成「游戏还没结束」。"""
    state, _ = build_identity_case()

    with pytest.raises(ValueError):
        evaluate_win(state, FakePack(VILLAGER_GROUP), None)


def test_any_of_takes_group_rule_into_account():
    """`any_of` 里混排阵营规则与身份规则，任一命中即终局。"""
    state, cards = build_identity_case()
    for cid in ("chr_v1", "chr_v2"):
        state.characters[cid].position = Position.dead
    config = {
        "any_of": [
            {"kind": "camp_eliminated", "camp": "wolf"},
            VILLAGER_GROUP,
        ]
    }

    result = evaluate_win(state, FakePack(config), cards)

    assert result is not None
    assert result.kind == "group_eliminated"
