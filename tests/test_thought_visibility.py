"""思维过程（thought）的可见性：三档视角。

| 视角 | 谁能看到别人的盘算 |
|---|---|
| 上帝（没有人以参与方身份在看） | 全部 |
| 参与者（存活角色） | 按世界包 `visibility.thoughts`：`god` = 看不到别人；`participant` = 互相可见；`all` = 全部 |
| **出局者**（完成遗言后） | 全部——死后不再是博弈方，理应能复盘全局 |

这条是**输出侧**的裁剪：prompt 层 thought 本来就是私有的（不进任何 prompt），
但 API 层以前对所有 actor 无差别下发，前端会把别人的盘算显示给当前玩家。

测试用**构造的回执**而不是真实模型：mock 不产 thought，构造才能精确验证裁剪规则。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.api.deps import _turn_payload
from trpg.engine.loop import TurnRecord
from trpg.models.enums import Position
from trpg.storage import auth as auth_mod
from trpg.storage import db

app_mod = importlib.import_module("trpg.api.deps")

ALICE = ("alice", "alice-pass-1")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "thought.db")
    monkeypatch.setattr(app.state, "sessions", {})
    previous = app.state.auth_required
    app.state.auth_required = True
    auth_mod.LIMITER._fails.clear()
    with TestClient(app) as c:
        yield c
    app.state.auth_required = previous


def _login(client: TestClient) -> None:
    conn = app_mod._new_conn()
    try:
        db.create_account(
            conn, username=ALICE[0], password_hash=auth_mod.hash_password(ALICE[1])
        )
    finally:
        conn.close()
    assert client.post(
        "/api/login", json={"username": ALICE[0], "password": ALICE[1]}
    ).status_code == 200


def _session(client: TestClient, world: str = "werewolf", players: int = 8):
    """建一局并返回 (sid, 角色 id 列表)。每个测试只调一次（账号只能建一次）。"""
    _login(client)
    res = client.post(
        "/api/sessions",
        json={"world": world, "players": players, "provider": "mock", "seed": "9"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    ids = [c["id"] for c in data.get("characters") or []]
    return data["session_id"], ids


def _view(
    client: TestClient,
    sid: str,
    ids: list[str],
    viewer: str | None = None,
    viewer_is_dead: bool = False,
) -> dict[str, str]:
    """把一个带 thought 的回执过一遍输出裁剪，返回 {角色 id: 可见的 thought}。"""
    session = app.state.sessions[sid]
    if viewer_is_dead:
        session.state.characters[ids[0]].position = Position.dead

    rec = TurnRecord(turn=1, scene="x")
    rec.actors = list(ids[:3])
    rec.thoughts = [f"盘算-{index}" for index in range(len(rec.actors))]
    rec.actions = ["做事"] * len(rec.actors)
    rec.outcomes = [""] * len(rec.actors)
    rec.narration = [""] * len(rec.actors)

    payload = _turn_payload(session, rec, viewer=viewer)
    return {a["id"]: a.get("thought") or "" for a in payload["actors"]}


def test_god_view_sees_all_thoughts(client):
    """没有人以参与方身份在看 → 上帝视角，看得到所有人的盘算。"""
    sid, ids = _session(client)

    thoughts = _view(client, sid, ids)

    assert thoughts
    assert all(text for text in thoughts.values()), "上帝视角应当看得到全部盘算"


def test_participant_cannot_see_others_in_hidden_world(client):
    """狼人杀（`visibility.thoughts: god`）：参与者只看得到自己的盘算。"""
    sid, ids = _session(client)

    thoughts = _view(client, sid, ids, viewer=ids[0])

    others = [text for cid, text in thoughts.items() if cid != ids[0]]
    assert all(not text for text in others), "参与者不该看到别人的盘算"


def test_participant_sees_own_thought(client):
    sid, ids = _session(client)

    thoughts = _view(client, sid, ids, viewer=ids[0])

    assert thoughts.get(ids[0]), "参与者应当看得到自己的盘算"


def test_eliminated_viewer_returns_to_god_view(client):
    """出局者（含完成遗言后）回到上帝视角，看得到所有人的盘算。"""
    sid, ids = _session(client)

    thoughts = _view(client, sid, ids, viewer=ids[0], viewer_is_dead=True)

    assert all(text for text in thoughts.values()), "出局者应当看得到全局盘算"


def test_world_without_visibility_config_keeps_old_behaviour(client):
    """没配 `visibility` 的世界（ironvale）行为不变：谁都能看。"""
    sid, ids = _session(client, world="ironvale", players=2)

    thoughts = _view(client, sid, ids, viewer=ids[0])

    assert all(text for text in thoughts.values()), "缺省 all：参与者也应看得到盘算"
