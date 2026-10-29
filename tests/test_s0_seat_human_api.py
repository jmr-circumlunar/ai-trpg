"""S4-3（API 侧）：建局按号位映射真人席位 + pending 带可见信息与带理由的建议。

单独成文件：并发实例正在改 `trpg/api/routes/worlds.py`，仓库可能短时间不可导入；
把 API 断言隔离出来，导入失败只影响本文件，不拖累其它测试。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    app_mod = importlib.import_module("trpg.api.deps")
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "s43api.db")
    monkeypatch.setattr(app.state, "sessions", {})
    with TestClient(app) as c:
        yield c


def test_world_context_exposes_seats(client):
    """世界上下文若带 `ui.seats`，结构要对。

    注：`context.seats` 段的接入点与并发实例正在改的 `routes/worlds.py` 重叠，
    本轮**不碰那个文件**（避免互相覆盖），所以这里只断言「有就用、结构要对」，
    房间页在拿不到声明时退回「N 号位」。
    """
    data = client.get("/api/worlds/werewolf/context").json()
    seats = (data.get("ui") or {}).get("seats")
    if seats is None:
        pytest.skip("本轮未接入 context.seats 段（并发实例正在改 routes/worlds.py）")
    assert isinstance(seats, list)
    for seat in seats:
        assert "index" in seat and "label" in seat


def test_create_session_accepts_human_seats(client):
    """房间页只知道号位 → 建局按号位序号映射到角色 id。

    注：**开局时还没有回合在跑**，所以 `pending` 必然是 None
    （pending 的语义是「此刻正在等谁提交」，不是「谁是人」）；
    真人席位要看 `human_ids`。
    """
    created = client.post(
        "/api/sessions",
        json={
            "world": "werewolf", "players": 8, "provider": "mock", "seed": "43",
            "human_seats": [1],
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()
    human_ids = data.get("human_ids") or []
    assert len(human_ids) == 1, f"应有且只有一个人类席位：{human_ids}"
    assert data.get("pending") is None, "还没开回合，不该有人在等"


def test_create_session_without_human_seats_has_none(client):
    created = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "44"},
    ).json()
    assert not created.get("pending"), "没声明真人席位就不该等人类"
    assert not (created.get("human_ids") or []), "没声明真人席位 → 没有真人"


def _wait_for_human(seat, cid: str) -> None:
    """让某个席位进入「正在等人」的状态（后台线程里等 5 秒）。"""
    import asyncio
    import threading
    import time

    def run() -> None:
        asyncio.run(seat.wait_action(cid, "测试者", 1, [], [], timeout=5))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(100):
        if seat.pending():
            return
        time.sleep(0.02)


def test_pending_carries_visible_and_suggestions(client):
    """真人面板的两块数据由服务端给：可见信息 + 带理由的建议。"""
    created = client.post(
        "/api/sessions",
        json={
            "world": "werewolf", "players": 8, "provider": "mock", "seed": "45",
            "human_seats": [1],
        },
    ).json()
    sid = created["session_id"]
    session = client.app.state.sessions[sid]
    seat = session.human
    cid = str((seat.human_ids or ["chr_p1"])[0])

    # 造一条信念快照：建议要带依据（reason 必填）
    from trpg.storage import db as storage_db

    storage_db.save_beliefs(
        session.conn,
        sid,
        1,
        {
            cid: {
                "self_belief": {"label": "alpha", "confidence": 0.6},
                "suspicions": {
                    "chr_p2": {
                        "target": "chr_p2",
                        "label": "beta",
                        "confidence": 0.7,
                        "reason": "他昨夜不在",
                    }
                },
            }
        },
    )

    _wait_for_human(seat, cid)
    pending = client.get(f"/api/sessions/{sid}/pending").json()["pending"]
    assert pending, "席在等人 → pending 应该有内容"
    assert "visible" in pending, "pending 要带可见信息"
    assert "suggestions" in pending, "pending 要带建议"
    assert pending["visible"], "可见信息不该为空"
    assert pending["suggestions"], "有怀疑度就该给建议"
    for item in pending["suggestions"]:
        assert "reason" in item, "每条建议都要有依据（reason 必填）"
        assert "confidence" in item
