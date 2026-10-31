"""W2 · 出口判据的机器代理指标（阵营视角三条）。

被测对象：`trpg/eval/arena.py::ArenaResult.exit_proxies` + `trpg/eval/cli.py`。

指标口径见知识库 `过程记录/方案/42-出口判据代理指标-W2`：

| 指标 | 定义 |
|---|---|
| `deception_success` | 敌对阵营的**伪装成功率** = 己方阵营的票里投给**己方目标**的比例 |
| `ally_effective_identification` | 己方阵营的**指认命中率** = 己方阵营的票里投给**敌对目标**的比例 |
| `ally_collective_success` | 己方阵营**至少一次把敌对成员投票淘汰**的对局占比 |

**公式测试用中性阵营名**（red / blue）：如果这些断言必须改成「狼人 / 好人」才能跑，
说明框架把世界藏进代码了（与 `tests/test_skills.py` 同一把尺子）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from trpg.eval.arena import ArenaResult, GameResult  # noqa: E402

RED = "red"
BLUE = "blue"

# 六条指标：前三条不分阵营，后三条要敌对阵营词表
PROXY_KEYS = {
    "first_day_survival",
    "vote_hit_rate",
    "vote_entropy",
    "deception_success",
    "ally_effective_identification",
    "ally_collective_success",
}


def _game(votes, deaths=(), camps=None) -> GameResult:
    """造一局最小结果：只给「票 / 出局 / 阵营」，够算阵营视角的指标。"""
    return GameResult(
        seed="test",
        all_camps=dict(camps or {}),
        votes=[(1, voter, target) for voter, target in votes],
        deaths=[(1, cid, cause) for cid, cause in deaths],
    )


def _proxies(game: GameResult, hostile=(RED,)) -> dict:
    arena = ArenaResult(games=[game], hostile_camps=tuple(hostile))
    return arena.exit_proxies()


# ── 指标口径 ─────────────────────────────────────────────────


def test_exit_proxies_expose_six_metrics():
    proxies = _proxies(_game([]))

    assert set(proxies) == PROXY_KEYS, proxies


def test_deception_and_identification_split_the_same_votes():
    """同一批票，两个视角：3 票里 1 票命中敌对 → 命中率 1/3、伪装率 2/3。"""
    game = _game(
        votes=[("a", "b"), ("a", "b"), ("a", "d")],
        camps={"a": BLUE, "b": BLUE, "c": RED, "d": RED},
    )

    proxies = _proxies(game)

    assert proxies["ally_effective_identification"] == pytest.approx(1 / 3)
    assert proxies["deception_success"] == pytest.approx(2 / 3)
    # 两者互补：同一批票，两个视角
    assert proxies["deception_success"] + proxies["ally_effective_identification"] == pytest.approx(1.0)


def test_hostile_voters_do_not_enter_the_denominator():
    """敌对阵营自己的票不进分母（它不是「被带偏」的票）。"""
    base = _game(
        votes=[("a", "b"), ("a", "d")],
        camps={"a": BLUE, "b": BLUE, "c": RED, "d": RED},
    )
    with_extra = _game(
        votes=[("a", "b"), ("a", "d"), ("c", "d")],
        camps={"a": BLUE, "b": BLUE, "c": RED, "d": RED},
    )

    before = _proxies(base)
    after = _proxies(with_extra)

    assert before["ally_effective_identification"] == after["ally_effective_identification"]
    assert before["deception_success"] == after["deception_success"]


def test_abstentions_are_not_counted():
    """弃权（没有目标）不带信息，不进分母。"""
    game = _game(
        votes=[("a", "d"), ("a", "")],
        camps={"a": BLUE, "d": RED},
    )

    proxies = _proxies(game)

    assert proxies["ally_effective_identification"] == 1.0
    assert proxies["deception_success"] == 0.0


def test_ally_collective_success_needs_both_a_vote_and_a_removal():
    """「指认成功」= 己方投了他 **且** 他是被集体决策淘汰的。"""
    camps = {"a": BLUE, "d": RED}
    voted_and_exiled = _game(votes=[("a", "d")], deaths=[("d", "collective_result")], camps=camps)
    voted_but_killed = _game(votes=[("a", "d")], deaths=[("d", "consensus_result")], camps=camps)

    assert _proxies(voted_and_exiled)["ally_collective_success"] == 1.0
    assert _proxies(voted_but_killed)["ally_collective_success"] == 0.0


def test_without_hostile_camps_the_camp_metrics_stay_zero():
    """没有词表就不猜：阵营视角的三条为 0，但键仍然齐全（口径不随输入变）。"""
    game = _game(
        votes=[("a", "d")],
        camps={"a": BLUE, "d": RED},
    )

    proxies = _proxies(game, hostile=())

    assert set(proxies) == PROXY_KEYS
    assert proxies["deception_success"] == 0.0
    assert proxies["ally_effective_identification"] == 0.0
    assert proxies["ally_collective_success"] == 0.0


# ── CLI：基线对照 + 数值落盘 ─────────────────────────────────


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run.py", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def test_cli_reports_baseline_proxies_with_the_same_keys():
    """随机基线的**同一组指标**必须同时产出（没有基线的数值没有意义）。"""
    res = _run(
        "arena", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "7", "--hostile-camps", "wolf",
    )

    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert set(data["exit_proxies"]) == PROXY_KEYS
    assert set(data["baseline_exit_proxies"]) == PROXY_KEYS


def test_cli_out_writes_metrics_file(tmp_path):
    """`--out` 把指标 + 基线对照 + 跑动参数落盘（可跨轮对比）。"""
    target = tmp_path / "proxies.json"

    res = _run(
        "arena", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "7", "--hostile-camps", "wolf",
        "--out", str(target),
    )

    assert res.returncode == 0, res.stderr
    assert target.exists(), "指标应当落盘"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data["exit_proxies"]) == PROXY_KEYS
    assert set(data["baseline_exit_proxies"]) == PROXY_KEYS
    meta = data["meta"]
    assert meta["world"] == "werewolf"
    assert meta["seed"] == "7"
    assert meta["games"] == 1 and meta["turns"] == 3
    assert meta["hostile_camps"] == ["wolf"]
    assert meta["at"], "落盘文件要带时间戳（跨轮对比用）"


def test_same_seed_same_proxies():
    """同 seed 同结果：代理指标可复跑、可进 CI。"""
    first = _run(
        "arena", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "11", "--hostile-camps", "wolf",
    )
    second = _run(
        "arena", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "11", "--hostile-camps", "wolf",
    )

    assert first.returncode == 0 and second.returncode == 0
    assert (
        json.loads(first.stdout)["exit_proxies"]
        == json.loads(second.stdout)["exit_proxies"]
    )


# ── 技能结果指标（查验命中 / 解药合理 / 毒药合理）──────────────


def _skill_game() -> GameResult:
    """一局构造数据：查验命中 1/2、解药救对 1/2、毒药打中 1/2。"""
    return GameResult(
        seed="t",
        all_camps={"a": "red", "b": "blue", "c": "blue"},
        skills=[
            (1, "seer", "reveal_camp", "a"),
            (2, "seer", "reveal_camp", "b"),
            (1, "medic", "heal", "c"),
            (2, "medic", "heal", "b"),
            (1, "medic", "poison", "a"),
            (2, "medic", "poison", "b"),
        ],
        # 每回合夜里共议的刀口
        consensus=[(1, "c"), (2, "x")],
    )


def test_skill_proxies_measure_reveal_heal_and_poison():
    """三条技能指标各 1/2，样本数如实给出。"""
    arena = ArenaResult(games=[_skill_game()], hostile_camps=("red",))

    proxies = arena.skill_proxies()

    assert proxies["reveal_hit_rate"] == pytest.approx(0.5)
    assert proxies["heal_precision"] == pytest.approx(0.5)
    assert proxies["poison_precision"] == pytest.approx(0.5)
    assert proxies["reveal_samples"] == 2
    assert proxies["heal_samples"] == 2
    assert proxies["poison_samples"] == 2


def test_skill_proxies_report_zero_samples_when_nothing_happened():
    """没有技能事件时比例是 0 且**样本数是 0**（不能把「没数据」读成「全错」）。"""
    arena = ArenaResult(games=[GameResult(seed="t")], hostile_camps=("red",))

    proxies = arena.skill_proxies()

    assert proxies["reveal_samples"] == 0
    assert proxies["heal_samples"] == 0
    assert proxies["poison_samples"] == 0
    assert proxies["reveal_hit_rate"] == 0.0


# ── 技能结果指标（查验命中 / 解药合理 / 毒药合理）──────────────


def _skill_game() -> GameResult:
    """一局构造数据：查验命中 1/2、解药救对 1/2、毒药打中 1/2。"""
    return GameResult(
        seed="t",
        all_camps={"a": "red", "b": "blue", "c": "blue"},
        skills=[
            (1, "seer", "reveal_camp", "a"),
            (2, "seer", "reveal_camp", "b"),
            (1, "medic", "heal", "c"),
            (2, "medic", "heal", "b"),
            (1, "medic", "poison", "a"),
            (2, "medic", "poison", "b"),
        ],
        # 每回合夜里共议的刀口
        consensus=[(1, "c"), (2, "x")],
    )


def test_skill_proxies_measure_reveal_heal_and_poison():
    """三条技能指标各 1/2，样本数如实给出。"""
    arena = ArenaResult(games=[_skill_game()], hostile_camps=("red",))

    proxies = arena.skill_proxies()

    assert proxies["reveal_hit_rate"] == pytest.approx(0.5)
    assert proxies["heal_precision"] == pytest.approx(0.5)
    assert proxies["poison_precision"] == pytest.approx(0.5)
    assert proxies["reveal_samples"] == 2
    assert proxies["heal_samples"] == 2
    assert proxies["poison_samples"] == 2


def test_skill_proxies_report_zero_samples_when_nothing_happened():
    """没有技能事件时比例是 0 且**样本数是 0**（不能把「没数据」读成「全错」）。"""
    arena = ArenaResult(games=[GameResult(seed="t")], hostile_camps=("red",))

    proxies = arena.skill_proxies()

    assert proxies["reveal_samples"] == 0
    assert proxies["heal_samples"] == 0
    assert proxies["poison_samples"] == 0
    assert proxies["reveal_hit_rate"] == 0.0
