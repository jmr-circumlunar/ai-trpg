"""S5-1：NPC 关系双时态（表 + 模型 + 注入 + 消灭 TODO）。

要钉住的五件事：
1. **表与模型在**：写入后能读回；
2. **时点查询**：某回合当时成立的关系 ≠ 现在的关系；
3. **失效关系不出现**：此刻查询里没有已闭合的关系，但**历史仍在**（append-only）；
4. **关系进 prompt**：只给仍成立的，且没有关系时**不出现空段落**（免得模型编造）；
5. **关系值不得未经声明进 StateDelta**：白名单里没有 relations 路径（用测试钉住）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trpg.agents.prompts.npc import npc_system
from trpg.engine.state_merge import _path_allowed
from trpg.models.relation import Relation, relations_to_dicts
from trpg.storage import db

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "rel.db"), check_same_thread=False)
    db.init_db(connection)
    yield connection
    connection.close()


def _rel(subject="npc_a", object_="chr_p1", kind="ally", **kw) -> Relation:
    data = {
        "subject": subject,
        "object": object_,
        "kind": kind,
        "weight": 0.6,
        "valid_from": 2,
        "valid_to": None,
        "source_event": "evt_1",
        "recorded_turn": 2,
    }
    data.update(kw)
    return Relation(**data)


# ── 表与模型 ────────────────────────────────────────────────
def test_table_exists_and_old_db_is_compatible(tmp_path):
    """旧库兼容：init_db 之后表就在（不需要迁移脚本）。"""
    connection = sqlite3.connect(str(tmp_path / "old.db"), check_same_thread=False)
    db.init_db(connection)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(npc_relations)").fetchall()
    }
    for name in (
        "subject", "object", "kind", "weight", "valid_from", "valid_to", "source_event",
    ):
        assert name in columns, f"npc_relations 缺列 {name}"
    connection.close()


def test_save_and_load(conn):
    db.save_relation(conn, "ses_x", _rel())
    rows = db.load_relations(conn, "ses_x")
    assert len(rows) == 1
    assert rows[0].subject == "npc_a"
    assert rows[0].kind == "ally"
    assert rows[0].valid_to is None


def test_relations_are_session_scoped(conn):
    db.save_relation(conn, "ses_a", _rel())
    db.save_relation(conn, "ses_b", _rel(subject="npc_b"))
    assert len(db.load_relations(conn, "ses_a")) == 1
    assert len(db.load_relations(conn, "ses_b")) == 1


# ── 时点查询 / 失效 ─────────────────────────────────────────
def test_closed_relation_is_not_active_now_but_stays_in_history(conn):
    """关系破裂 = 闭合区间（写 valid_to），**不删历史**。"""
    db.save_relation(conn, "ses_x", _rel(valid_from=2))
    closed = db.close_relation(conn, "ses_x", subject="npc_a", object="chr_p1", at_turn=5)
    assert closed == 1

    now = db.load_relations_at(conn, "ses_x")
    assert not any(item.kind == "ally" for item in now), "已失效的关系不该出现在此刻"

    history = db.load_relations(conn, "ses_x")
    assert len(history) == 1, "历史必须留着（可回溯）"
    assert history[0].valid_to == 5


def test_point_in_time_query_sees_the_relation_before_it_ended(conn):
    db.save_relation(conn, "ses_x", _rel(valid_from=2))
    db.close_relation(conn, "ses_x", subject="npc_a", object="chr_p1", at_turn=5)
    assert db.load_relations_at(conn, "ses_x", 3), "第 3 回合当时关系成立"
    assert not db.load_relations_at(conn, "ses_x", 6), "第 6 回合关系已破裂"


def test_relation_active_at_helper():
    item = Relation(subject="a", object="b", kind="ally", valid_from=2, valid_to=5)
    assert item.active_at(3)
    assert item.active_at(2) and item.active_at(5)
    assert not item.active_at(6)
    assert not item.active_at(None), "有 valid_to 的关系在「此刻」已失效"


def test_new_relation_after_break_is_separate_fact(conn):
    """破裂后重新结盟 = **另一条事实**，两条都在库里。"""
    db.save_relation(conn, "ses_x", _rel(valid_from=2))
    db.close_relation(conn, "ses_x", subject="npc_a", object="chr_p1", at_turn=4)
    db.save_relation(conn, "ses_x", _rel(valid_from=6, recorded_turn=6))
    assert len(db.load_relations(conn, "ses_x")) == 2
    now = db.load_relations_at(conn, "ses_x")
    assert len(now) == 1 and now[0].valid_from == 6


# ── prompt 注入 ─────────────────────────────────────────────
def test_prompt_includes_active_relations():
    text = npc_system(
        {"name": "守夜人", "goal": "活着", "secret": "怕黑", "attitude": 0},
        "示例世界",
        relations_text="npc_a 与 chr_p1：ally（强度 0.6）· 第 2 回合起（至今仍成立）",
    )
    assert "npc_a 与 chr_p1" in text
    assert "至今仍成立" in text


def test_prompt_has_no_empty_relation_block():
    """没有关系时**整段不出现** —— 空段落只会让模型编造关系。"""
    text = npc_system({"name": "守夜人", "goal": "活着"}, "示例世界")
    assert "你与别人的关系" not in text
    assert "（至今仍成立）" not in text


def test_relations_text_helper(conn):
    db.save_relation(conn, "ses_x", _rel())
    text = db.relations_text(db.load_relations_at(conn, "ses_x"))
    assert "npc_a" in text and "chr_p1" in text


# ── StateDelta 白名单 ───────────────────────────────────────
def test_relations_cannot_enter_state_delta():
    """关系值不得未经声明直接进 StateDelta（白名单里没有 relations 路径）。"""
    for path in ("relations", "relations.npc", "characters.chr_p1.relations"):
        assert not _path_allowed(path), f"{path} 不该在 StateDelta 白名单里"


# ── TODO 消灭 ───────────────────────────────────────────────
def test_play_py_relation_todo_is_gone():
    play = (ROOT / "trpg" / "api" / "routes" / "play.py").read_text(encoding="utf-8")
    assert "P5-T11" not in play, "relations.npc 的 TODO 还没消灭"
    assert '"npc": []' not in play, "relations.npc 还是空列表占位"


def test_relations_to_dicts_helper():
    items = [_rel(), {"subject": "a", "object": "b", "kind": "rival"}]
    out = relations_to_dicts(items)
    assert len(out) == 2
    assert out[0]["kind"] == "ally"
    assert out[1]["kind"] == "rival"
