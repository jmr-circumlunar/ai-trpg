import pytest

from trpg.rules.dice import DiceEngine, DiceError


def test_same_seed_same_sequence():
    a, b = DiceEngine(42), DiceEngine(42)
    seq_a = [a.d(20) for _ in range(20)]
    seq_b = [b.d(20) for _ in range(20)]
    assert seq_a == seq_b


def test_different_seed_differs():
    a, b = DiceEngine(1), DiceEngine(2)
    assert [a.d(20) for _ in range(20)] != [b.d(20) for _ in range(20)]


def test_roll_range():
    dice = DiceEngine(7)
    for _ in range(200):
        assert 1 <= dice.d(20) <= 20


def test_expression_with_modifier():
    dice = DiceEngine("expr")
    rolls, total = dice.roll("2d6+3")
    assert len(rolls) == 2
    assert total == sum(rolls) + 3


def test_expression_negative_modifier():
    dice = DiceEngine("neg")
    rolls, total = dice.roll("1d20-2")
    assert total == rolls[0] - 2


@pytest.mark.parametrize("expr", ["", "d", "abc", "1d1", "0d6", "30d6", "1d20*2"])
def test_bad_expression(expr):
    with pytest.raises(DiceError):
        DiceEngine(1).roll(expr)


def test_rng_state_advances():
    dice = DiceEngine(5)
    before = dice.rng_state
    dice.d(6)
    assert dice.rng_state != before
