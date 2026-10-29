"""B40 空白包：模板必须永远真的能跑（模板一漂移，测试先红）。

这也是 schema 的最终验收：`_blank` 只用 YAML 就能配出一个可跑的世界，
说明「框架 / 世界包」的边界是干净的。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_contract import check_world  # noqa: E402
from trpg.rulespack.validate import validate_declarations  # noqa: E402
from trpg.world.pack import discover_worlds, load_world  # noqa: E402

BLANK = ROOT / "worlds" / "_blank"


def test_blank_package_exists():
    assert (BLANK / "world.yaml").exists(), "空白包缺失：worlds/_blank/world.yaml"
    assert (BLANK / "README.md").exists(), "空白包必须有 README（字段说明 + 反例）"


def test_blank_passes_world_check():
    assert check_world(BLANK, ROOT) == []


def test_blank_declarations_are_valid():
    assert validate_declarations(BLANK) == []


def test_blank_loads_and_can_build_character():
    pack = load_world("_blank", ROOT)

    assert pack.world_id == "_blank"
    assert pack.profession_pool(), "空白包至少要有一个职业，否则建不了卡"
    assert pack.seeds, "空白包必须带事件种子（C32）"


def test_blank_is_not_listed_as_playable_world():
    """下划线开头是刻意排除的：模板不该出现在选世界列表里。"""
    assert "_blank" not in discover_worlds(ROOT)


def test_blank_runs_three_turns_with_mock_provider(tmp_path):
    """真实的端到端：CLI 跑 3 回合，退出码必须为 0。"""
    result = subprocess.run(
        [
            sys.executable,
            "run.py",
            "run",
            "--world",
            "_blank",
            "--provider",
            "mock",
            "--turns",
            "3",
            "--db",
            str(tmp_path / "blank.db"),
            "--quiet",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
