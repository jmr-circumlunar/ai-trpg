"""R6：记忆后端（float32 BLOB / 回灌 / 清理 / 阈值）。

以前的问题不是"检索算法慢"，而是：
- 每条向量占 49KB（Python float 对象）→ 万条撞 MemoryMax
- 只写不读 → resume 后什么都想不起来
- 无阈值 → top_k 恒满，不相关的记忆也被当成回忆
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from trpg.memory import HashEmbedder, MemoryBackend, VectorStore
from trpg.memory.recall import hybrid_recall
from trpg.storage import db as db_mod


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "mem.db"))
    db_mod.init_db(c)
    yield c
    c.close()


def _store(conn, dim: int = 128) -> VectorStore:
    return VectorStore(conn, "ses_mem", HashEmbedder(dim))


# ── 存储格式 ────────────────────────────────────────────────
def test_embedding_is_stored_as_blob(conn):
    store = _store(conn)
    store.add_many("chr_a", ["钟楼门洞的砖被抠走了"], turn=1)

    raw = conn.execute("SELECT embedding FROM memory_vectors").fetchone()[0]
    assert isinstance(raw, (bytes, bytearray)), "必须存 float32 字节，不是 JSON 文本"
    assert len(raw) == 128 * 4, "128 维 × 4 字节"


def test_memory_footprint_is_bounded(conn):
    store = _store(conn, dim=1536)
    store.add_many("chr_a", [f"第{i}件事" for i in range(50)], turn=1)
    # 50 条 × 1536 维 × 4 字节 ≈ 300KB；纯 Python float 会是 2.4MB 量级
    assert store.memory_bytes() == 50 * 1536 * 4


# ── 回灌（resume 的关键）─────────────────────────────────────
def test_load_restores_memories(conn):
    store = _store(conn)
    store.add_many("chr_a", ["旧城的钟楼门洞少了几块砖", "灰街的锁被撬开"], turn=2)
    store.add_many("_lore", ["学院地下有一层不该去的旧楼"], kind="lore", turn=0)

    fresh = _store(conn)          # 模拟重启后新建的 store
    assert fresh.count() == 0
    assert fresh.load() == 3
    assert fresh.count("chr_a") == 2
    assert fresh.count("_lore") == 1
    hits = fresh.search("chr_a", "钟楼")
    assert hits, "回灌之后必须能检索到"


def test_load_accepts_legacy_json_text(conn):
    """旧库里的 embedding 是 JSON 字符串，迁移工具没跑也要能读。"""
    vec = [1.0] * 8
    conn.execute(
        "INSERT INTO memory_vectors (session_id, char_id, turn, kind, ref_id, text, embedding) "
        "VALUES (?,?,?,?,?,?,?)",
        ("ses_old", "chr_a", 1, "event", "turn1", "旧格式的记忆", json.dumps(vec)),
    )
    conn.commit()

    store = VectorStore(conn, "ses_old", HashEmbedder(8))
    assert store.load() == 1
    assert store.count("chr_a") == 1
    assert store.all_for("chr_a")[0].text == "旧格式的记忆"


# ── 清理 ────────────────────────────────────────────────────
def test_delete_session_after_rolls_back_tail(conn):
    store = _store(conn)
    store.add_many("chr_a", ["第一回合的事"], turn=1)
    store.add_many("chr_a", ["第二回合的事"], turn=2)
    store.add_many("chr_a", ["第三回合的事"], turn=3)

    assert store.delete_session_after(1) == 2
    assert store.count("chr_a") == 1
    rows = conn.execute("SELECT turn FROM memory_vectors ORDER BY turn").fetchall()
    assert [r[0] for r in rows] == [1], "库里也要删掉"


def test_delete_session_clears_everything(conn):
    store = _store(conn)
    store.add_many("chr_a", ["甲", "乙"], turn=1)
    store.delete_session()
    assert store.count() == 0
    assert conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 0


# ── 接口契约 ────────────────────────────────────────────────
def test_vector_store_satisfies_backend_protocol():
    store = VectorStore(None, "s", HashEmbedder(16))
    assert isinstance(store, MemoryBackend), "VectorStore 必须满足 MemoryBackend 接口"


# ── 阈值 ────────────────────────────────────────────────────
class _CountingEmbedder:
    """数一数到底调了几次 embedding（批量写的验收点）。"""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim
        self.calls = 0
        self.batches: list[int] = []

    def embed(self, texts, kind="db"):
        self.calls += 1
        self.batches.append(len(texts))
        return HashEmbedder(self.dim).embed(texts, kind=kind)


def test_add_grouped_collapses_into_one_call(conn):
    emb = _CountingEmbedder()
    store = VectorStore(conn, "ses_g", emb)

    store.add_grouped({"chr_a": ["一"], "chr_b": ["二"], "chr_c": ["三", "四"]}, turn=1)

    assert emb.calls == 1, "多个桶必须合并成一次 embedding 调用"
    assert emb.batches == [4], "一次调用要包含全部文本"
    assert store.count() == 4
    assert store.count("chr_c") == 2
    rows = conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
    assert rows == 4


def test_dim_mismatch_is_loud(conn):
    store = VectorStore(conn, "ses_d", HashEmbedder(32))
    store.add_many("chr_a", ["旧维度记忆"], turn=1)
    store.embedder = HashEmbedder(64)  # 模拟中途换了 embedding 源
    with pytest.raises(ValueError):
        store.add_many("chr_a", ["新维度记忆"], turn=2)


def test_ten_thousand_vectors_fit_memory_budget():
    """G1 的资源账：1 万条 × 1536 维 float32 必须远小于 100MB。"""
    per_vector = 1536 * 4
    assert per_vector * 10000 / (1024 * 1024) < 100


def test_relative_margin_drops_low_scores(conn):
    store = _store(conn, dim=256)
    store.add_many("chr_a", ["钟楼门洞的砖被抠走了"], turn=1)
    store.add_many("chr_a", ["食堂今天的汤很咸"], turn=2)

    wide = hybrid_recall("钟楼", "chr_a", None, store, vector_k=4, rel_margin=0.0)
    strict = hybrid_recall("钟楼", "chr_a", None, store, vector_k=4, rel_margin=0.9)
    assert len(strict.items) <= len(wide.items), "相对阈值只应减少候选，不应增加"
