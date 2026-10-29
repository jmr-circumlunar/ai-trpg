"""登录与公网保护（防未授权消耗 token）。

对应知识库 `过程记录/方案/05-登录与公网开放.md`。
其他测试默认关闭认证（conftest 的 autouse fixture），认证在这里显式打开来测。
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.storage import auth as auth_mod
from trpg.storage import db

app_mod = importlib.import_module("trpg.api.deps")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """开启认证 + 独立临时库。"""
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(app.state, "sessions", {})
    previous = app.state.auth_required
    app.state.auth_required = True
    auth_mod.LIMITER._fails.clear()
    with TestClient(app) as c:
        yield c
    app.state.auth_required = previous


def _make_account(username="tester", password="correct-horse", role="user") -> str:
    conn = app_mod._new_conn()
    try:
        return db.create_account(
            conn, username=username, password_hash=auth_mod.hash_password(password), role=role
        )
    finally:
        conn.close()


# ── 密码哈希 ────────────────────────────────────────────────
def test_password_hash_is_not_plaintext():
    stored = auth_mod.hash_password("s3cret")
    assert "s3cret" not in stored
    assert stored.startswith("pbkdf2_sha256$")
    assert auth_mod.verify_password("s3cret", stored) is True
    assert auth_mod.verify_password("wrong", stored) is False


def test_verify_password_handles_broken_storage():
    assert auth_mod.verify_password("x", "garbage") is False
    assert auth_mod.verify_password("x", "") is False


def test_hash_is_salted():
    assert auth_mod.hash_password("same") != auth_mod.hash_password("same")


# ── 访问控制 ────────────────────────────────────────────────
def test_health_is_public(client):
    assert client.get("/api/health").status_code == 200


def test_login_page_is_public(client):
    res = client.get("/login")
    assert res.status_code == 200
    assert "登录" in res.text


def test_page_redirects_to_login_when_logged_out(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert "/login" in res.headers.get("location", "")


def test_api_returns_401_when_logged_out(client):
    assert client.get("/api/worlds").status_code == 401


def test_login_then_access_page(client):
    _make_account()
    res = client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    assert res.status_code == 200
    assert res.json()["username"] == "tester"
    # 登录后能进页面
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/api/worlds").status_code == 200


def test_login_with_wrong_password_rejected(client):
    _make_account()
    res = client.post("/api/login", json={"username": "tester", "password": "nope"})
    assert res.status_code == 401


def test_login_with_unknown_user_rejected(client):
    res = client.post("/api/login", json={"username": "ghost", "password": "x"})
    assert res.status_code == 401


def test_logout_invalidates_session(client):
    _make_account()
    client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    assert client.get("/api/worlds").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/worlds").status_code == 401


def test_me_reports_account(client):
    _make_account()
    client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    me = client.get("/api/me").json()
    assert me["username"] == "tester"
    assert me["role"] == "user"


# ── 限流 ────────────────────────────────────────────────────
def test_repeated_failures_get_locked_out(client):
    _make_account()
    for _ in range(5):
        res = client.post("/api/login", json={"username": "tester", "password": "bad"})
        assert res.status_code == 401
    res = client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    assert res.status_code == 429


# ── 令牌与账号存储 ──────────────────────────────────────────
def test_session_token_stored_as_hash_only(client):
    _make_account()
    res = client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    token = res.cookies.get(auth_mod.COOKIE_NAME)
    assert token
    conn = app_mod._new_conn()
    try:
        rows = conn.execute("SELECT token_hash FROM auth_sessions").fetchall()
    finally:
        conn.close()
    assert rows
    assert all(r[0] != token for r in rows), "库里不能存明文令牌"
    assert any(r[0] == auth_mod.token_hash(token) for r in rows)


def test_password_not_stored_plaintext(client):
    _make_account()
    conn = app_mod._new_conn()
    try:
        row = conn.execute("SELECT password_hash FROM accounts WHERE username='tester'").fetchone()
    finally:
        conn.close()
    assert row and "correct-horse" not in row[0]


def test_disabled_account_cannot_login(client):
    aid = _make_account()
    conn = app_mod._new_conn()
    try:
        db.set_account_disabled(conn, aid, True)
    finally:
        conn.close()
    res = client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    assert res.status_code == 401


def test_password_change_invalidates_sessions(client):
    aid = _make_account()
    client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    conn = app_mod._new_conn()
    try:
        db.set_account_password(conn, aid, auth_mod.hash_password("new-password"))
        db.delete_account_sessions(conn, aid)
    finally:
        conn.close()
    assert client.get("/api/worlds").status_code == 401


# ── 管理员 ──────────────────────────────────────────────────
def test_admin_can_create_account(client):
    _make_account("admin1", "admin-password", role="admin")
    client.post("/api/login", json={"username": "admin1", "password": "admin-password"})
    res = client.post(
        "/api/accounts", json={"username": "guest", "password": "guest-password", "role": "user"}
    )
    assert res.status_code == 200
    # 新账号能登录
    assert client.post(
        "/api/login", json={"username": "guest", "password": "guest-password"}
    ).status_code == 200


def test_non_admin_cannot_create_account(client):
    _make_account()
    client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    res = client.post("/api/accounts", json={"username": "guest", "password": "guest-password"})
    assert res.status_code == 403


def test_account_list_requires_admin(client):
    _make_account()
    client.post("/api/login", json={"username": "tester", "password": "correct-horse"})
    assert client.get("/api/accounts").status_code == 403
