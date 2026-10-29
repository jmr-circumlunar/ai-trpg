"""经验接线：char→agent 映射、开局注入、局末写回、开关。

接线前有两个真空点：

1. `sessions.agent_ids_json` **只写不读** → 库里存着绑定，代码里从没消费过；
2. `AgentLLM` 没有 agent 标识 → `reflect()` 写出来的经验**无处可归**。

测试不硬编码角色 id（真实 id 形如 `chr_p1`，与旧世界的 `chr_1` 不同），
一律从映射里取，避免「测试写死了一个不存在的 id 却看不出来」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.api import deps  # noqa: E402
from trpg.api.app import app  # noqa: E402
from trpg.storage.experience import add_experience, list_experience  # noqa: E402

WORLD = "werewolf"


@pytest.fixture
def client():
    with TestClient(app) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean_experience():
    """每个用例从空表开始——经验是跨局数据，不隔离会互相污染。"""
    conn = deps._new_conn()
    try:
        conn.execute("DELETE FROM agent_experience")
        conn.commit()
    finally:
        conn.close()
    yield


def _db():
    return deps._new_conn()


def _login(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})


def _session(client: TestClient, players: int = 4, agent_ids: list[str] | None = None) -> str:
    _login(client)
    payload = {"world": WORLD, "players": players, "provider": "mock", "seed": "5"}
    if agent_ids is not None:
        payload["agent_ids"] = agent_ids
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["session_id"]


def test_every_character_gets_a_stable_agent(client):
    sid = _session(client)
    session = app.state.sessions[sid]

    assert session.char_to_agent, "建局后应当定下 char→agent 映射"
    assert all(session.char_to_agent.values()), "每个角色都要有 agent 标识"

    for cid, agent_id in session.char_to_agent.items():
        # 没显式绑定时派生 `<world>:<char>`，同世界同席位跨局稳定
        assert agent_id == f"{WORLD}:{cid}", cid


def test_explicit_agent_ids_win(client):
    # 建局会校验 agent 存在（不能绑一个不存在的 agent），所以先落两条档案
    conn = _db()
    try:
        for aid in ("agt_a", "agt_b"):
            conn.execute(
                "INSERT OR IGNORE INTO agents (id, name, created_at) VALUES (?, ?, '')",
                (aid, aid),
            )
        conn.commit()
    finally:
        conn.close()

    sid = _session(client, players=2, agent_ids=["agt_a", "agt_b"])
    mapping = app.state.sessions[sid].char_to_agent

    # 显式绑定的两个席位优先；其余席位派生 id（players 与实际建卡数可能不同）
    assert list(mapping.values())[:2] == ["agt_a", "agt_b"]


def test_mapping_is_also_visible_to_llm(client):
    """映射要回灌到 llm——局末 `reflect` 靠它决定经验写谁。"""
    sid = _session(client)
    session = app.state.sessions[sid]

    assert session.loop.llm.agent_ids == session.char_to_agent


def test_experience_is_loaded_into_prompt_slot(client):
    probe = _session(client)
    cid = next(iter(app.state.sessions[probe].char_to_agent))
    agent_id = f"{WORLD}:{cid}"

    conn = _db()
    try:
        add_experience(conn, agent_id, WORLD, "别第一个跳预言家")
    finally:
        conn.close()

    sid = _session(client)
    session = app.state.sessions[sid]
    assert "别第一个跳预言家" in session.loop.llm.experience.get(cid, [])


def test_finish_writes_experience_back_to_the_same_agent(client):
    """局末提炼要把经验写回该角色的 agent。

    **注意 `advance` 可能已经终局**：小局里村民一回合就被杀光，判胜在 `advance`
    内部触发 `_finish`，经验那时已经写了。所以这里不假设「必须手动 `_finish`」，
    而是看最终结果——这也更贴近真实用法。
    """
    sid = _session(client)
    session = app.state.sessions[sid]
    cid = next(iter(session.char_to_agent))

    client.post(f"/api/sessions/{sid}/advance", json={})

    if session.status != "ended":
        deps._finish(session, kind="ended", turn=1)

    conn = _db()
    try:
        learned = list_experience(conn, f"{WORLD}:{cid}", WORLD)
    finally:
        conn.close()
    assert learned, "经验要写回该角色对应的 agent"


def test_learned_experience_shows_up_next_session(client):
    """上一局学到的，下一局开局就该在 prompt 槽位里——这是「跨局」的定义。"""
    sid = _session(client)
    client.post(f"/api/sessions/{sid}/advance", json={})
    first = app.state.sessions[sid]
    cid = next(iter(first.char_to_agent))
    deps._finish(first, kind="ended", turn=1)

    next_sid = _session(client)
    bucket = app.state.sessions[next_sid].loop.llm.experience

    assert bucket.get(cid), "新一局应当载入上一局的教训"


def test_disabled_system_reads_and_writes_nothing(client, monkeypatch):
    """`systems.experience` 关掉时连读都不读（不是读了不用）。"""
    from trpg.api.deps import Session

    probe = _session(client)
    cid = next(iter(app.state.sessions[probe].char_to_agent))
    monkeypatch.setattr(Session, "experience_enabled", lambda self: False)

    conn = _db()
    try:
        add_experience(conn, f"{WORLD}:{cid}", WORLD, "这条不该被读到")
    finally:
        conn.close()

    sid = _session(client)
    session = app.state.sessions[sid]
    assert session.loop.llm.experience.get(cid) is None

    client.post(f"/api/sessions/{sid}/advance", json={})
    payload = deps._finish(session, kind="ended", turn=1)

    assert payload.get("experience_learned", 0) == 0
