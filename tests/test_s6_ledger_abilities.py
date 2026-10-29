"""S6-4：追加式影响账本 + 能力包覆盖链 + 解锁树依赖校验。

判据对应 39 号方案 S6-4：
- 三件事各有测试；
- 覆盖链每次保存留版本备份（可回溯）；
- 运行时不得写 worlds/。
"""

from __future__ import annotations

import sqlite3

from trpg.rulespack.overrides import merge_chain, save_merged, unlock_nodes
from trpg.rulespack.unlocks import validate_unlocks
from trpg.storage import db
from trpg.storage.ledger import history, latest, write


def _conn():
    c = sqlite3.connect(":memory:")
    db.init_db(c)
    return c


# ── ① 影响只追加，不改写 ──────────────────────────────────────


def test_ledger_is_append_only():
    conn = _conn()
    write(conn, "ironvale", "npc_x", "death", {"name": "守卫"}, session_id="s1", turn=1)
    write(conn, "ironvale", "npc_x", "death", {"name": "守卫"}, session_id="s1", turn=2)
    write(conn, "ironvale", "npc_x", "note", {"name": "守卫"}, session_id="s1", turn=3)

    rows = conn.execute(
        "SELECT COUNT(*) FROM world_ledger WHERE world_id = ? AND key = ?", ("ironvale", "npc_x")
    ).fetchone()
    assert int(rows[0]) == 3, "同 key 写三次就该有三条（旧记录一条都不改）"

    items = history(conn, "ironvale", "npc_x")
    assert len(items) == 3, "历史可回溯"
    assert items[0]["source_turn"] == 1 and items[-1]["source_turn"] == 3


def test_ledger_latest_is_the_newest():
    conn = _conn()
    write(conn, "ironvale", "k", "a", {"v": 1}, session_id="s1", turn=1)
    write(conn, "ironvale", "k", "a", {"v": 2}, session_id="s1", turn=2)
    newest = latest(conn, "ironvale", "k")
    assert newest is not None
    assert newest["value"] == {"v": 2}, "当前视图取最新一条"
    assert latest(conn, "ironvale", "missing") is None


# ── ② 能力包覆盖链：优先级显式 + 合并回写 + 版本备份 ──────────


def test_merge_chain_priority_is_explicit():
    low = {"id": "low", "priority": 1, "value": "low", "extra": {"a": 1}}
    high = {"id": "high", "priority": 9, "value": "high"}
    merged = merge_chain([low, high])
    assert merged["value"] == "high", "priority 大的覆盖小的"
    assert merged["extra"] == {"a": 1}, "未覆盖的键保留"


def test_merge_chain_same_priority_later_wins():
    first = {"id": "a", "priority": 5, "value": "first"}
    second = {"id": "b", "priority": 5, "value": "second"}
    assert merge_chain([first, second])["value"] == "second", "同优先级时链上靠后者胜"
    assert merge_chain([second, first])["value"] == "first", "顺序变了结果跟着变（顺序有意义）"


def test_merge_chain_merges_unlock_lists_by_id():
    base = {"id": "base", "priority": 0, "unlocks": [{"id": "u1", "requires": []}]}
    extra = {"id": "extra", "priority": 1, "unlocks": [{"id": "u2", "requires": ["u1"]}]}
    merged = merge_chain([base, extra])
    ids = [n["id"] for n in merged["unlocks"]]
    assert ids == ["u1", "u2"], "解锁树按 id 并集，不是整段替换"


def test_save_merged_backs_up_previous_version(tmp_path):
    merged = {"id": "a", "priority": 0}
    out, backup = save_merged(tmp_path, merged)
    assert out.exists()
    assert backup is None, "第一次保存没有上一版可备份"
    out2, backup2 = save_merged(tmp_path, {"id": "b"})
    assert backup2 is not None and backup2.exists(), "第二次保存必须留上一版备份"
    assert backup2.parent.name == "backups"
    assert "a" in backup2.read_text(encoding="utf-8")
    assert "b" in out2.read_text(encoding="utf-8")
    assert not (tmp_path / "worlds").exists(), "运行时不写 worlds"


# ── ③ 解锁树：命名引用 + 依赖校验 ─────────────────────────────


def test_unlock_tree_valid():
    nodes = [
        {"id": "u1", "requires": []},
        {"id": "u2", "requires": ["u1"]},
    ]
    assert validate_unlocks(nodes) == [], "合法的依赖树不该报问题"


def test_unlock_missing_dependency_is_reported():
    nodes = [{"id": "u1", "requires": ["nope"]}]
    problems = validate_unlocks(nodes)
    assert any("nope" in p for p in problems), "缺失依赖要报出来"


def test_unlock_cycle_is_reported():
    nodes = [
        {"id": "a", "requires": ["b"]},
        {"id": "b", "requires": ["a"]},
    ]
    problems = validate_unlocks(nodes)
    assert any("成环" in p for p in problems), "成环要报出来"


def test_unlock_requires_must_be_named_reference():
    nodes = [{"id": "a", "requires": ["1 + 1"]}]
    problems = validate_unlocks(nodes)
    assert any("不存在" in p for p in problems), "非命名引用（表达式）会被当成找不到的 id"

    nodes2 = [{"id": "a", "requires": "u1"}]
    problems2 = validate_unlocks(nodes2)
    assert any("id 列表" in p for p in problems2), "requires 必须是列表"


def test_declared_abilities_are_valid():
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((root / "rules" / "abilities.yaml").read_text(encoding="utf-8"))
    packs = raw.get("abilities") or []
    nodes = unlock_nodes(packs)
    assert nodes, "声明里要有解锁项"
    assert validate_unlocks(nodes) == [], "仓库里的声明必须自洽"
