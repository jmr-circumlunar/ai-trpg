"""S2-2：信念的跨回合存储 + 失败回落的回归入口。

四条钉：

1. **跨回合累积**：第 1 回合写下的信念，第 2 回合读回来还在（旧信念整体进 prompt 的前提）；
2. **失败保留旧信念**：结构化输出失败 → 返回**旧信念**并记错，**绝不返回空**（C-3）；
3. **旧库可打开**：老库（没有 `agent_beliefs` 表）跑 `init_db` → 表自动补建、数据不丢；
4. **缺省零变化**：没有信念时一切与改造前一致（golden 不变）。
"""

from __future__ import annotations

import sqlite3

import pytest

from trpg.engine import beliefs as belief_engine
from trpg.models import CharacterRuntime, Mindset, SelfBelief, Suspicion
from trpg.storage import db as storage_db
from trpg.storage.conn import init_db
from support.builders import make_state


def _mindset(target: str = "chr_b", confidence: float = 0.4) -> Mindset:
    return Mindset(
        self_belief=SelfBelief(label="alpha", confidence=0.6),
        suspicions={
            target: Suspicion(
                target=target, label="beta", confidence=confidence, reason="他昨夜不在"
            )
        },
    )


# ── 1. 跨回合累积 ────────────────────────────────────────────

def test_beliefs_persist_across_turns():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    try:
        storage_db.save_beliefs(conn, "ses_x", 1, {"chr_a": _mindset()})
        # 第 2 回合开始：读回旧信念
        stored = storage_db.load_beliefs(conn, "ses_x")
        assert "chr_a" in stored
        assert stored["chr_a"]["self_belief"]["label"] == "alpha"
        assert stored["chr_a"]["suspicions"]["chr_b"]["reason"] == "他昨夜不在"
        # 第 2 回合写新信念 → 仍能读出最新一条
        storage_db.save_beliefs(
            conn, "ses_x", 2, {"chr_a": _mindset(confidence=0.7)}
        )
        latest = storage_db.load_beliefs(conn, "ses_x")
        assert latest["chr_a"]["suspicions"]["chr_b"]["confidence"] == pytest.approx(0.7)
        # 历史留着（只增不改的留痕）
        history = storage_db.load_belief_history(conn, "ses_x", "chr_a")
        assert [turn for turn, _ in history] == [1, 2]
    finally:
        conn.close()


def test_old_beliefs_are_restored_into_state():
    """重启 / 读档后：state 里的信念能从表里回填回来。"""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    try:
        storage_db.save_beliefs(conn, "ses_x", 1, {"chr_a": _mindset()})
        stored = storage_db.load_beliefs(conn, "ses_x")
    finally:
        conn.close()

    state = make_state()
    state.characters["chr_a"] = CharacterRuntime(hp=5, sanity=5)
    assert state.characters["chr_a"].mindset is None

    belief_engine.restore_beliefs(state, stored)
    mindset = state.characters["chr_a"].mindset
    assert isinstance(mindset, Mindset)
    assert mindset.suspicions["chr_b"].reason == "他昨夜不在"


def test_restore_keeps_old_value_when_stored_is_broken():
    state = make_state()
    state.characters["chr_a"] = CharacterRuntime(hp=5, sanity=5)
    old = _mindset()
    state.characters["chr_a"].mindset = old

    belief_engine.restore_beliefs(state, {"chr_a": {"suspicions": {"chr_b": {}}}})
    assert state.characters["chr_a"].mindset is old, "存坏了要保留原值"


def test_old_belief_reaches_the_prompt():
    """旧信念整体进 prompt：knowledge 渲染的是**累积后的**信念。"""
    from trpg.engine import knowledge as knowledge_mod

    state = make_state()
    state.characters["chr_a"] = CharacterRuntime(hp=5, sanity=5)
    state.characters["chr_a"].mindset = _mindset()
    lines = knowledge_mod.render_mindset(
        state.characters["chr_a"].mindset, lambda cid: "乙"
    )
    text = "\n".join(lines)
    assert "alpha" in text and "他昨夜不在" in text


# ── 2. 失败保留旧信念（C-3）──────────────────────────────────

def test_failed_update_keeps_old_belief():
    old = _mindset()
    new, error = belief_engine.update_mindset(old, None)
    assert new is old, "失败必须返回旧信念"
    assert error, "失败必须记错"

    new, error = belief_engine.update_mindset(old, {"self_belief": {"label": "x"}})
    assert new is old
    assert error

    new, error = belief_engine.update_mindset(old, "不是结构化输出")
    assert new is old
    assert error


def test_failed_update_never_returns_empty():
    """没有旧信念时失败 → 返回 None（= 没信念），而不是空信念对象。"""
    new, error = belief_engine.update_mindset(None, None)
    assert new is None
    assert error


def test_successful_update_replaces_whole_mindset():
    old = _mindset()
    parsed = {
        "self_belief": {"label": "gamma", "confidence": 0.2},
        "suspicions": {
            "chr_c": {"target": "chr_c", "confidence": 0.9, "reason": "他自证清白得太急"}
        },
    }
    new, error = belief_engine.update_mindset(old, parsed)
    assert error == ""
    assert isinstance(new, Mindset)
    assert new.self_belief.label == "gamma"
    assert "chr_c" in new.suspicions


def test_update_rejects_labels_outside_world_vocab():
    old = _mindset()
    parsed = {"self_belief": {"label": "unknown", "confidence": 0.5}}
    new, error = belief_engine.update_mindset(old, parsed, vocab=("alpha", "beta"))
    assert new is old, "标签不在词表 → 保留旧信念"
    assert "unknown" in error


def test_beliefs_of_collects_state_mindsets():
    state = make_state()
    state.characters["chr_a"] = CharacterRuntime(hp=5, sanity=5)
    state.characters["chr_a"].mindset = _mindset()
    collected = belief_engine.beliefs_of(state)
    assert list(collected) == ["chr_a"]


# ── 3. 旧库兼容（迁移测试）───────────────────────────────────

def test_old_database_opens_and_gets_the_new_table():
    """老库（没有 agent_beliefs 表）→ init_db 补建，旧数据不丢。"""
    conn = sqlite3.connect(":memory:")
    # 造一个「老库」：只有 sessions 表，且有数据
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT, seed TEXT, "
        "world_id TEXT, era TEXT, status TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions (id, name) VALUES ('ses_old', '旧存档')"
    )
    conn.commit()

    init_db(conn)

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "agent_beliefs" in tables, "新表没建出来"
    row = conn.execute("SELECT name FROM sessions WHERE id='ses_old'").fetchone()
    assert row and row[0] == "旧存档", "旧数据被弄丢了"
    conn.close()


def test_new_table_is_idempotent():
    """init_db 跑两次结果一致（幂等是硬要求）。"""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    init_db(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_beliefs)")}
    assert {"session_id", "turn", "actor_id", "mindset_json"} <= cols
    conn.close()
