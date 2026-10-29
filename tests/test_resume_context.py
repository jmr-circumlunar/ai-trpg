"""读档不再失忆：短期叙事与章节摘要落库 + 回灌（B25）。"""

import sqlite3
from pathlib import Path

from trpg.agents import AgentLLM, LLMConfig, MockLLM
from trpg.engine.loop import GameLoop
from trpg.game import hydrate_llm
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.rules import Rules
from trpg.storage import db
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]
SID = "ses_resume"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db.init_db(conn)
    return conn


def _state() -> GameState:
    card = make_card()
    return GameState(
        session_id=SID,
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_a": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )


# ── 存取层 ────────────────────────────────────────────────


def test_summary_roundtrip_keeps_chronological_order():
    conn = _conn()
    db.save_summary(conn, SID, 10, "第6~10回合：乙")
    db.save_summary(conn, SID, 5, "第1~5回合：甲")

    assert db.load_summaries(conn, SID) == ["第1~5回合：甲", "第6~10回合：乙"]


def test_save_summary_is_idempotent_per_turn():
    conn = _conn()
    db.save_summary(conn, SID, 5, "旧")
    db.save_summary(conn, SID, 5, "新")

    assert db.load_summaries(conn, SID) == ["新"], "同一 upto_turn 重写不应产生两行"


def test_delete_summaries_after_rollback_turn():
    conn = _conn()
    db.save_summary(conn, SID, 5, "甲")
    db.save_summary(conn, SID, 10, "乙")

    db.delete_summaries_after(conn, SID, 5)

    assert db.load_summaries(conn, SID) == ["甲"]


def test_recent_narrations_returns_chronological_tail():
    conn = _conn()
    for turn in (1, 2, 3):
        db.save_turn(conn, SID, turn, "chr_p1", "", "行动", f"叙事{turn}")

    assert db.recent_narrations(conn, SID, limit=2) == ["叙事2", "叙事3"]


# ── 回灌 ──────────────────────────────────────────────────


def test_agent_hydrate_restores_context():
    agent = AgentLLM(LLMConfig(ROOT), {}, seeds=[])
    agent.hydrate(recent_public=["你推开了门。", "锁芯松了。"], summaries=["第1~5回合：甲"])

    assert agent._recent_public == ["你推开了门。", "锁芯松了。"]
    assert agent._summaries == ["第1~5回合：甲"]


def test_hydrate_llm_pulls_from_db():
    conn = _conn()
    for turn in (1, 2):
        db.save_turn(conn, SID, turn, "chr_p1", "", "行动", f"叙事{turn}")
    db.save_summary(conn, SID, 5, "第1~5回合：甲")

    seen: dict = {}

    class FakeLLM:
        def hydrate(self, recent_public=None, summaries=None):
            seen["recent"] = recent_public
            seen["summaries"] = summaries

    hydrate_llm(FakeLLM(), conn, SID, recent_turns=1)

    assert seen["recent"] == ["叙事1", "叙事2"]
    assert seen["summaries"] == ["第1~5回合：甲"]


# ── 落库：编排层注入 sink ─────────────────────────────────


class SinkLLM(MockLLM):
    def set_summary_sink(self, sink) -> None:
        self.sink = sink


def _loop(llm) -> GameLoop:
    cards = {"chr_p1": make_card()}
    return GameLoop(Rules(ROOT), cards, _state(), llm, seed=1, conn=_conn())


def test_loop_injects_summary_sink_and_persists():
    llm = SinkLLM(1)
    loop = _loop(llm)

    assert callable(getattr(llm, "sink", None)), "有 db 连接时必须注入 sink"

    loop._persist_summary("第1~5回合：甲", 5)
    assert db.load_summaries(loop.conn, SID) == ["第1~5回合：甲"]
