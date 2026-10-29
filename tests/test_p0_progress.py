"""P0：会话进度列、provider 默认值、会话级锁。

这三项都是“用户能感知但不易从功能测试看出来”的东西，所以单独钉成断言：
- 进度列不写 → 世界选择页无法排序/显示进度
- provider 默认 mock → 从历史继续会掉进离线模式
- 全局锁 → 两个账号同时玩互相等
"""

from __future__ import annotations

import inspect
import sqlite3

# 注意：`from trpg.api import app as x` 拿到的是 FastAPI 实例（包属性遮蔽子模块），
# 这里必须从子模块直接取符号。
from trpg.api.app import CreateSessionReq, _lock_for, reset_session, resume_session
from trpg.storage import db as db_mod

_PAST = "2020-01-01T00:00:00+00:00"


def _conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "p0.db"))
    db_mod.init_db(conn)
    return conn


# ── provider 默认值 ─────────────────────────────────────────
def test_create_session_default_provider_is_auto():
    assert CreateSessionReq().provider == "auto", "建局默认必须走真实模型"


def test_resume_and_reset_default_provider_is_auto():
    for fn in (resume_session, reset_session):
        default = inspect.signature(fn).parameters["provider"].default
        assert default == "auto", f"{fn.__name__} 默认 provider 不能是 mock"


# ── 会话进度列 ──────────────────────────────────────────────
def test_touch_session_writes_progress(tmp_path):
    conn = _conn(tmp_path)
    db_mod.save_session(
        conn, "s1", "42", "ironvale", "medieval", _PAST,
        name="测试局", timeline="", human_ids=[], world_day=1,
    )
    before = {s["session_id"]: s for s in db_mod.list_sessions(conn)}["s1"]
    assert before["current_turn"] == 0
    assert before["updated_at"] == _PAST, "没推进过时 updated_at 回落到 created_at"
    assert before["status"] == "running"

    db_mod.touch_session(conn, "s1", 7, "GM 写的第一幕\n第二行会被压平")

    after = {s["session_id"]: s for s in db_mod.list_sessions(conn)}["s1"]
    assert after["current_turn"] == 7
    assert after["summary"] == "GM 写的第一幕 第二行会被压平", "摘要必须压成单行"
    assert after["updated_at"] > before["updated_at"], "updated_at 必须前进，列表才能排序"


def test_touch_session_can_pause(tmp_path):
    conn = _conn(tmp_path)
    db_mod.save_session(conn, "s2", "1", "ironvale", "medieval", _PAST)
    db_mod.touch_session(conn, "s2", 3, status="paused")
    row = {s["session_id"]: s for s in db_mod.list_sessions(conn)}["s2"]
    assert row["status"] == "paused"


# ── 会话级锁 ────────────────────────────────────────────────
def test_lock_is_per_session():
    a1 = _lock_for("ses_a")
    a2 = _lock_for("ses_a")
    b = _lock_for("ses_b")
    assert a1 is a2, "同一会话必须复用同一把锁（同局串行）"
    assert a1 is not b, "不同会话不能共用锁（否则两人同时玩会互相等）"
