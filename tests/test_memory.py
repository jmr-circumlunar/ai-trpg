"""角色记忆：写入、按角色隔离查询、注入 prompt、持久化。"""

import asyncio
import json
import sqlite3
from pathlib import Path

from trpg.engine.loop import GameLoop
from trpg.llm import AgentLLM, LLMConfig, MockLLM
from trpg.llm.providers.base import ChatResult
from trpg.models.memory import CharacterEvent
from trpg.storage import db
from trpg.storage.memory import EventStore
from trpg.testing import make_card, make_state
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.rules.loader import Rules

ROOT = Path(__file__).resolve().parents[1]


def ev(cid: str, turn: int, summary: str, importance: int = 5) -> CharacterEvent:
    return CharacterEvent(
        session_id="s1", char_id=cid, turn=turn, summary=summary, importance=importance
    )


# ── 存取 ─────────────────────────────────────────────────
def test_add_and_recent():
    store = EventStore()
    for i in range(1, 8):
        store.add(ev("chr_a", i, f"事件{i}"))
    assert [e.turn for e in store.recent("chr_a", limit=3)] == [5, 6, 7]


def test_isolation_between_characters():
    store = EventStore()
    store.add(ev("chr_a", 1, "A 的秘密"))
    store.add(ev("chr_b", 1, "B 的事"))
    assert [e.summary for e in store.recent("chr_b")] == ["B 的事"]


def test_search():
    store = EventStore()
    store.add(ev("chr_a", 1, "撬锁·success：打开账房侧门"))
    store.add(ev("chr_a", 2, "交涉·failure：老妇不再理他"))
    assert len(store.search("chr_a", "撬锁")) == 1
    assert store.search("chr_a", "不存在") == []


def test_important_first():
    store = EventStore()
    store.add(ev("chr_a", 1, "小事", 3))
    store.add(ev("chr_a", 2, "大事", 9))
    assert store.important("chr_a", threshold=7)[0].summary == "大事"


def test_persists_to_sqlite():
    conn = sqlite3.connect(":memory:")
    db.init_db(conn)
    store = EventStore(conn, "s1")
    store.add(ev("chr_a", 1, "写进库"))
    rows = conn.execute("SELECT char_id, summary FROM character_events").fetchall()
    assert rows == [("chr_a", "写进库")]


# ── 回合写入 ──────────────────────────────────────────────
def _loop_with_store(store: EventStore) -> GameLoop:
    rules = Rules(ROOT)
    cards = {"chr_p1": make_card(), "chr_p2": make_card(profession="战士")}
    state = GameState(
        session_id="ses_mem",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=2),
        characters={
            cid: CharacterRuntime(hp=c.ext.hp, sanity=c.ext.sanity)
            for cid, c in cards.items()
        },
    )
    return GameLoop(rules, cards, state, MockLLM(42), seed=42, events=store)


def test_loop_records_one_event_per_actor_per_turn():
    store = EventStore()
    asyncio.run(_loop_with_store(store).run(3))
    assert len(store.all_for("chr_p1")) == 3
    assert len(store.all_for("chr_p2")) == 3
    assert store.all_for("chr_p1")[0].turn == 1


def test_event_carries_time_and_location():
    store = EventStore()
    asyncio.run(_loop_with_store(store).run(2))
    first = store.all_for("chr_p1")[0]
    assert first.day >= 1
    assert "loc_a" in first.entities


# ── 注入 prompt ──────────────────────────────────────────
class Recorder:
    """第一步调 recall，第二步 final（模拟 ReAct）。"""

    kind = "rec"

    def __init__(self, query: str = "撬锁") -> None:
        self.calls: list[list[dict]] = []
        self.query = query

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.calls.append(messages)
        if len(self.calls) == 1:
            payload = {"type": "tool", "tool": "recall", "args": {"query": self.query}}
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


def test_recall_tool_injects_memory():
    """回忆不是自动注入的——Agent 调了 recall 才会看到。"""
    cfg = LLMConfig(ROOT)
    cards = {"chr_a": make_card(), "chr_b": make_card(profession="战士")}
    rec = Recorder()
    cfg._adapters = {"minimax": rec}

    store = EventStore()
    store.add(ev("chr_a", 1, "撬锁·success：打开账房侧门", 8))
    store.add(ev("chr_b", 1, "交涉·failure：被人轰出去", 6))

    agent = AgentLLM(cfg, cards, events=store)
    agent.player_action("chr_a", cards["chr_a"], make_state(), 2)

    assert len(rec.calls) == 2, "应先调 tool 再 final"
    second = json.dumps(rec.calls[1], ensure_ascii=False)
    assert "撬锁·success" in second  # 自己回忆出来的
    assert "交涉·failure" not in second  # 别人的记忆不会串台


def test_no_recall_by_default():
    """不调 tool 时，prompt 里不应出现任何长记忆（只有近期上下文）。"""
    cfg = LLMConfig(ROOT)
    cards = {"chr_a": make_card()}
    rec = Recorder()
    cfg._adapters = {"minimax": rec}
    store = EventStore()
    store.add(ev("chr_a", 1, "很久以前的事：撬锁成功", 8))

    agent = AgentLLM(cfg, cards, events=store)
    # 直接返回 final（不调 tool）
    rec.query = ""
    agent._tool_call = lambda *a, **k: []  # 不会走到这里
    rec.chat = lambda messages, model, temperature=0.9, max_tokens=1200, extra_override=None: (
        rec.calls.append(messages)
        or ChatResult(
            content=json.dumps(
                {"type": "final", "thought": "想", "action": "做", "skill": "观察"},
                ensure_ascii=False,
            ),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
    )
    agent.player_action("chr_a", cards["chr_a"], make_state(), 2)

    assert len(rec.calls) == 1
    prompt = json.dumps(rec.calls[0], ensure_ascii=False)
    assert "很久以前的事" not in prompt


def test_no_events_degrades_gracefully():
    cfg = LLMConfig(ROOT)
    cards = {"chr_a": make_card()}
    rec = Recorder()
    cfg._adapters = {"minimax": rec}
    agent = AgentLLM(cfg, cards, events=None)
    action = agent.player_action("chr_a", cards["chr_a"], make_state(), 1)
    assert action.action
