"""P0：账号数据隔离（局 / 角色库 / 点数）。

认证在本文件显式打开（其他测试由 conftest 的 autouse fixture 关闭）。
未认证时 `owner_of()` 回退 `'local'`，所以旧测试零改动即可绿；
这里要证明的是：**登录之后，两个账号互相看不到、开不了、点不了**。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app, owner_of
from trpg.growth.wallet import Wallet
from trpg.storage import auth as auth_mod
from trpg.storage import db

app_mod = importlib.import_module("trpg.api.deps")

ALICE = ("alice", "alice-pass-1")
BOB = ("bob", "bob-pass-2")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "owner.db")
    monkeypatch.setattr(app.state, "sessions", {})
    previous = app.state.auth_required
    app.state.auth_required = True
    auth_mod.LIMITER._fails.clear()
    with TestClient(app) as c:
        yield c
    app.state.auth_required = previous


def _account(username: str, password: str) -> str:
    conn = app_mod._new_conn()
    try:
        return db.create_account(
            conn, username=username, password_hash=auth_mod.hash_password(password)
        )
    finally:
        conn.close()


def _login(client: TestClient, user: tuple[str, str]) -> None:
    res = client.post("/api/login", json={"username": user[0], "password": user[1]})
    assert res.status_code == 200, res.text


# ── 角色库 ──────────────────────────────────────────────────
def test_character_library_is_isolated(client):
    _account(*ALICE)
    _account(*BOB)

    _login(client, ALICE)
    ids = client.post("/api/characters/presets?world=ironvale&players=2&seed=9").json()["ids"]
    assert client.get("/api/characters").json()["characters"], "alice 应该有卡"

    _login(client, BOB)
    assert client.get("/api/characters").json()["characters"] == [], "bob 不能看到 alice 的卡"
    assert client.get(f"/api/characters/{ids[0]}").status_code == 404, "读别人的卡要装作不存在"
    assert client.delete(f"/api/characters/{ids[0]}").status_code == 404, "更不能删"


# ── 局 ──────────────────────────────────────────────────────
def test_session_is_isolated(client):
    _account(*ALICE)
    _account(*BOB)

    _login(client, ALICE)
    created = client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "11"},
    )
    assert created.status_code == 200, created.text
    sid = created.json()["session_id"]

    _login(client, BOB)
    listed = client.get("/api/sessions").json()["sessions"]
    assert all(s["session_id"] != sid for s in listed), "别的账号不能在列表里看到"
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.post(f"/api/sessions/{sid}/advance").status_code == 404, "不能替别人推进"
    assert client.post(f"/api/sessions/{sid}/resume").status_code == 404, "不能抢别人的存档"
    assert client.get(f"/api/sessions/{sid}/trace").status_code == 404


def test_unauthenticated_falls_back_to_local():
    assert owner_of(None) == "local"
    assert owner_of(object()) == "local"


# ── 点数（局外成长）─────────────────────────────────────────
def test_meta_points_are_isolated(client):
    alice_id = _account(*ALICE)
    _account(*BOB)

    _login(client, ALICE)
    key = client.get("/api/meta?world=ironvale").json()["currency"]["key"]
    conn = app_mod._new_conn()
    try:
        Wallet(conn, owner_id=alice_id).add(key, 7, world_id="", reason="test")
    finally:
        conn.close()
    assert client.get("/api/meta?world=ironvale").json()["balance"] == 7

    _login(client, BOB)
    assert client.get("/api/meta?world=ironvale").json()["balance"] == 0, "点数不能跳账号"
