"""P4-T5/T6：角色档案（随成长自动变化）+ profile 接口。

关键点：**不新增存储**。身份、装备、经历年表本来就有（卡 + `character_events`），
以前只是没人读出来 —— 所以"档案会随成长变化"是数据驱动的，不是手写的。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app

app_mod = importlib.import_module("trpg.api.deps")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "profile.db")
    monkeypatch.setattr(app.state, "sessions", {})
    with TestClient(app) as c:
        yield c


def _session(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/sessions",
        json={"world": "longzu", "players": 2, "provider": "mock", "seed": "11"},
    )
    assert created.status_code == 200, created.text
    data = created.json()
    return data["session_id"], data["characters"][0]["id"]


def test_profile_has_all_sections(client):
    sid, cid = _session(client)
    res = client.get(f"/api/sessions/{sid}/characters/{cid}/profile")
    assert res.status_code == 200, res.text

    p = res.json()
    assert set(p) >= {"id", "basic", "now", "gear", "timeline", "growth", "relations"}
    assert p["basic"]["name"]
    assert p["basic"]["path_label"], "身份要给中文名，不能是内部 id"
    assert p["now"]["hp"] >= 0 and p["now"]["hp_max"] > 0
    assert "gold" in p["now"] and "supplies" in p["now"], "钱是角色的，档案里要有"
    assert isinstance(p["gear"], list)
    assert isinstance(p["growth"]["skills"], dict)


def test_profile_timeline_grows_with_play(client):
    sid, cid = _session(client)
    before = client.get(f"/api/sessions/{sid}/characters/{cid}/profile").json()["timeline"]

    assert client.post(f"/api/sessions/{sid}/advance").status_code == 200
    after = client.get(f"/api/sessions/{sid}/characters/{cid}/profile").json()["timeline"]

    assert len(after) > len(before), "打了一回合，年表要长出一条"
    assert after[0]["turn"] >= 1
    assert after[0]["summary"], "年表条目要有内容"


def test_profile_unknown_character_is_404(client):
    sid, _ = _session(client)
    assert client.get(f"/api/sessions/{sid}/characters/chr_nope/profile").status_code == 404


def test_reasoning_is_collected_for_trace_only():
    """推理链只留痕给 trace，不进叙事流（里面有 DC/HP，会破坏沉浸）。"""
    from trpg.agents.agent import AgentLLM

    class _Result:
        raw = {"choices": [{"message": {"reasoning_content": "先看门岗，再决定要不要亮证"}}]}

    llm = object.__new__(AgentLLM)
    llm.reasoning_log = []
    AgentLLM._note_reasoning(llm, _Result(), "scene")

    assert llm.reasoning_log[0]["text"].startswith("先看门岗")
    assert llm.reasoning_log[0]["task"] == "scene"


def test_profile_hides_other_missing_session(client):
    assert client.get("/api/sessions/ses_nope/characters/chr_x/profile").status_code == 404
