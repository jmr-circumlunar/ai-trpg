"""W1 · 狼人杀专项回归（胜负侧）：三类胜负判定 + 屠边。

标准规则 R1（**屠边**）：狼人阵营杀光**神职**或杀光**平民**即胜；
好人阵营要**狼全部出局**才胜（依据：2026-10-30 联网核对的规则表，
知识库 `过程记录/方案/41-…-W1`）。

被测对象是真实世界包 `worlds/werewolf/world.yaml: win_condition.any_of`
（camp_eliminated + 两条 group_eliminated）+ 真实判定函数
`trpg/engine/ending.py::evaluate_win`（**纯代码，LLM 不参与**）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.werewolf_board import (  # noqa: E402
    GODS,
    VILLAGERS,
    WOLVES,
    build_board,
    run_turns,
    votes,
)
from trpg.engine.ending import evaluate_win  # noqa: E402


def _verdict(dead):
    """按给定出局名单跑一次判定（纯代码，不跑回合）。"""
    loop, _ = build_board(dead=dead)
    return evaluate_win(loop.state, loop.pack, loop.cards)


def test_wolves_eliminated_means_village_wins():
    """狼全灭 → 好人胜（屠边的反面：好人阵营的胜利条件是狼全灭）。"""
    result = _verdict(WOLVES)

    assert result is not None
    assert result.kind == "camp_eliminated"
    assert result.winner == "village"


def test_partial_wolf_elimination_is_not_a_win():
    """只死一两只狼不算赢——胜负判定不能提前。"""
    result = _verdict(("chr_1", "chr_2"))

    assert result is None, "狼还没死光，好人不能提前判胜"


def test_gods_eliminated_means_wolves_win():
    """屠神：神职（预言家/女巫/猎人）全灭 → 狼胜（平民还活着也算）。"""
    result = _verdict(GODS)

    assert result is not None
    assert result.kind == "group_eliminated"
    assert result.winner == "wolf"
    assert "神职" in result.detail


def test_villagers_eliminated_means_wolves_win():
    """屠民：平民全灭 → 狼胜（神职还活着也算）。"""
    result = _verdict(VILLAGERS)

    assert result is not None
    assert result.kind == "group_eliminated"
    assert result.winner == "wolf"
    assert "村民" in result.detail


def test_no_winner_while_both_camps_are_alive():
    """两边都有人活着 → 不判胜。"""
    result = _verdict(("chr_8",))

    assert result is None


def test_villagers_outnumbering_wolves_is_not_a_win():
    """人数优势不等于胜利——狼人杀只认「杀光」。"""
    result = _verdict(("chr_6", "chr_8"))

    assert result is None


def test_engine_records_the_winner_and_stops_the_day():
    """判出胜负后：写 `state.winner`、回执留痕、当天不再投票。"""
    loop, _ = build_board(dead=VILLAGERS)

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert loop.state.winner == "wolf", "屠民应当判狼胜"
    assert rec.vote_result["win"]["kind"] == "group_eliminated"
    assert rec.vote_result["win"]["winner"] == "wolf"
    assert "vote" not in rec.order, "胜负已定，当天不该再投票"
    assert any(
        item.get("id") == "vote" and "break_if" in str(item.get("reason"))
        for item in rec.skipped
    ), rec.skipped


def test_game_stops_producing_content_after_the_verdict():
    """胜负已定之后的回合全是空回合（`break_if: win_decided`）。

    这一条顺带钉住一个曾经被当成 bug 的现象：**mock 局「10 回合只出几个人」**
    不是白天没投票，而是游戏早就分出胜负，后面的回合被整体跳过。
    """
    loop, _ = build_board(
        vote_plan={
            **votes(
                1,
                day_rounds=(
                    {
                        "chr_1": "chr_2",
                        "chr_2": "chr_3",
                        "chr_3": "chr_2",
                        "chr_4": "chr_1",
                        "chr_5": "chr_1",
                        "chr_6": "chr_1",
                        "chr_7": "chr_1",
                        "chr_8": "chr_1",
                    },
                ),
            ),
            **votes(
                2,
                day_rounds=(
                    {
                        "chr_2": "chr_3",
                        "chr_3": "chr_2",
                        "chr_4": "chr_2",
                        "chr_5": "chr_2",
                        "chr_6": "chr_2",
                        "chr_7": "chr_2",
                        "chr_8": "chr_2",
                    },
                ),
            ),
            **votes(
                3,
                day_rounds=(
                    {
                        "chr_3": "chr_2",
                        "chr_4": "chr_3",
                        "chr_5": "chr_3",
                        "chr_6": "chr_3",
                        "chr_7": "chr_3",
                        "chr_8": "chr_3",
                    },
                ),
            ),
        }
    )

    records, _ = run_turns(loop, 6)

    assert loop.state.winner == "village", "狼全被投出局应当判好人胜"
    decided_at = next(
        index for index, rec in enumerate(records) if rec.vote_result.get("win")
    )
    assert decided_at <= 3, "三轮白天就该分出胜负"
    for rec in records[decided_at + 1 :]:
        assert rec.order == [
            "night",
            "night_skill",
            "night_kill",
            "dawn",
            "check_night",
        ], rec.order
        assert not rec.votes, "胜负已定之后不该再有投票"
        assert rec.skipped, "跳过的阶段要留痕"
