"""S3-2：评测 CLI 三件套（arena / leaderboard / calibrate）。

要钉住的四件事：

1. **三命令可用**：`run.py` 上真的挂了这三个子命令，且 `--help` 可读；
2. **leaderboard 输出 Markdown**（可直接落知识库），并附**随机基线**对照；
3. **离线确定性 mock 可跑**：CI 无需 key，同 seed 同结果；
4. **退出码语义清晰**：0 成功、非 0 失败。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run.py", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


@pytest.fixture(scope="module")
def leaderboard_run() -> subprocess.CompletedProcess:
    return _run(
        "leaderboard", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "7",
    )


# ── 命令存在 ────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", ["arena", "leaderboard", "calibrate"])
def test_run_py_exposes_eval_commands(cmd):
    res = _run(cmd, "--help")
    assert res.returncode == 0, res.stderr
    assert cmd in res.stdout
    assert "--world" in res.stdout and "--games" in res.stdout


def test_module_entry_also_works():
    """`python -m trpg.eval` 与 `run.py` 是同一套命令（两个入口都要能用）。"""
    res = subprocess.run(
        [sys.executable, "-m", "trpg.eval", "--help"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert res.returncode == 0
    assert "arena" in res.stdout and "leaderboard" in res.stdout and "calibrate" in res.stdout


# ── leaderboard：Markdown ───────────────────────────────────
def test_leaderboard_outputs_markdown(leaderboard_run):
    assert leaderboard_run.returncode == 0, leaderboard_run.stderr
    out = leaderboard_run.stdout
    assert "| 名次 | agent | 胜率 |" in out, "默认输出必须是 Markdown 表"
    assert "|" in out and "---" in out
    assert "**随机基线（对照）**" in out, "没有基线的胜率没有意义，必须给出对照"


def test_leaderboard_json_format_available(leaderboard_run):
    res = _run(
        "leaderboard", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "7", "--format", "json",
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert "leaderboard" in data and "baseline" in data


# ── arena / calibrate ───────────────────────────────────────
def test_arena_outputs_metrics_with_baseline():
    res = _run(
        "arena", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "7",
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    for key in ("camp_survival_rate", "agent_win_rate", "calibration"):
        assert key in data, f"arena 输出缺 {key}"
    assert "baseline_camp_win_rate" in data, "arena 必须带随机基线对照"


def test_calibrate_outputs_calibration():
    res = _run(
        "calibrate", "--world", "werewolf", "--players", "8",
        "--games", "1", "--turns", "3", "--seed", "7",
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert "calibration" in data
    assert "reliability" in data, "校准报告要有可靠性表"


# ── 确定性 ──────────────────────────────────────────────────
def test_same_seed_same_result():
    a = _run("arena", "--world", "werewolf", "--players", "8",
             "--games", "1", "--turns", "3", "--seed", "123")
    b = _run("arena", "--world", "werewolf", "--players", "8",
             "--games", "1", "--turns", "3", "--seed", "123")
    assert a.returncode == 0 and b.returncode == 0
    assert a.stdout == b.stdout, "同 seed 必须同结果（可复现、可进 CI）"


def test_different_seed_may_differ():
    a = _run("arena", "--world", "werewolf", "--players", "8",
             "--games", "1", "--turns", "3", "--seed", "1")
    b = _run("arena", "--world", "werewolf", "--players", "8",
             "--games", "1", "--turns", "3", "--seed", "2")
    assert a.returncode == 0 and b.returncode == 0


# ── 退出码语义 ──────────────────────────────────────────────
def test_failure_returns_nonzero():
    res = _run("arena", "--world", "no_such_world", "--games", "1")
    assert res.returncode != 0, "失败必须非 0（CI 要能判）"
    assert "失败" in (res.stderr or "")


def test_success_returns_zero():
    res = _run("arena", "--world", "werewolf", "--players", "8",
               "--games", "1", "--turns", "2", "--seed", "7")
    assert res.returncode == 0
