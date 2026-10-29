"""S2-3：概率信念四步（先验 / 硬化 / 微调 / 守恒重归一 + 跨轮平滑）。

钉住出口判据的三件事：

1. **守恒**：未确认项的怀疑度之和 = 剩余敌数（硬化的已确认敌人数要先扣掉）；
2. **硬化**：世界包声明的已确认项取 0/1，且**不参与微调**；
3. **依据来源可追溯**：每条怀疑都带 `sources`（先验 / 硬化 / 微调 / 平滑）。

外加：微调只对不确定项、越界夹取、跨轮平滑单调、缺省零变化。
"""

from __future__ import annotations

import pytest

from trpg.engine.beliefs import compute_beliefs, enemy_count_from_state
from trpg.models import CharacterRuntime
from trpg.rules.loader import Rules
from support.builders import make_state

EPS = 1e-6


def _params(root=None):
    return Rules(root).belief


def test_conservation_sum_equals_remaining_enemies():
    """守恒：4 个候选、2 个剩余敌 → 怀疑度之和 = 2。"""
    out = compute_beliefs(
        candidates=["a", "b", "c", "d"],
        enemy_count=2,
        params=_params(),
    )
    total = sum(item["confidence"] for item in out.values())
    assert total == pytest.approx(2.0, abs=EPS)


def test_conservation_subtracts_confirmed_enemies():
    """硬化的已确认敌人要先从剩余敌数里扣掉。"""
    out = compute_beliefs(
        candidates=["a", "b", "c"],
        enemy_count=3,
        confirmed={"a": {"stance": "enemy", "reason": "已确认", "source": "声明"}},
        params=_params(),
    )
    assert out["a"]["confidence"] == pytest.approx(1.0)
    rest = sum(out[t]["confidence"] for t in ("b", "c"))
    assert rest == pytest.approx(2.0, abs=EPS)
    assert rest + out["a"]["confidence"] == pytest.approx(3.0, abs=EPS)


def test_hardened_items_do_not_move():
    """硬化项取 0/1：给再大的微调也不动。"""
    out = compute_beliefs(
        candidates=["a", "b", "c"],
        enemy_count=2,
        confirmed={
            "a": {"stance": "enemy", "reason": "已确认是敌人", "source": "声明"},
            "b": {"stance": "ally", "reason": "已确认是自己人", "source": "声明"},
        },
        adjustments={"a": 0.9, "b": -0.9, "c": 0.1},
        params=_params(),
    )
    assert out["a"]["confidence"] == pytest.approx(1.0)
    assert out["b"]["confidence"] == pytest.approx(0.0)
    assert out["a"]["sources"] == ["confirmed:声明"]


def test_adjustment_only_touches_uncertain_items():
    """微调只对不确定项生效（有微调标记 → 权重更高）。"""
    out = compute_beliefs(
        candidates=["a", "b"],
        enemy_count=1,
        adjustments={"a": 0.3},
        params=_params(),
    )
    assert "adjust" in out["a"]["sources"]
    assert "adjust" not in out["b"]["sources"]
    assert out["a"]["confidence"] > out["b"]["confidence"]


def test_adjustment_is_clamped():
    """越界微调被夹住（上限来自 rules/belief.yaml）。"""
    out = compute_beliefs(
        candidates=["a", "b"],
        enemy_count=1,
        adjustments={"a": 5.0, "b": -5.0},
        params=_params(),
    )
    assert 0.0 <= out["a"]["confidence"] <= 1.0
    assert 0.0 <= out["b"]["confidence"] <= 1.0


def test_smoothing_is_monotonic_in_prior():
    """跨轮平滑：上一轮越怀疑，这轮结果越高（长期信任与即时判断分开）。"""
    low = compute_beliefs(
        candidates=["a", "b"], enemy_count=1, prior={"a": 0.1, "b": 0.1}, params=_params()
    )
    high = compute_beliefs(
        candidates=["a", "b"], enemy_count=1, prior={"a": 0.9, "b": 0.1}, params=_params()
    )
    assert high["a"]["confidence"] > low["a"]["confidence"]
    assert "smooth" in high["a"]["sources"]


def test_sources_are_traceable():
    """每条怀疑都带来源标记。"""
    out = compute_beliefs(
        candidates=["a", "b"],
        enemy_count=1,
        adjustments={"a": 0.2},
        prior={"a": 0.4},
        params=_params(),
    )
    assert out["a"]["sources"]
    assert "prior" in out["a"]["sources"]
    assert out["a"]["reason"]
    assert "先验" in out["a"]["reason"]


def test_zero_enemies_means_zero_suspicion():
    out = compute_beliefs(candidates=["a", "b"], enemy_count=0, params=_params())
    assert sum(item["confidence"] for item in out.values()) == pytest.approx(0.0, abs=EPS)


def test_no_candidates_does_not_crash():
    out = compute_beliefs(candidates=[], enemy_count=2, params=_params())
    assert out == {}


def test_enemy_count_uses_world_declared_hostile_camps():
    """剩余敌数按**世界包声明的敌对阵营**算（框架只比阵营 id）。"""
    state = make_state()
    state.characters["me"] = CharacterRuntime(hp=5, sanity=5, camp="alpha")
    state.characters["x"] = CharacterRuntime(hp=5, sanity=5, camp="beta")
    state.characters["y"] = CharacterRuntime(hp=5, sanity=5, camp="beta")
    state.characters["z"] = CharacterRuntime(hp=5, sanity=5, camp="alpha")
    assert enemy_count_from_state(state, "me", ("beta",)) == 2
    assert enemy_count_from_state(state, "me", ()) == 0
    assert enemy_count_from_state(state, "me", ("gamma",)) == 0


def test_belief_params_live_in_rules_yaml():
    """数值真相源：参数在 `rules/belief.yaml`，不在代码里。"""
    params = _params()
    assert set(params) >= {"prior", "harden", "adjust", "smooth", "conservation"}
    assert params["harden"]["enemy_confidence"] == 1.0
    assert params["harden"]["ally_confidence"] == 0.0
    assert 0.0 <= params["smooth"]["weight"] <= 1.0


def test_framework_has_no_world_faction_words():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "trpg" / "engine" / "beliefs.py").read_text(
        encoding="utf-8"
    )
    for bad in ("狼", "村民", "悍跳", "屠边", "银水", "预言家"):
        assert bad not in src, f"框架层不许出现世界词：{bad}"
