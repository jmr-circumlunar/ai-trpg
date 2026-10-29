"""P4-T1：GM 的结构化选项（options）必须真的走到前端。

以前 GM 输出 `hooks` 字段，但 loop 不落库、不返回前端 —— 全链路无人消费，
等于模型每回合都在白算一份"可做的事"。这里把它钉成可验收的链路：
生成 → 落库 → payload →（前端按钮）。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from trpg.engine.loop import normalize_options
from trpg.storage import db as db_mod


# ── 归一化 ──────────────────────────────────────────────────
def test_string_hooks_are_upgraded():
    out = normalize_options(["把复测单折进袖口", "问老执事要通行条"])
    assert [o["label"] for o in out] == ["把复测单折进袖口", "问老执事要通行条"]
    assert all(o["id"].startswith("opt_") for o in out)


def test_structured_options_are_kept():
    out = normalize_options(
        [{"id": "a", "label": "撬锁", "hint": "器械 · 会响"}, {"label": "退回去"}]
    )
    assert out[0] == {"id": "a", "label": "撬锁", "hint": "器械 · 会响"}
    assert out[1]["id"] == "opt_2", "缺 id 要补一个稳定的 id"


def test_bad_options_are_dropped_not_raised():
    out = normalize_options(["", None, 42, {"hint": "没标签"}, {"label": "  "}, {"label": "能用的"}])
    assert [o["label"] for o in out] == ["能用的"], "坏数据丢掉，不要让整回合失败"


def test_options_are_capped():
    out = normalize_options([f"选项{i}" for i in range(10)])
    assert len(out) == 4, "最多 4 条，多了就是噪声"


# ── 落库 ────────────────────────────────────────────────────
def test_options_roundtrip_through_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "opt.db"))
    db_mod.init_db(conn)
    try:
        opts = [{"id": "opt_1", "label": "把复测单折进袖口", "hint": "隐蔽"}]
        db_mod.save_turn(
            conn, "ses_o", 1, "chr_p1", "先看看", "折单子", "他折好了",
            json.dumps(opts, ensure_ascii=False),
        )
        db_mod.save_turn(conn, "ses_o", 1, "chr_p2", "跟上", "跟过去", "他跟着走")

        turns = db_mod.load_turns(conn, "ses_o")
    finally:
        conn.close()

    assert len(turns) == 1
    assert turns[0]["options"] == opts, "恢复后选项也要回来（同一回合每行同一份）"


# ── payload ─────────────────────────────────────────────────
def test_turn_payload_has_options_key(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from trpg.api.app import app

    app_mod = importlib.import_module("trpg.api.deps")
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "p4.db")
    monkeypatch.setattr(app.state, "sessions", {})

    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"world": "longzu", "players": 2, "provider": "mock", "seed": "21"},
        )
        sid = created.json()["session_id"]
        res = client.post(f"/api/sessions/{sid}/advance").json()

    assert "options" in res, "前端要靠这个字段渲染按钮"
    assert isinstance(res["options"], list)
