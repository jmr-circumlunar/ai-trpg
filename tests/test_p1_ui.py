"""P1-T1：`scene` 事件必须携带**完整**叙事。

前端打字机依赖这条：以前引擎发的是 `scene[:120]`，长场景只能看到半句，
而完整文本要等回合结束的 `done`。这里把「事件文本 == 最终叙事」钉成契约。
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app

app_mod = importlib.import_module("trpg.api.deps")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "p1.db")
    monkeypatch.setattr(app.state, "sessions", {})
    with TestClient(app) as c:
        yield c


def _events(client: TestClient, sid: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    with client.stream("GET", f"/api/sessions/{sid}/advance/stream") as res:
        event = None
        for line in res.iter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: ") and event:
                out.append((event, json.loads(line[6:])))
                event = None
    return out


def test_scene_event_is_not_truncated(client):
    created = client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "41"},
    )
    assert created.status_code == 200
    sid = created.json()["session_id"]

    events = _events(client, sid)
    scene_texts = [d.get("text", "") for ev, d in events if ev == "scene"]
    done = next(d for ev, d in events if ev == "done")

    assert scene_texts, "必须发出 scene 事件"
    assert scene_texts[0] == done["scene"], "scene 事件必须与最终叙事完全一致（不能截断）"
