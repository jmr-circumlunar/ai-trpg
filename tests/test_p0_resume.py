"""P0-T8 / P0-T9：恢复后的回合历史、回合号、世界知识桶。

“重启接着玩”是这个项目最基本的承诺，但之前：
- `Session.records` 不还原 → trace 回合空白
- `loop.next_turn` 用记忆条数推算 → 记忆被压缩后回合号会错
- `seed_lore` 只在新建局调用 → 恢复后「回忆」拿不到世界知识
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app

app_mod = importlib.import_module("trpg.api.deps")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "resume.db")
    monkeypatch.setattr(app.state, "sessions", {})
    with TestClient(app) as c:
        yield c


def _new_session(client: TestClient, seed: str = "31") -> str:
    res = client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": seed},
    )
    assert res.status_code == 200, res.text
    return res.json()["session_id"]


def _restart() -> None:
    """模拟服务重启：内存里的会话全丢。"""
    app.state.sessions.clear()


def test_trace_falls_back_to_db_after_resume(client):
    sid = _new_session(client)
    for _ in range(2):
        assert client.post(f"/api/sessions/{sid}/advance").status_code == 200

    before = client.get(f"/api/sessions/{sid}/trace").json()
    assert before["turns_source"] == "memory"
    assert len(before["turns"]) == 2

    _restart()
    assert client.post(f"/api/sessions/{sid}/resume?provider=mock").status_code == 200

    after = client.get(f"/api/sessions/{sid}/trace").json()
    assert after["turns_source"] == "db", "恢复后应从库里重建回合历史"
    assert len(after["turns"]) == 2, "恢复后回合历史不能空白"
    assert all(t["actors"] for t in after["turns"]), "每回合要知道是谁行动的"
    assert any(t["narration"] for t in after["turns"]), "回合叙事要能读回来"


def test_resume_continues_turn_numbering(client):
    sid = _new_session(client, "32")
    for _ in range(2):
        client.post(f"/api/sessions/{sid}/advance")

    _restart()
    client.post(f"/api/sessions/{sid}/resume?provider=mock")

    nxt = client.post(f"/api/sessions/{sid}/advance").json()
    assert nxt["turn"] == 3, "恢复后回合号要接着数，不能退回第 1 回合"


def test_resume_keeps_character_memories(client):
    """P2 退出验证：恢复后角色记忆（不只是世界知识）不能丢。"""
    sid = _new_session(client, "34")
    assert client.post(f"/api/sessions/{sid}/advance").status_code == 200

    before = app.state.sessions[sid].loop.vectors.count()
    assert before > 0, "推进一回合后应该有记忆写入（批量写也走这条路）"

    _restart()
    assert client.post(f"/api/sessions/{sid}/resume?provider=mock").status_code == 200
    after = app.state.sessions[sid].loop.vectors.count()
    assert after >= before, f"恢复后记忆少了：{before} → {after}"


def test_resume_rebuilds_lore_bucket(client):
    sid = _new_session(client, "33")
    _restart()
    assert client.post(f"/api/sessions/{sid}/resume?provider=mock").status_code == 200

    session = app.state.sessions[sid]
    assert session.loop.vectors.count("_lore") > 0, "恢复后世界知识桶必须重建"
