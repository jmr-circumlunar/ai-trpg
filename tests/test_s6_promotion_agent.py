"""S6-3：使用频率按「道具 × Agent」计 + 晋升补丁真跑通 + 合并前备份。

判据对应 39 号方案 S6-3：
- 计数维度、补丁生成、备份各有测试；
- 运行时只生成待确认补丁，不写 worlds。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trpg.equipment.promote import collect_promotions, read_pending
from trpg.storage import db
from trpg.storage.items import bump_item_usage, item_usage_candidates_by_agent


def _conn():
    c = sqlite3.connect(":memory:")
    db.init_db(c)
    return c


def test_usage_is_counted_per_agent():
    conn = _conn()
    bump_item_usage(conn, "ironvale", "s1", "旧灯笼", turn=1, agent_id="chr_a")
    bump_item_usage(conn, "ironvale", "s1", "旧灯笼", turn=2, agent_id="chr_a")
    bump_item_usage(conn, "ironvale", "s1", "旧灯笼", turn=3, agent_id="chr_b")

    rows = item_usage_candidates_by_agent(conn, "ironvale")
    by_key = {(r["canonical"], r["agent_id"]): r for r in rows}
    assert by_key[("旧灯笼", "chr_a")]["uses"] == 2, "同 agent 的使用要累加"
    assert by_key[("旧灯笼", "chr_b")]["uses"] == 1, "不同 agent 分开计数"

    total = conn.execute("SELECT SUM(uses) FROM item_usage").fetchone()[0]
    assert int(total) == 3, "全局计数仍然保留"


def test_promotion_patch_generated_only_when_threshold_met(tmp_path):
    conn = _conn()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    for turn in range(1, 4):
        bump_item_usage(conn, "ironvale", "s1", "旧灯笼", turn=turn, agent_id="chr_a")

    params = {"prop_to_instance": 3, "instance_to_template": 3, "require_review": True}
    rows = item_usage_candidates_by_agent(conn, "ironvale")
    paths = collect_promotions(tmp_path, "ironvale", rows, params=params)
    assert len(paths) == 1, "达到阈值就该产出一份补丁"
    assert paths[0].parent.name == "promotions"
    assert paths[0].parent.parent.name == "data"
    assert not (tmp_path / "worlds").exists(), "运行时不得写 worlds"

    patches = read_pending(tmp_path)
    assert patches and patches[0]["canonical"] == "旧灯笼"
    assert patches[0]["agent_id"] == "chr_a", "补丁要记清是哪个 agent 用出来的"
    assert int(patches[0]["uses"]) == 3


def test_no_patch_below_threshold(tmp_path):
    conn = _conn()
    bump_item_usage(conn, "ironvale", "s1", "旧灯笼", turn=1, agent_id="chr_a")
    rows = item_usage_candidates_by_agent(conn, "ironvale")
    paths = collect_promotions(tmp_path, "ironvale", rows, params={"prop_to_instance": 3})
    assert paths == [], "没到阈值不产补丁"


def test_backup_before_merge(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "apply_promotions", Path(__file__).resolve().parents[1] / "tools" / "apply_promotions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    world_dir = tmp_path / "worlds" / "ironvale"
    world_dir.mkdir(parents=True)
    items = world_dir / "items.yaml"
    items.write_text("items: []\n", encoding="utf-8")

    backup = module._backup_items(tmp_path, "ironvale")
    assert backup is not None and backup.exists(), "合并前必须留备份"
    assert backup.parent.name == "backups"
    assert backup.read_text(encoding="utf-8") == "items: []\n"


def test_real_patch_artifact_exists():
    """真实局跑出的补丁必须落在 data/promotions（出口判据）。"""
    root = Path(__file__).resolve().parents[1]
    pending = root / "data" / "promotions"
    files = sorted(pending.glob("*.yaml")) if pending.is_dir() else []
    assert files, "data/promotions 应有真实产物"
