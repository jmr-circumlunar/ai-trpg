"""P3-T10 / P3-T12：Agent 实体（人）与随机建卡。

Agent 是人的容器（跨局人格与见闻），角色是世界里的一张皮。
1 Agent : N 卡 —— 一张皮废了换一张，人还是那个人。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.storage import auth as auth_mod
from trpg.storage import db

app_mod = importlib.import_module("trpg.api.deps")

ALICE = ("alice", "alice-pass-1")
BOB = ("bob", "bob-pass-2")

#: 出厂默认 agent（`config/agents.yaml`）：首次访问 `/api/agents` 时落成档案。
#: 所以「新建一个账号后列表为空」的旧假设不再成立——列表 = 出厂的人 + 自己建的。
from pathlib import Path as _Path  # noqa: E402

from trpg.config.loader import config_path as _config_path  # noqa: E402
from trpg.config.loader import load_yaml as _load_yaml  # noqa: E402

_REPO = _Path(__file__).resolve().parents[1]
BUILTIN = [
    str(item.get("id"))
    for item in ((_load_yaml(_config_path(_REPO, "agents"), {}) or {}).get("agents") or [])
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "agents.db")
    monkeypatch.setattr(app.state, "sessions", {})
    previous = app.state.auth_required
    app.state.auth_required = True
    auth_mod.LIMITER._fails.clear()
    with TestClient(app) as c:
        yield c
    app.state.auth_required = previous


def _account(user: tuple[str, str]) -> str:
    conn = app_mod._new_conn()
    try:
        return db.create_account(
            conn, username=user[0], password_hash=auth_mod.hash_password(user[1])
        )
    finally:
        conn.close()


def _login(client: TestClient, user: tuple[str, str]) -> None:
    assert client.post(
        "/api/login", json={"username": user[0], "password": user[1]}
    ).status_code == 200


# ── Agent CRUD ──────────────────────────────────────────────
def test_agent_crud(client):
    _account(ALICE)
    _login(client, ALICE)

    created = client.post(
        "/api/agents",
        json={"name": "老王", "persona": "话少，先看再说", "voice": "低沉"},
    )
    assert created.status_code == 200, created.text
    aid = created.json()["id"]
    assert aid.startswith("agt_")

    listed = client.get("/api/agents").json()["agents"]
    # 出厂就有人（`config/agents.yaml` 首次访问时落成档案），所以我建的人只是**多一个**
    ids = [a["id"] for a in listed]
    assert aid in ids
    assert len(ids) == len(BUILTIN) + 1
    mine = next(a for a in listed if a["id"] == aid)
    assert mine["persona"] == "话少，先看再说"

    updated = client.put(
        f"/api/agents/{aid}", json={"name": "老王", "persona": "话少，但记仇"}
    )
    assert updated.status_code == 200
    assert updated.json()["agent"]["persona"] == "话少，但记仇"

    assert client.delete(f"/api/agents/{aid}").status_code == 200
    rest = [a["id"] for a in client.get("/api/agents").json()["agents"]]
    assert aid not in rest, "删掉的人不该还在"


def test_agent_needs_name(client):
    _account(ALICE)
    _login(client, ALICE)
    assert client.post("/api/agents", json={"name": "   "}).status_code == 400


def test_agents_are_isolated(client):
    _account(ALICE)
    _account(BOB)
    _login(client, ALICE)
    aid = client.post("/api/agents", json={"name": "alice 的人"}).json()["id"]

    _login(client, BOB)
    others = [a["id"] for a in client.get("/api/agents").json()["agents"]]
    assert aid not in others, "别人看不到我的 Agent"
    assert client.put(f"/api/agents/{aid}", json={"name": "抢"}).status_code == 404
    assert client.delete(f"/api/agents/{aid}").status_code == 404


# ── 席位 → Agent ────────────────────────────────────────────
def _session_body(**kw):
    body = {"world": "longzu", "players": 2, "provider": "mock", "seed": "5"}
    body.update(kw)
    return body


def test_seat_binds_agents(client):
    _account(ALICE)
    _login(client, ALICE)
    a1 = client.post("/api/agents", json={"name": "甲"}).json()["id"]
    a2 = client.post("/api/agents", json={"name": "乙"}).json()["id"]

    res = client.post("/api/sessions", json=_session_body(agent_ids=[a1, a2]))
    assert res.status_code == 200, res.text

    conn = app_mod._new_conn()
    try:
        row = conn.execute(
            "SELECT agent_ids_json FROM sessions WHERE id = ?", (res.json()["session_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert a1 in (row[0] or "") and a2 in (row[0] or ""), "席位与 Agent 的绑定要落库"


def test_same_agent_cannot_take_two_seats(client):
    _account(ALICE)
    _login(client, ALICE)
    aid = client.post("/api/agents", json={"name": "甲"}).json()["id"]

    res = client.post("/api/sessions", json=_session_body(agent_ids=[aid, aid]))
    assert res.status_code == 400
    assert "两个席位" in res.json()["detail"], "一个 Agent 就是一个人，不能同时坐两个席位"


def test_unknown_agent_is_rejected(client):
    _account(ALICE)
    _login(client, ALICE)
    res = client.post("/api/sessions", json=_session_body(agent_ids=["agt_nope", ""]))
    assert res.status_code == 404


# ── 随机建卡 ────────────────────────────────────────────────
def test_roll_creates_one_card(client):
    _account(ALICE)
    _login(client, ALICE)

    res = client.post("/api/characters/roll?world=longzu&seed=abc")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["id"].startswith("chr_")
    assert data["card"]["data"]["name"]

    listed = client.get("/api/characters").json()["characters"]
    assert [c["id"] for c in listed] == [data["id"]], "生成的卡要进角色库"


def test_roll_is_reproducible_by_seed(client):
    """同 seed 保证**身份**可复现（出身/身份/天赋/职业）。

    数值（hp/理智/装备掷定）仍各自随机——抽到同一个「人」，但体质和行头不同，
    这是有意的：否则同 seed 只是复制粘贴。
    """
    _account(ALICE)
    _login(client, ALICE)

    a = client.post("/api/characters/roll?world=longzu&seed=same").json()["card"]
    b = client.post("/api/characters/roll?world=longzu&seed=same").json()["card"]

    def ext(card: dict) -> dict:
        # 卡是 SillyTavern V2 超集：扩展字段落在 data.extensions.ai_trpg
        data = card.get("data") or {}
        return (data.get("extensions") or {}).get("ai_trpg") or {}

    for key in ("origin", "path", "profession", "talents"):
        assert ext(a)[key] == ext(b)[key], f"{key} 必须同 seed 可复现"
    assert a["data"]["name"] == b["data"]["name"]

