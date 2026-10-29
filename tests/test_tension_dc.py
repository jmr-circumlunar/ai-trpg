"""张力修正 DC（B1）：纯查表，模型不得自己加难度。"""

from pathlib import Path

from trpg.models import PlayerAction
from trpg.models.enums import Intent
from trpg.rules import DiceEngine, Rules
from trpg.rules.judge import judge
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]
RULES = Rules(ROOT)


def test_tension_table_matches_yaml_for_every_value():
    table = RULES.tension_dc_mod
    assert table, "rules/ruleset.yaml 必须有 tension_dc_mod（数值只在 yaml）"

    bands = sorted(table)
    for tension in range(0, 101):
        expected = table[max(k for k in bands if k <= tension)]
        assert RULES.tension_mod(tension) == expected, f"张力 {tension} 查表不对"


def test_tension_mod_is_monotonic_and_zero_at_calm():
    values = [RULES.tension_mod(t) for t in range(0, 101, 5)]
    assert values == sorted(values), "张力越高不能越容易"
    assert RULES.tension_mod(0) == 0, "平静时不该有额外难度"


def test_tension_out_of_range_is_clamped():
    assert RULES.tension_mod(-50) == RULES.tension_mod(0)
    assert RULES.tension_mod(999) == RULES.tension_mod(100)


def test_missing_config_keeps_dc_unchanged():
    """旧规则没有这条键时不得报错，也不得改 DC。"""
    legacy = Rules(ROOT)
    legacy.ruleset.pop("tension_dc_mod", None)

    assert legacy.tension_mod(100) == 0


def test_judge_applies_state_tension():
    card = make_card()
    action = PlayerAction(
        actor_id="chr_test01", action="用撬锁试探", skill="撬锁", intent=Intent.normal
    )

    calm = make_state(card)
    calm.tension = 0
    heated = make_state(card)
    heated.tension = 95

    # 同一个 seed：骰面必须一致，差异只能来自 DC
    a = judge(action, card, calm, RULES, DiceEngine("same-seed"))
    b = judge(action, card, heated, RULES, DiceEngine("same-seed"))

    assert b.rolls == a.rolls
    assert b.dc - a.dc == RULES.tension_mod(95) - RULES.tension_mod(0)
    assert b.dc > a.dc, "高张力下同一行动必须更难"
