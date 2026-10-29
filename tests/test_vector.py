"""向量记忆：embedding、检索、混合召回、召回日志。"""

import json
from pathlib import Path

from trpg.agents import AgentLLM, LLMConfig
from trpg.agents.providers.base import ChatResult
from trpg.memory import HashEmbedder, VectorStore, cosine, hybrid_recall
from trpg.models.memory import CharacterEvent
from trpg.storage.memory import EventStore
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]
EMB = HashEmbedder(dim=256)


def _store() -> VectorStore:
    return VectorStore(None, "s1", EMB)


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([1, 0], [1, 0, 0]) == 0.0  # 维度不一致


def test_hash_embedder_similarity_order():
    a = EMB.embed(["钟楼的铁栅被人新焊过"])[0]
    b = EMB.embed(["铁栅是新焊的，就在钟楼下"])[0]
    c = EMB.embed(["鱼市今天鱼很便宜"])[0]
    assert cosine(a, b) > cosine(a, c)


def test_store_add_and_search():
    store = _store()
    store.add_many("chr_a", ["在钟楼下撬锁失败", "在鱼市问到了旧账册"])
    hits = store.search("chr_a", "钟楼 铁栅", top_k=2)
    assert hits
    assert "钟楼" in hits[0][0].text


def test_store_isolation():
    store = _store()
    store.add_many("chr_a", ["A 的秘密"])
    store.add_many("chr_b", ["B 的事"])
    hits = store.search("chr_b", "秘密", top_k=5)
    assert all("A 的秘密" not in item.text for item, _ in hits)


def test_store_persists(tmp_path):
    import sqlite3

    from trpg.storage import db

    conn = sqlite3.connect(":memory:")
    db.init_db(conn)
    store = VectorStore(conn, "s1", EMB)
    store.add_many("chr_a", ["写进库的一条"])
    rows = conn.execute("SELECT char_id, text FROM memory_vectors").fetchall()
    assert rows == [("chr_a", "写进库的一条")]


# ── 混合召回 ─────────────────────────────────────────────
def _events() -> EventStore:
    store = EventStore()
    store.add(
        CharacterEvent(
            session_id="s1",
            char_id="chr_a",
            turn=1,
            summary="撬锁·crit_success：打开账房侧门",
            importance=8,
        )
    )
    store.add(
        CharacterEvent(
            session_id="s1",
            char_id="chr_a",
            turn=2,
            summary="交涉·failure：老妇不再理他",
            importance=5,
        )
    )
    return store


def test_hybrid_recall_merges_sources():
    vectors = _store()
    vectors.add_many("chr_a", ["在钟楼下撬锁失败，铁栅是新焊的"])
    result = hybrid_recall("钟楼 铁栅", "chr_a", _events(), vectors)

    assert result.items
    sources = {i.source for i in result.items}
    assert "important" in sources or "recent" in sources
    assert result.debug["injected"] == len(result.items)
    assert "vector_hits" in result.debug


def test_hybrid_recall_without_vectors():
    result = hybrid_recall("任意", "chr_a", _events(), None)
    assert result.items
    assert "vector_hits" not in result.debug


def test_hybrid_recall_empty():
    result = hybrid_recall("任意", "chr_nobody", EventStore(), None)
    assert result.items == []


def test_recall_respects_limit():
    vectors = _store()
    vectors.add_many("chr_a", [f"第{i}次尝试：撬锁" for i in range(10)])
    result = hybrid_recall("撬锁", "chr_a", _events(), vectors, limit=3)
    assert len(result.items) <= 3


# ── 召回日志 ─────────────────────────────────────────────
class Echo:
    """第一步调 recall，第二步 final。"""

    kind = "echo"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.calls += 1
        if self.calls == 1:
            payload = {"type": "tool", "tool": "recall", "args": {"query": "钟楼 铁栅"}}
        else:
            payload = {
                "type": "final",
                "thought": "想",
                "action": "做",
                "skill": "观察",
                "intent": "normal",
            }
        return ChatResult(
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_recall_log_written():
    cfg = LLMConfig(ROOT)
    cfg._adapters = {"minimax": Echo()}
    cards = {"chr_a": make_card()}
    events = _events()
    vectors = _store()
    vectors.add_many("chr_a", ["钟楼下的铁栅"])

    agent = AgentLLM(cfg, cards, events=events, vectors=vectors)
    agent.player_action("chr_a", cards["chr_a"], make_state(), 3)

    assert agent.recall_log, "调了 recall 就应有日志"
    entry = agent.recall_log[-1]
    assert entry["char_id"] == "chr_a"
    assert "items" in entry and "debug" in entry
    assert entry["debug"].get("vector_hits") is not None
