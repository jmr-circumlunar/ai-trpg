"""C56 正面实现：隐藏身份按世界包的 `secrets` 矩阵裁剪后才出客户端。

两条线都要守住：
① **快照**里永远不含 `camp`（阵营只能从按可见性裁剪的端点拿）；
② 隐藏职业（`secrets.profession: self`）在**别人的视角**下被抹掉。
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "vis.db")
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


def _werewolf(client: TestClient) -> dict:
    _login(client)
    res = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "9"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _camps(client: TestClient, sid: str, char_ids: list[str]) -> dict[str, str]:
    """用上帝视角读每个人的阵营（只有上帝视角看得到全部）。"""
    out = {}
    for cid in char_ids:
        res = client.get(f"/api/sessions/{sid}/identity/{cid}")
        assert res.status_code == 200, res.text
        out[cid] = res.json().get("camp") or ""
    return out


def test_snapshot_never_contains_camp(client):
    """阵营不进快照——快照是所有人共享的，带上就等于公开。"""
    data = _werewolf(client)
    sid = data["session_id"]

    chars = client.get(f"/api/sessions/{sid}").json()["characters"]

    assert chars, "本局应有人"
    for char in chars:
        assert "camp" not in char
        assert "camp_revealed" not in char


def test_hidden_profession_is_masked_for_others(client):
    """`secrets.profession: self` → 别人的职业在快照里是空的。"""
    data = _werewolf(client)
    sid = data["session_id"]
    viewer = data["characters"][0]["id"]

    chars = client.get(f"/api/sessions/{sid}?viewer={viewer}").json()["characters"]

    mine = next(c for c in chars if c["id"] == viewer)
    others = [c for c in chars if c["id"] != viewer]
    assert mine["profession"], "本人应当看得见自己的职业"
    assert all(c["profession"] == "" for c in others), "别人的隐藏职业不该出现在快照里"


def test_god_view_sees_everything(client):
    data = _werewolf(client)
    sid = data["session_id"]

    chars = client.get(f"/api/sessions/{sid}").json()["characters"]

    assert all(c["profession"] for c in chars), "上帝视角应看得到全部职业"


def test_identity_endpoint_respects_visibility(client):
    """本人看得到全部；同阵营看得到阵营；其他人什么都看不到。"""
    data = _werewolf(client)
    sid = data["session_id"]
    ids = [c["id"] for c in data["characters"]]
    camps = _camps(client, sid, ids)

    groups: dict[str, list[str]] = {}
    for cid, camp in camps.items():
        groups.setdefault(camp or "none", []).append(cid)
    group = max(groups.values(), key=len)
    if len(group) < 2:
        pytest.skip("本局同阵营人数不足 2，无法验证阵营可见性")

    me, mate = group[0], group[1]
    outsider = next((cid for cid, camp in camps.items() if camp != camps[me]), me)

    # 本人：职业 + 阵营
    own = client.get(f"/api/sessions/{sid}/identity/{me}?viewer={me}").json()
    assert own["profession"]
    assert own["camp"] == camps[me]

    # 同阵营：看得到阵营，看不到职业
    same = client.get(f"/api/sessions/{sid}/identity/{me}?viewer={mate}").json()
    assert same["camp"] == camps[me]
    assert same["profession"] == ""

    # 其他人：都看不到
    other = client.get(f"/api/sessions/{sid}/identity/{me}?viewer={outsider}").json()
    assert other["camp"] == ""
    assert other["profession"] == ""


def test_identity_of_unknown_character_is_404(client):
    data = _werewolf(client)
    sid = data["session_id"]

    res = client.get(f"/api/sessions/{sid}/identity/chr_nobody")

    assert res.status_code == 404


def test_turn_payload_carries_votes(client):
    """票型与计票结果要出得来（前端决策面板、复盘页都要）。"""
    data = _werewolf(client)
    sid = data["session_id"]

    res = client.post(f"/api/sessions/{sid}/advance")

    assert res.status_code == 200, res.text
    payload = res.json()
    assert "votes" in payload
    assert "vote_result" in payload
