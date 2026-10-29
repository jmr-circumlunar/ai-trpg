"""RRF 融合键必须是稳定标识（B24）。

同一件事在结构化路带「第N回合」前缀、在向量路只有正文，用文本当键永不相等，
RRF 就退化成「两路各占名额」。融合键改为 `ref`（退化文本）。
"""

from trpg.memory.recall import hybrid_recall
from trpg.memory.vector_store import MemoryVector
from trpg.models.memory import CharacterEvent
from trpg.storage.memory import EventStore

K = 7  # rrf_k，让期望分数好算：单路贡献 = 1/(K+rank+1)


def _event(turn: int, summary: str, importance: int = 8, char_id: str = "chr_a") -> CharacterEvent:
    return CharacterEvent(
        session_id="s",
        char_id=char_id,
        turn=turn,
        day=1,
        hour=20,
        summary=summary,
        importance=importance,
    )


class FakeVectors:
    """只回固定命中，不碰 embedding。"""

    def __init__(self, hits: list[tuple[MemoryVector, float]]) -> None:
        self.hits = hits

    def search(self, buckets, query, top_k=4, min_score=0.0):
        return list(self.hits)


def _vec(turn: int, text: str, ref_id: str, char_id: str = "chr_a") -> MemoryVector:
    return MemoryVector(char_id=char_id, text=text, vector=[0.0], turn=turn, ref_id=ref_id)


def test_same_ref_merges_two_paths():
    events = EventStore()
    events.add(_event(3, "撬锁（success）"))
    vectors = FakeVectors([(_vec(3, "撬锁（success）", "turn3"), 0.91)])

    result = hybrid_recall("撬锁", "chr_a", events, vectors, rrf_k=K, limit=6)

    assert len(result.items) == 1, "同一件事必须融合成一条"
    # important / recent / vector 三路都是 rank 0（只有一条候选）
    assert result.items[0].score == 3 / (K + 1)
    assert result.debug["fused"] == 1


def test_different_ref_does_not_merge():
    events = EventStore()
    events.add(_event(1, "潜行（success）", importance=4))
    events.add(_event(2, "观察（failure）", importance=4))
    vectors = FakeVectors(
        [(_vec(2, "观察（failure）", "turn2"), 0.8), (_vec(1, "潜行（success）", "turn1"), 0.7)]
    )

    result = hybrid_recall("四周", "chr_a", events, vectors, rrf_k=K, limit=6)

    assert len(result.items) == 2
    assert {it.ref for it in result.items} == {"turn1", "turn2"}


def test_missing_ref_falls_back_to_text():
    """没有 ref 的历史向量不能全塌进同一个空键。"""
    events = EventStore()
    vectors = FakeVectors(
        [(_vec(5, "旧事甲", ""), 0.9), (_vec(6, "旧事乙", ""), 0.8)]
    )

    result = hybrid_recall("旧事", "chr_a", events, vectors, rrf_k=K, limit=6)

    assert len(result.items) == 2
    assert result.debug["fused"] == 2
