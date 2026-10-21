from fastapi.testclient import TestClient

from trpg.api.app import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_list_worlds():
    r = client.get("/api/worlds")
    assert r.status_code == 200
    worlds = r.json()["worlds"]
    ids = [w["id"] for w in worlds]
    assert "ironvale" in ids
    ironvale = next(w for w in worlds if w["id"] == "ironvale")
    assert ironvale["counts"]["seeds"] >= 6
    assert ironvale["eras"][0]["tier_max"] >= 1


def test_create_session_bad_world():
    r = client.post("/api/sessions", json={"world": "no-such-world"})
    assert r.status_code == 400


def test_session_flow_mock():
    r = client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "7"},
    )
    assert r.status_code == 200
    data = r.json()
    sid = data["session_id"]
    assert len(data["characters"]) == 2
    assert data["world"]["id"] == "ironvale"

    adv = client.post(f"/api/sessions/{sid}/advance")
    assert adv.status_code == 200
    turn = adv.json()
    assert turn["turn"] == 1
    assert len(turn["actors"]) == 2
    assert turn["scene"]
    assert turn["state"]["version"] >= 1

    trace = client.get(f"/api/sessions/{sid}/trace")
    assert trace.status_code == 200
    assert len(trace.json()["turns"]) == 1


def test_unknown_session_404():
    assert client.get("/api/sessions/ses_nope").status_code == 404


def test_persist_and_resume():
    """服务重启后能从 SQLite 恢复会话并继续推进。"""
    r = client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "9"},
    )
    sid = r.json()["session_id"]
    assert client.post(f"/api/sessions/{sid}/advance").status_code == 200

    listed = client.get("/api/sessions").json()["sessions"]
    assert any(x["session_id"] == sid for x in listed)

    # 模拟重启：清空内存会话
    from trpg.api.app import app as _app

    _app.state.sessions.pop(sid, None)

    resumed = client.post(f"/api/sessions/{sid}/resume?provider=mock")
    assert resumed.status_code == 200
    assert resumed.json()["session_id"] == sid

    nxt = client.post(f"/api/sessions/{sid}/advance")
    assert nxt.status_code == 200
    assert nxt.json()["turn"] == 2  # 接着第 2 回合，不是从 1 重来


def test_resume_missing_returns_404():
    assert client.post("/api/sessions/ses_nope/resume").status_code == 404


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "IRONVALE" in r.text
