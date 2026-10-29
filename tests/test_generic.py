"""C60 框架通用性：`trpg/` 里不得出现任何世界专属词汇。

这条守的是「框架只提供通用能力」：一旦框架里出现某个世界的词，
说明该能力已经被那一类游戏绑架，必须改成参数或下沉到世界包。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_generic import BLACKLIST, find_violations  # noqa: E402


def test_blacklist_is_not_empty_and_generic():
    assert "night" in BLACKLIST
    assert "werewolf" in BLACKLIST


def test_trpg_has_no_world_vocabulary():
    violations = find_violations(ROOT / "trpg")
    assert violations == [], "框架内出现世界专属词汇：" + "; ".join(
        f"{Path(p).relative_to(ROOT)}:{line}:{word}" for p, line, word in violations
    )


def test_checker_detects_planted_violation(tmp_path):
    pkg = tmp_path / "trpg"
    pkg.mkdir()
    (pkg / "bad_module.py").write_text(
        "def night_action():\n    return 1\n", encoding="utf-8"
    )

    violations = find_violations(pkg)

    assert violations, "应能检出 night 词汇"
    assert violations[0][2] == "night"


def test_checker_allows_marked_world_example(tmp_path):
    pkg = tmp_path / "trpg"
    pkg.mkdir()
    (pkg / "ok_module.py").write_text(
        "# world-example: 某世界把这个阶段命名为 night\nx = 1\n", encoding="utf-8"
    )

    assert find_violations(pkg) == []


def test_checker_ignores_non_python_files(tmp_path):
    pkg = tmp_path / "trpg"
    pkg.mkdir()
    (pkg / "notes.md").write_text("night werewolf", encoding="utf-8")

    assert find_violations(pkg) == []
