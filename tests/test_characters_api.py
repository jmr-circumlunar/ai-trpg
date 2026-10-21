"""角色库 CRUD + 职业来自世界包 + 开局可选指定角色。"""

from fastapi.testclient import TestClient

from trpg.api.app import app

client = TestClient(app)


def test_professions_come_from_world_pack():
    r = client.get("/api/worlds/ironvale/professions")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["professions"]]
    assert "盗贼" in ids and "医师" in ids
    # label 是世界观命名，id 保持稳定
    thief = next(p for p in r.json()["professions"] if p["id"] == "盗贼")
    assert thief["label"]


def test_professions_unknown_world_404():
    assert client.get("/api/worlds/nope/professions").status_code == 404


def test_create_get_update_delete():
    created = client.post(
        "/api/characters",
        json={
            "name": "测试角色",
            "profession": "医师",
            "private_goal": "找回师父",
            "provider": "minimax",
            "model": "MiniMax-M3.1-Flash",
            "reasoning_effort": "medium",
        },
    )
    assert created.status_code == 200
    cid = created.json()["id"]
    card = created.json()["card"]
    assert card["data"]["name"] == "测试角色"
    assert card["data"]["extensions"]["ai_trpg"]["profession"] == "医师"
    assert card["data"]["extensions"]["ai_trpg"]["model"]["provider"] == "minimax"

    got = client.get(f"/api/characters/{cid}")
    assert got.status_code == 200

    updated = client.put(
        f"/api/characters/{cid}",
        json={"name": "改过的名字", "profession": "医师"},
    )
    assert updated.status_code == 200
    assert updated.json()["card"]["data"]["name"] == "改过的名字"

    listed = client.get("/api/characters").json()["characters"]
    assert any(c["id"] == cid for c in listed)

    assert client.delete(f"/api/characters/{cid}").status_code == 200
    assert client.get(f"/api/characters/{cid}").status_code == 404


def test_profession_defaults_applied():
    r = client.post("/api/characters", json={"name": "默认属性", "profession": "战士"})
    stats = r.json()["card"]["data"]["extensions"]["ai_trpg"]["stats"]
    assert stats["str"] == 15  # 来自 professions.yaml，不是代码常量


def test_stats_can_override():
    r = client.post(
        "/api/characters",
        json={"name": "自定义", "profession": "战士", "stats": {"str": 18}},
    )
    stats = r.json()["card"]["data"]["extensions"]["ai_trpg"]["stats"]
    assert stats["str"] == 18


def test_seed_presets():
    r = client.post("/api/characters/presets?world=ironvale&players=3")
    assert r.status_code == 200
    assert r.json()["count"] == 3


def test_session_with_character_ids():
    presets = client.post("/api/characters/presets?world=ironvale&players=2").json()["ids"]
    r = client.post(
        "/api/sessions",
        json={"world": "ironvale", "provider": "mock", "character_ids": presets},
    )
    assert r.status_code == 200
    assert len(r.json()["characters"]) == 2


def test_session_with_bad_character_ids():
    r = client.post(
        "/api/sessions",
        json={"world": "ironvale", "provider": "mock", "character_ids": ["chr_nope"]},
    )
    assert r.status_code == 400
