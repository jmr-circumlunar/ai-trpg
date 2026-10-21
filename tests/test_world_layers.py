"""世界包分层：core 常驻 / region 按需 / archive 进向量。"""

from pathlib import Path

from trpg.game import seed_lore
from trpg.llm import prompts
from trpg.memory import HashEmbedder, VectorStore, hybrid_recall
from trpg.storage.memory import EventStore
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]


def _pack():
    return load_world("ironvale", ROOT)


def test_core_loaded():
    pack = _pack()
    assert pack.core_text.strip(), "core 应有常驻设定"
    assert "铁律" in pack.core_text


def test_archive_loaded():
    pack = _pack()
    assert pack.archive_docs, "archive 应有长文"
    titles = " ".join(d["path"] for d in pack.archive_docs)
    assert "glossary" in titles
    assert any("灰钟" in d["text"] for d in pack.archive_docs)


def test_region_text_on_demand():
    pack = _pack()
    assert "北港" in pack.region_text("north_port")
    assert pack.region_text("no_such_region") == ""


def test_core_injected_into_gm_prompt():
    system = prompts.gm_system("铁谷", "medieval", "冷峻", [], world_core="世界铁律XYZ")
    assert "世界铁律XYZ" in system
    assert "常驻" in system


def test_lore_seeded_into_global_bucket():
    pack = _pack()
    store = VectorStore(None, "s", HashEmbedder(256))
    count = seed_lore(store, pack)
    assert count >= 1
    assert store.count("_lore") == count


def test_lore_hit_by_recall():
    pack = _pack()
    store = VectorStore(None, "s", HashEmbedder(256))
    seed_lore(store, pack)

    result = hybrid_recall("灰钟是什么", "chr_a", EventStore(), store, vector_k=3)
    hits = result.debug.get("vector_hits") or []
    assert hits, "查询世界名词应命中 archive"

    store.add_many("chr_a", ["在钟楼下撬锁失败"]) if hasattr(store, "add_many") else None


def test_character_memory_and_lore_share_search():
    pack = _pack()
    store = VectorStore(None, "s", HashEmbedder(256))
    seed_lore(store, pack)
    store.add_many("chr_a", ["我在钟楼下丢了撬锁铁片"])

    result = hybrid_recall("钟楼", "chr_a", EventStore(), store, vector_k=4)
    all_hits = result.debug.get("vector_hits") or []
    assert all_hits, "应同时从角色桶与世界桶召回"
