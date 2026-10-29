"""模型调用指标：task / token / 耗时 / 重试必须真实落库（B27）。

口径：一行 = 一次真实 API 调用；`agent` 是角色（gm/npc/summary/ending），
`task` 是任务（scene/narrate/player/summary/ending）。记账点在 `AgentBase._chat_json`。
"""

import asyncio
import json
import sqlite3
from pathlib import Path

from trpg.agents import AgentLLM, LLMConfig, MockLLM
from trpg.agents.providers.base import ChatResult
from trpg.engine.loop import GameLoop
from trpg.models import (
    CharacterRuntime,
    GameState,
    Party,
    PlayerAction,
    Scene,
    WorldRef,
)
from trpg.models.enums import Intent
from trpg.rules import DiceEngine, Rules
from trpg.rules.judge import judge
from trpg.storage import db as store_db
from trpg.world import load_world
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]
PACK = load_world("ironvale", ROOT)
SCENE_PAYLOAD = {"narration": "巷子尽头有一盏灭了的灯。", "hooks": [], "present_npcs": []}
NARRATE_PAYLOAD = {
    "narrations": [{"actor_id": "chr_a", "text": "锁芯松了。"}],
    "state_delta": None,
}


class Canned:
    """按顺序吐固定内容的假 provider（第一次可以故意不合法）。"""

    kind = "canned"

    def __init__(self, payloads: list) -> None:
        self.payloads = list(payloads)
        self.calls: list[list[dict]] = []

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.calls.append(messages)
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return ChatResult(
            content=content,
            usage={"prompt_tokens": 120, "completion_tokens": 60},
            model=model,
        )


def _state() -> GameState:
    card = make_card()
    return GameState(
        session_id="ses_calls",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_a": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )


def _agent(provider: Canned) -> AgentLLM:
    cfg = LLMConfig(ROOT)
    cfg._adapters = {"minimax": provider}
    return AgentLLM(cfg, {"chr_a": make_card()}, seeds=PACK.seeds, seed="42")


def _judged(state: GameState):
    action = PlayerAction(
        actor_id="chr_a",
        action="用撬锁试探锁孔",
        approach="贴着墙",
        skill="撬锁",
        intent=Intent.careful,
    )
    return action, judge(action, make_card(), state, Rules(ROOT), DiceEngine("t1"))


# ── 记账点：AgentBase._chat_json ────────────────────────────


def test_agent_base_records_task_tokens_and_model():
    state = _state()
    action, result = _judged(state)
    agent = _agent(Canned([SCENE_PAYLOAD, NARRATE_PAYLOAD]))

    agent.describe_scene(state, 1)
    agent.narrate_batch(state, [action], [result], 1)

    records = agent.drain_calls()
    assert [r["task"] for r in records] == ["scene", "narrate"]
    assert all(r["agent"] == "gm" for r in records), "角色由 TASK_TIER 推导"
    assert all(r["tokens_in"] == 120 and r["tokens_out"] == 60 for r in records)
    assert all("/" in r["model"] for r in records), "model 必须带 provider 前缀"
    assert all(r["latency_ms"] >= 0 for r in records)
    assert agent.drain_calls() == [], "drain 后必须清空，避免重复落库"


def test_json_retry_marks_retry_flag():
    state = _state()
    agent = _agent(Canned(["这不是 JSON", SCENE_PAYLOAD]))

    agent.describe_scene(state, 1)

    records = agent.drain_calls()
    # 一次不合法响应也是一次真实调用（花了 token），所以记两行，第二行标重试
    assert len(records) == 2
    assert [r["retry"] for r in records] == [0, 1]
    assert all(r["tokens_in"] == 120 for r in records)


# ── 落库点：loop._persist_calls ────────────────────────────


class MetricsLLM(MockLLM):
    """模拟 AgentLLM 的 drain 接口，用来验证编排层落库口径。"""

    def __init__(self, seed, records: list[dict]) -> None:
        super().__init__(seed)
        self._records = list(records)

    def drain_calls(self) -> list[dict]:
        out, self._records = self._records, []
        return out


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    store_db.init_db(conn)
    return conn


def _loop(llm, conn=None) -> GameLoop:
    cards = {f"chr_p{i + 1}": make_card(profession=["盗贼", "战士"][i]) for i in range(2)}
    state = GameState(
        session_id="ses_calls_loop",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42, conn=conn)


def test_persist_calls_writes_one_row_per_call():
    conn = _conn()
    llm = MetricsLLM(
        1,
        [
            {"task": "scene", "agent": "gm", "model": "a/b", "tokens_in": 120,
             "tokens_out": 80, "latency_ms": 43, "retry": 0},
            {"task": "player", "agent": "npc", "model": "a/b", "tokens_in": 200,
             "tokens_out": 50, "latency_ms": 88, "retry": 1},
        ],
    )
    loop = _loop(llm, conn)
    loop._persist_calls(1)

    rows = conn.execute(
        "SELECT task, agent, tokens_in, tokens_out, latency_ms, retry "
        "FROM agent_calls ORDER BY task"
    ).fetchall()
    assert rows == [
        ("player", "npc", 200, 50, 88, 1),
        ("scene", "gm", 120, 80, 43, 0),
    ]

    loop._persist_calls(1)  # 已 drain 过，不得重复写
    assert conn.execute("SELECT count(*) FROM agent_calls").fetchone()[0] == 2


def test_turn_records_calls_once_not_per_actor():
    """回归：以前按 actor 循环写 agent_calls，同一回合会重复多行且全叫 gm。"""
    conn = _conn()
    llm = MetricsLLM(
        5,
        [
            {"task": "scene", "agent": "gm", "model": "a/b", "tokens_in": 100,
             "tokens_out": 40, "latency_ms": 30, "retry": 0},
            {"task": "narrate", "agent": "gm", "model": "a/b", "tokens_in": 300,
             "tokens_out": 120, "latency_ms": 60, "retry": 0},
        ],
    )
    loop = _loop(llm, conn)
    asyncio.run(loop.run(1))

    rows = [r[0] for r in conn.execute("SELECT task FROM agent_calls").fetchall()]
    assert sorted(rows) == ["narrate", "scene"], "2 个玩家也只写 2 行（一行一次调用）"


def test_old_db_gets_task_column():
    """旧库不重建、不丢数据，只补 task 列。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE agent_calls (session_id TEXT, turn INTEGER, agent TEXT, model TEXT, "
        "tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, request_json TEXT, "
        "response_raw TEXT, retry INTEGER DEFAULT 0)"
    )
    store_db.init_db(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_calls)")}
    assert "task" in cols
