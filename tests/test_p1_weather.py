"""R5-T10 天气修正 / R5-T7 模型分档。

天气和时段一样**只影响 DC**，不制造凭空变化：雨雾改变的是判定难度，不是数值。
分档只调「思考深度 / max_tokens」，不换模型——换模型要重新评估质量。
"""

from __future__ import annotations

from pathlib import Path

from trpg.rules.judge import judge
from trpg.agents import agent as agent_mod
from trpg.agents.spec import LLMConfig, ModelSpec
from trpg.models import PlayerAction
from trpg.rules.dice import DiceEngine
from trpg.rules.loader import Rules
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]


def _rules() -> Rules:
    return Rules(ROOT)


# ── T10 天气 ────────────────────────────────────────────────
def test_weather_mod_is_positive_means_harder():
    rules = _rules()
    assert rules.weather_mod("fog", "观察") == 2, "雾里观察更难"
    assert rules.weather_mod("rain", "观察") == 1
    assert rules.weather_mod("clear", "观察") == 0
    assert rules.weather_mod("rain", "潜行") == 0, "没配的技能不受影响"


def test_unknown_weather_is_harmless():
    rules = _rules()
    assert rules.weather_mod("hailstorm", "观察") == 0
    assert rules.weather_mod("", "观察") == 0
    assert rules.weather_mod("fog", None) == 0


def test_weather_labels():
    rules = _rules()
    assert rules.weather_label("fog") == "雾"
    assert rules.weather_label("unknown") == "unknown", "未知值原样返回，不编造"


def test_judge_dc_increases_in_fog():
    rules = _rules()
    card = make_card()
    state = make_state(card)
    action = PlayerAction(actor_id="chr_test01", action="看码头", thought="", skill="观察")

    state.clock.weather = "clear"
    clear = judge(action, card, state, rules, DiceEngine(1))
    state.clock.weather = "fog"
    fog = judge(action, card, state, rules, DiceEngine(1))

    assert fog.dc - clear.dc == rules.weather_mod("fog", "观察"), "天气只能改 DC"


def test_judge_other_skill_unaffected_by_fog():
    rules = _rules()
    card = make_card()
    state = make_state(card)
    action = PlayerAction(actor_id="chr_test01", action="动手", thought="", skill="器械")

    state.clock.weather = "clear"
    clear = judge(action, card, state, rules, DiceEngine(1))
    state.clock.weather = "fog"
    fog = judge(action, card, state, rules, DiceEngine(1))
    assert fog.dc == clear.dc


# ── T7 模型分档 ─────────────────────────────────────────────
def test_spec_for_applies_tier():
    cfg = LLMConfig(ROOT)
    base = ModelSpec(provider="minimax", model="MiniMax-M3.1-Flash", reasoning_effort="medium")

    npc = cfg.spec_for("npc", base)
    assert npc.reasoning_effort == "low", "NPC 是短活，走低档思考"
    assert npc.max_tokens == 600
    assert npc.model == base.model, "分档不换模型"

    gm = cfg.spec_for("gm", base)
    assert gm is base, "GM 用主力档：原样返回"


def test_spec_for_unknown_role_is_passthrough():
    cfg = LLMConfig(ROOT)
    base = ModelSpec(provider="minimax", model="x", reasoning_effort="medium")
    assert cfg.spec_for("no-such-role", base) is base


def test_task_tier_mapping_covers_gm_and_short_tasks():
    assert agent_mod.TASK_TIER["scene"] == "gm"
    assert agent_mod.TASK_TIER["narrate"] == "gm"
    assert agent_mod.TASK_TIER["player"] == "npc"
    assert agent_mod.TASK_TIER["summary"] == "summary"
