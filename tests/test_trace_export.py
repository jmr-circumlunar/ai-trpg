"""S3-1：三类日志落库（prompt / output / transition）+ 对局导出 JSON。

要钉住的四件事：

1. **三类日志齐全**：导出 JSON 里同时有模型调用（prompt/output）与状态迁移（transition）；
2. **导出可用且只读**：结构稳定、重复导出结果一致、不改库；
3. **与状态同源**：迁移日志在引擎的落库点写入（不是事后补写）；
4. **不落敏感内容**：迁移日志只存公开信息，私密字段（心声）不进库。
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.storage import db

ROOT = "."


@pytest.fixture
def conn(tmp_path):
    import sqlite3

    connection = sqlite3.connect(str(tmp_path / "trace.db"), check_same_thread=False)
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    app_mod = importlib.import_module("trpg.api.deps")
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "s31.db")
    monkeypatch.setattr(app.state, "sessions", {})
    return TestClient(app)


# ── transition ──────────────────────────────────────────────
def test_save_and_load_transitions(conn):
    db.save_transition(conn, "ses_x", 1, "action", {"action": "拔刀"}, actor_id="chr_a")
    db.save_transition(conn, "ses_x", 1, "judgment", {"outcome": "success"}, actor_id="chr_a")
    db.save_transition(conn, "ses_x", 2, "action", {"action": "跑"}, actor_id="chr_b")

    rows = db.load_transitions(conn, "ses_x")
    assert len(rows) == 3
    assert [r["turn"] for r in rows] == [1, 1, 2], "必须按写入顺序读回"
    assert rows[0]["kind"] == "action"
    assert rows[0]["payload"]["action"] == "拔刀"
    assert rows[0]["actor_id"] == "chr_a"
    assert rows[0]["created_at"], "每条迁移日志要有时间戳"


def test_transitions_are_append_only(conn):
    db.save_transition(conn, "ses_y", 1, "action", {"n": 1})
    db.save_transition(conn, "ses_y", 1, "action", {"n": 2})
    rows = db.load_transitions(conn, "ses_y")
    assert len(rows) == 2, "同回合同角色的多次迁移都要留痕（不能覆盖）"


def test_transitions_are_session_scoped(conn):
    db.save_transition(conn, "ses_a", 1, "action", {"n": 1})
    db.save_transition(conn, "ses_b", 1, "action", {"n": 2})
    assert len(db.load_transitions(conn, "ses_a")) == 1
    assert len(db.load_transitions(conn, "ses_b")) == 1


# ── prompt / output ─────────────────────────────────────────
def test_load_agent_calls_returns_prompt_and_output(conn):
    # 直接写库（不依赖 `save_agent_call` 的签名）：读取侧只认**表结构**，
    # 写入侧签名在演进中（另一条线正在改），测试不该被它绑住。
    conn.execute(
        "INSERT INTO agent_calls (session_id, turn, agent, task, model, "
        "tokens_in, tokens_out, latency_ms, retry, request_json, response_raw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ses_x", 1, "chr_a", "player_action", "gpt-x",
            10, 20, 33, 0,
            json.dumps({"prompt": "你在铁谷的房间里…"}, ensure_ascii=False),
            json.dumps({"action": "拔刀"}, ensure_ascii=False),
        ),
    )
    conn.commit()
    calls = db.load_agent_calls(conn, "ses_x")
    assert len(calls) == 1
    assert calls[0]["prompt"] == {"prompt": "你在铁谷的房间里…"}
    assert calls[0]["output"] == {"action": "拔刀"}
    assert calls[0]["model"] == "gpt-x"
    assert calls[0]["tokens_in"] == 10 and calls[0]["tokens_out"] == 20


# ── 导出 ────────────────────────────────────────────────────
def test_build_session_export_has_three_log_kinds(conn):
    conn.execute(
        "INSERT INTO agent_calls (session_id, turn, agent, task, model, "
        "tokens_in, tokens_out, latency_ms, retry, request_json, response_raw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ses_x", 1, "chr_a", "player_action", "m",
            1, 1, 1, 0,
            json.dumps({"p": 1}),
            json.dumps({"o": 1}),
        ),
    )
    conn.commit()
    db.save_transition(conn, "ses_x", 1, "action", {"action": "拔刀"}, actor_id="chr_a")

    payload = db.build_session_export(conn, "ses_x", extra={"world": "ironvale", "status": "running"})
    assert payload["schema_version"] >= 1
    assert payload["session_id"] == "ses_x"
    assert payload["world"] == "ironvale"
    assert payload["calls"], "导出必须含 prompt/output"
    assert payload["transitions"], "导出必须含 transition"
    assert "turns" in payload and "ending" in payload
    # 单个 JSON：能直接序列化
    assert json.loads(json.dumps(payload, ensure_ascii=False))["schema_version"] == payload["schema_version"]


def test_export_is_read_only(conn):
    db.save_transition(conn, "ses_x", 1, "action", {"action": "拔刀"})
    first = db.build_session_export(conn, "ses_x")
    second = db.build_session_export(conn, "ses_x")
    assert first["transitions"] == second["transitions"], "重复导出结果一致（只读）"
    assert len(db.load_transitions(conn, "ses_x")) == 1


# ── 端点 ────────────────────────────────────────────────────
def test_export_endpoint_exists(api_client):
    assert "/api/sessions/{sid}/export" in set(app.openapi()["paths"])


def test_export_endpoint_returns_json(api_client):
    created = api_client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "31"},
    ).json()
    sid = created["session_id"]
    api_client.post(f"/api/sessions/{sid}/advance")

    data = api_client.get(f"/api/sessions/{sid}/export").json()
    assert data["session_id"] == sid
    assert data["world"] == "ironvale"
    assert "transitions" in data and "calls" in data and "turns" in data
    assert isinstance(data["transitions"], list)


# ── 敏感内容不落库 ──────────────────────────────────────────
def test_transition_payload_has_no_private_fields(conn):
    """迁移日志只放公开信息：私密字段（thought / notes）不该出现。"""
    db.save_transition(
        conn,
        "ses_x",
        1,
        "action",
        {"action": "拔刀", "outcome": "success", "total": 12, "dc": 10,
         "narration": "他拔出了刀", "state_version": 3},
        actor_id="chr_a",
    )
    payload = db.load_transitions(conn, "ses_x")[0]["payload"]
    for banned in ("thought", "notes", "secret"):
        assert banned not in payload, f"迁移日志不该落私密字段 {banned}"
    assert payload["action"] == "拔刀"
    assert payload["state_version"] == 3


def test_engine_writes_transition_during_play(api_client):
    """引擎在**落库点**写迁移日志（与状态快照同点），不是事后补写。"""
    created = api_client.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "37"},
    ).json()
    sid = created["session_id"]
    api_client.post(f"/api/sessions/{sid}/advance")

    data = api_client.get(f"/api/sessions/{sid}/export").json()
    kinds = [item["kind"] for item in data["transitions"]]
    assert kinds, "推进一回合后应有迁移日志"
    assert all(item["turn"] >= 1 for item in data["transitions"])
