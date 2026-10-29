"""P3：流程约束（世界列表 / 一账号一世界单局 / 暂停退出 / 配置锁）。

这几条是「先选世界、历史优先、进局不可改配置」的**服务端保证**：
前端藏控件不算数，直接打 API 也必须拦住。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.storage import auth as auth_mod
from trpg.storage import db

app_mod = importlib.import_module("trpg.api.deps")

ALICE = ("alice", "alice-pass-1")
BOB = ("bob", "bob-pass-2")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "flow.db")
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


def _new_session(client: TestClient, world: str = "longzu", seed: str = "5"):
    return client.post(
        "/api/sessions",
        json={"world": world, "players": 2, "provider": "mock", "seed": seed},
    )


def test_me_worlds_lists_open_session(client):
    _account(ALICE)
    _login(client, ALICE)
    sid = _new_session(client).json()["session_id"]

    worlds = client.get("/api/me/worlds").json()["worlds"]
    longzu = next(w for w in worlds if w["id"] == "longzu")
    assert longzu["open"]["session_id"] == sid
    assert longzu["open"]["current_turn"] == 0
    assert longzu["history"] == 0


def test_one_open_session_per_world(client):
    _account(ALICE)
    _login(client, ALICE)
    assert _new_session(client).status_code == 200

    again = _new_session(client, seed="6")
    assert again.status_code == 409, "同一世界不能再开第二局"
    assert "还有一局没结束" in again.json()["detail"]

    other = _new_session(client, world="ironvale", seed="7")
    assert other.status_code == 200, "换世界不受影响（新世界没有进行中局）"


def test_pause_then_start_new(client):
    _account(ALICE)
    _login(client, ALICE)
    sid = _new_session(client).json()["session_id"]

    paused = client.post(f"/api/sessions/{sid}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    # 暂停后可以开新局
    assert _new_session(client, seed="8").status_code == 200

    # 暂停的局没有被删：仍能从历史列表里找到，状态是 paused（存档语义）
    listed = client.get("/api/sessions").json()["sessions"]
    paused_rows = [s for s in listed if s["session_id"] == sid]
    assert paused_rows and paused_rows[0]["status"] == "paused"

    # 世界选择页的「继续」指向最新的一局（running 优先）
    longzu = next(w for w in client.get("/api/me/worlds").json()["worlds"] if w["id"] == "longzu")
    assert longzu["open"]["status"] == "running"


def test_config_is_locked_after_start(client):
    _account(ALICE)
    _login(client, ALICE)
    sid = _new_session(client).json()["session_id"]

    conn = app_mod._new_conn()
    try:
        assert db.session_meta(conn, sid)["config_locked"] == 1, "开局即锁"
        with pytest.raises(HTTPException) as err:
            app_mod._require_unlocked(conn, sid)
        assert err.value.status_code == 409
    finally:
        conn.close()


def test_new_pages_render(client):
    """P3 页面壳：四个新入口都要能打开（未登录由中间件跳到 /login）。"""
    _account(ALICE)
    _login(client, ALICE)
    for path, needle in (
        ("/worlds", "选择一个世界"),
        ("/lobby", "准备开局"),
        ("/archive", "历史局"),
        ("/play", "AI 跑团"),
    ):
        res = client.get(path)
        assert res.status_code == 200, f"{path} 打不开"
        assert needle in res.text, f"{path} 内容不对"

    # 对局页必须带「进局即锁」逻辑（配置控件在局内隐藏）。
    # 断言的是**能力**而不是实现细节：旧静态页把配置控件写死在新路径里，
    # 新前端（Vue）用条件渲染隐藏它们——两条路径都必须成立。
    from pathlib import Path

    play_html = client.get("/play").text
    assert 'id="world"' in play_html or 'id="app"' in play_html

    src_dir = Path(__file__).resolve().parents[1] / "src"
    vue_files = list(src_dir.rglob("*.vue"))
    assert vue_files, "前端源码目录不应为空"

    # 方案 36 之后口径变了：**对局页不再有任何配置控件**（世界/人数/模型），
    # 配置都在大厅与房间里做。所以这里断言的是「局内没有配置」+「配置在别处」。
    index_src = (src_dir / "pages" / "IndexPage.vue").read_text(encoding="utf-8")
    for gone in ('id="world"', 'id="players"', 'id="provider"'):
        assert gone not in index_src, f"对局页不该再有配置控件 {gone}"
    assert any(
        "加号位" in path.read_text(encoding="utf-8") for path in vue_files
    ), "号位增减应当留在房间页"


def test_open_session_rule_is_per_account(client):
    _account(ALICE)
    _account(BOB)
    _login(client, ALICE)
    assert _new_session(client).status_code == 200

    _login(client, BOB)
    assert _new_session(client, seed="9").status_code == 200, "别人有局不挡我"
