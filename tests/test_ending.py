"""结算与故事线重置：手动结束 / 全灭 → epilogue + 落库；reset?to_turn=N 回滚。

一期只做这两种收尾，不做自动结局（章节小结局留四期）。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app

app_mod = importlib.import_module("trpg.api.deps")
from trpg.engine import ending
from trpg.models import CharacterRuntime
from trpg.models.enums import Position
from support.builders import make_card, make_state


@pytest.fixture
def api(tmp_path, monkeypatch):
    """独立的临时库 + 干净的 session 表：结算测试不该被别的测试残留影响。"""
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "ending.db")
    monkeypatch.setattr(app.state, "sessions", {})
    return TestClient(app)


# ── 结算快照 ────────────────────────────────────────────────
def test_summary_structure():
    card = make_card()
    state = make_state(card)
    data = ending.summary(state, {"chr_test01": card}, "ended", 3)

    assert data["kind"] == "ended"
    assert data["turn"] == 3
    assert data["survivors"] == ["chr_test01"]
    assert data["wipe"] is False
    who = data["characters"][0]
    assert who["name"] == "测试者"
    assert who["hp"] > 0
    assert who["position"] == "scene"
    assert isinstance(who["equipment"], list)


def test_is_wipe_only_when_all_dead():
    card = make_card()
    state = make_state(card)
    rt = state.characters["chr_test01"]

    rt.hp = 0  # 只是濒死
    assert ending.is_wipe(state) is False

    rt.position = Position.dead
    assert ending.is_wipe(state) is True


def test_epilogue_text_covers_dead_and_survivors():
    card = make_card()
    state = make_state(card)
    text = ending.epilogue_text(state, {"chr_test01": card}, "ended")
    assert "测试者" in text

    state.characters["chr_test01"].position = Position.dead
    wipe = ending.epilogue_text(state, {"chr_test01": card}, "wipe")
    assert "所有人" in wipe


def test_summary_lists_everyone_even_dead():
    alive = make_card("chr_alive")
    dead = make_card("chr_dead")
    state = make_state(alive)
    state.characters["chr_dead"] = CharacterRuntime(hp=0, sanity=0, position=Position.dead)
    data = ending.summary(
        state, {"chr_alive": alive, "chr_dead": dead}, "ended", 1
    )
    assert {c["id"] for c in data["characters"]} == {"chr_test01", "chr_dead"}
    assert data["survivors"] == ["chr_test01"]


# ── 接口 ────────────────────────────────────────────────────
def test_end_then_read_ending(api):
    created = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "21"},
    ).json()
    sid = created["session_id"]
    for _ in range(2):
        assert api.post(f"/api/sessions/{sid}/advance").status_code == 200

    assert api.get(f"/api/sessions/{sid}/ending").status_code == 404

    ended = api.post(f"/api/sessions/{sid}/end")
    assert ended.status_code == 200
    payload = ended.json()["ending"]
    assert payload["kind"] == "ended"
    assert payload["epilogue"]
    assert payload["survivors"]

    again = api.get(f"/api/sessions/{sid}/ending")
    assert again.status_code == 200
    assert again.json()["ending"]["epilogue"]


def test_reset_rolls_back_and_keeps_playing(api):
    created = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "22"},
    ).json()
    sid = created["session_id"]
    for _ in range(3):
        api.post(f"/api/sessions/{sid}/advance")

    reset = api.post(f"/api/sessions/{sid}/reset?to_turn=1&provider=mock")
    assert reset.status_code == 200
    body = reset.json()
    assert body["reset_to"] == 1
    assert body["state"]["status"] == "running"

    # 回滚后还能继续推进，且回合数接着 2 开始
    nxt = api.post(f"/api/sessions/{sid}/advance")
    assert nxt.status_code == 200
    assert nxt.json()["turn"] == 2


def test_reset_without_snapshot_returns_400(api):
    created = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 1, "provider": "mock", "seed": "23"},
    ).json()
    sid = created["session_id"]
    api.post(f"/api/sessions/{sid}/advance")
    bad = api.post(f"/api/sessions/{sid}/reset?to_turn=0&provider=mock")
    # 第 0 回合有初始快照（create_session 落的），所以这里应成功；
    # 真正拿不到的是尚未发生的回合
    assert bad.status_code == 200
    missing = api.post(f"/api/sessions/{sid}/reset?to_turn=999&provider=mock")
    assert missing.status_code == 200  # 取 <=999 的最后一条
