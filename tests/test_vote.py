"""B55 集体决策（C57）：`tally()` 是纯函数，平票可复现。

计票必须在代码里完成：模型只提交「我要投谁」，不参与「谁票多」。
`tie=runoff` 只返回「平票且无人当选」，下一轮由调用方发起——计票函数不自己循环。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.engine.phases.collective import (  # noqa: E402
    ABSTAIN,
    VoteError,
    VoteRecord,
    tally,
)


def test_plurality_picks_most_votes():
    votes = [VoteRecord("a", "x"), VoteRecord("b", "x"), VoteRecord("c", "y")]

    result = tally(votes)

    assert result.winner == "x"
    assert result.counts["x"] == 2
    assert result.tie is False


def test_abstain_is_counted_but_not_a_candidate():
    votes = [VoteRecord("a", "x"), VoteRecord("b", None)]

    result = tally(votes)

    assert result.winner == "x"
    assert result.counts[ABSTAIN] == 1
    assert "None" not in result.counts


def test_all_abstain_has_no_winner():
    result = tally([VoteRecord("a", None), VoteRecord("b", None)])

    assert result.winner is None
    assert result.tie is False


def test_tie_random_is_reproducible():
    votes = [VoteRecord("a", "x"), VoteRecord("b", "y")]

    first = tally(votes, tie="random", rng=random.Random(7))
    second = tally(votes, tie="random", rng=random.Random(7))

    assert first.winner == second.winner
    assert first.tie is True


def test_tie_runoff_reports_tie_without_winner():
    votes = [VoteRecord("a", "x"), VoteRecord("b", "y")]

    result = tally(votes, tie="runoff")

    assert result.tie is True
    assert result.winner is None
    assert result.rounds == 1


def test_majority_requires_more_than_half():
    votes = [VoteRecord("a", "x"), VoteRecord("b", "y"), VoteRecord("c", "z")]

    result = tally(votes, rule="majority", tie="none")

    assert result.winner is None
    assert result.tie is True


def test_majority_wins_with_half_plus_one():
    votes = [VoteRecord("a", "x"), VoteRecord("b", "x"), VoteRecord("c", "y")]

    result = tally(votes, rule="majority")

    assert result.winner == "x"


def test_unknown_rule_raises():
    with pytest.raises(VoteError):
        tally([VoteRecord("a", "x")], rule="dictatorship")


def test_unknown_tie_raises():
    with pytest.raises(VoteError):
        tally([VoteRecord("a", "x")], tie="coin")


def test_empty_votes_returns_empty_result():
    result = tally([])

    assert result.counts == {}
    assert result.winner is None


def test_vote_record_serialises():
    assert VoteRecord("a", "x", 2).as_dict() == {
        "voter": "a",
        "target": "x",
        "round": 2,
        "kind": "vote",
    }
