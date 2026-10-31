"""W1 · 狼人杀专项回归（投票侧）：平票 PK 重投。

标准规则 R5：**同票 → PK 再投一次；再平票则本轮无人出局**，直接进下一轮
（依据：2026-10-30 联网核对的规则表，知识库 `过程记录/方案/41-…-W1`）。

被测对象是真实世界包 `worlds/werewolf/turn_flow.yaml` 的
`vote: {rule: plurality, tie: runoff, rounds: 2}` 声明 + 真实计票函数
`trpg/engine/phases/collective.py::tally`。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.werewolf_board import (  # noqa: E402
    build_board,
    causes,
    run_turns,
    votes,
)
from trpg.models.enums import Position  # noqa: E402

# 第一轮：4 : 4 平票（chr_7 与 chr_8）
TIE_ROUND = {
    "chr_1": "chr_7",
    "chr_2": "chr_7",
    "chr_3": "chr_8",
    "chr_4": "chr_7",
    "chr_5": "chr_8",
    "chr_6": "chr_8",
    "chr_7": "chr_8",
    "chr_8": "chr_7",
}

# 第二轮：只有平票者是候选人，7 : 1 分出胜负
BREAK_ROUND = {
    "chr_1": "chr_7",
    "chr_2": "chr_7",
    "chr_3": "chr_7",
    "chr_4": "chr_7",
    "chr_5": "chr_7",
    "chr_6": "chr_7",
    "chr_7": "chr_8",
    "chr_8": "chr_7",
}


def test_plurality_exiles_the_most_voted():
    """没有平票时：得票最多者出局。"""
    loop, _ = build_board(
        vote_plan=votes(
            1,
            day_rounds=(
                {
                    "chr_1": "chr_8",
                    "chr_2": "chr_8",
                    "chr_3": "chr_8",
                    "chr_4": "chr_8",
                    "chr_5": "chr_8",
                    "chr_6": "chr_8",
                    "chr_7": "chr_8",
                    "chr_8": None,
                },
            ),
        )
    )

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert rec.vote_result["rounds"] == 1, "没有平票就不该重投"
    assert rec.vote_result["tie"] is False
    assert rec.vote_result["winner"] == "chr_8"
    assert loop.state.characters["chr_8"].position == Position.dead
    assert "collective_result" in causes(rec)


def test_tie_triggers_runoff_and_second_round_breaks_it():
    """平票 → PK 重投 → 分出胜负。"""
    loop, _ = build_board(vote_plan=votes(1, day_rounds=(TIE_ROUND, BREAK_ROUND)))

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert rec.vote_result["rounds"] == 2, "平票应当触发第二轮"
    assert rec.vote_result["winner"] == "chr_7", "第二轮应当分出胜负"
    # 回执里的 `vote_result` 是**最后一轮**的计票；第一轮平票在票型里留痕
    # （`kind=vote` 才是白天投票；`kind=consensus` 是夜里狼组共议的票）
    first_round = [
        vote
        for vote in rec.votes
        if vote.get("round") == 1 and vote.get("kind") == "vote"
    ]
    counts: dict = {}
    for vote in first_round:
        counts[vote.get("target")] = counts.get(vote.get("target"), 0) + 1
    assert counts == {"chr_7": 4, "chr_8": 4}, "第一轮应当是 4 : 4 平票"
    assert loop.state.characters["chr_7"].position == Position.dead
    assert loop.state.characters["chr_8"].position == Position.scene


def test_runoff_candidates_are_only_the_tied_players():
    """PK 轮只有平票者是候选人。"""
    loop, _ = build_board(vote_plan=votes(1, day_rounds=(TIE_ROUND, BREAK_ROUND)))

    records, _ = run_turns(loop, 1)

    rec = records[0]
    second_round = [vote for vote in rec.votes if vote.get("round") == 2]
    assert second_round, "回执里应当有第二轮票"
    assert all(
        vote.get("target") in {"chr_7", "chr_8"} for vote in second_round
    ), "PK 轮只能投给平票者"
    assert {vote.get("target") for vote in second_round} == {"chr_7", "chr_8"}


def test_second_tie_leaves_nobody_exiled():
    """再平票 → 本轮无人出局，直接进下一轮。"""
    loop, _ = build_board(vote_plan=votes(1, day_rounds=(TIE_ROUND, TIE_ROUND)))

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert rec.vote_result["rounds"] == 2
    assert rec.vote_result["tie"] is True
    assert not rec.vote_result.get("winner"), "再平票就没人当选"
    assert "collective_result" not in causes(rec), "再平票时无人出局"
    assert "vote" in rec.order, "投票阶段照常执行"


def test_eliminated_players_do_not_vote():
    """出局者没有投票资格（`eligible: alive`）。"""
    loop, _ = build_board(vote_plan=votes(1, council="chr_7"))

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert loop.state.characters["chr_7"].position == Position.dead
    assert all(vote.get("voter") != "chr_7" for vote in rec.votes), "出局者不该投票"
