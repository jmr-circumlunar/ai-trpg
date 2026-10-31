"""W3 · 信息泄露的矩阵审计（每回合 × 每视角 × 每出口）。

被测对象：

- `tools/audit_leak.py` —— 可复跑的审计脚本（**退出码 0 = 违规清单为空**）；
- `trpg/eval/leak_audit.py` —— 审计规则（纯函数、零世界词）；
- `trpg/api/views.py` —— **单一服务端出口**的裁剪。

**两条都要**：

1. 真实局跑下来**违规清单为空**；
2. 审计器对**故意造出来的泄露**必须报违规 —— 否则「清单为空」可能只是审计器没干活。

口径见知识库 `过程记录/方案/43-信息泄露矩阵审计-W3`。
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.eval.leak_audit import (
    audit_identity_payload,
    audit_log_payload,
    audit_state_payload,
)
from trpg.storage import auth as auth_mod
from trpg.storage import db

ROOT = Path(__file__).resolve().parents[1]
app_mod = importlib.import_module("trpg.api.deps")

# 审计矩阵用的**中性**参数：这里不出现任何世界词，规则与阵营含义无关
SECRETS = {"profession": "self", "camp": "self_and_camp"}


# ── ① 真实局：矩阵审计必须干净 ────────────────────────────────


def _run_audit(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/audit_leak.py", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def test_matrix_audit_over_every_viewer_and_exit_is_clean():
    """审计脚本可复跑：违规清单为空 → 退出码 0。"""
    res = _run_audit(
        "--world", "werewolf", "--players", "8", "--turns", "3",
        "--provider", "mock", "--seed", "9",
    )

    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    assert "违规清单为空" in res.stdout, res.stdout[-2000:]
    # 审计**确实干活了**：不是「因为没检查到东西所以空」
    stats = re.search(r"回合=(\d+) 视角=(\d+) 出口=(\d+) 载荷=(\d+)", res.stdout)
    assert stats, res.stdout[-2000:]
    turns, viewers, exits, payloads = (int(x) for x in stats.groups())
    assert turns >= 3
    assert viewers >= 8
    assert exits >= payloads >= 100


# ── ② 反向用例：审计器必须抓得到人为造出来的泄露 ───────────────


def test_auditor_catches_a_leaked_private_state():
    """别人的私有状态（技能资源余量 / 笔记）出现在共享快照里 → 报违规。

    这条是**回归钉子**：本轮实测到过「两种药各一瓶的角色被所有人看见」，
    修法是把该字段并入私有字段过滤；审计器要能抓住它复发。
    """
    payload = {
        "characters": [
            {"id": "me", "profession": "A", "skill_charges": {"x": 1}},
            {"id": "other", "profession": "", "skill_charges": {"x": 1}, "notes": ["n"]},
        ]
    }

    violations = audit_state_payload(payload, "me", secrets=SECRETS)

    rules = {item.rule for item in violations}
    assert "private_visible" in rules, violations
    targets = {item.target for item in violations}
    assert targets == {"other"}, violations
    assert all(item.viewer == "me" for item in violations)


def test_auditor_catches_a_leaked_identity_and_camp():
    payload = {
        "characters": [
            {"id": "me", "profession": "A"},
            {"id": "other", "profession": "B", "camp": "c1", "camp_label": "L"},
        ]
    }

    violations = audit_state_payload(payload, "me", secrets=SECRETS)

    rules = {item.rule for item in violations}
    assert "identity_visible" in rules, violations
    assert "camp_visible" in rules, violations


def test_auditor_ignores_the_viewers_own_rows():
    """本人看得到自己的身份与私有状态——不是泄露。"""
    payload = {
        "characters": [
            {"id": "me", "profession": "A", "skill_charges": {"x": 1}, "notes": ["n"]},
        ]
    }

    assert audit_state_payload(payload, "me", secrets=SECRETS) == []


def test_auditor_accepts_the_observer_view():
    """观察者（上帝 / 出局者复盘）看得到全部——不是泄露。"""
    payload = {"characters": [{"id": "other", "profession": "B", "camp": "c1"}]}

    assert audit_state_payload(payload, "other", secrets=SECRETS, observer=True) == []
    assert audit_state_payload(payload, None, secrets=SECRETS) == []


def test_identity_audit_follows_the_matrix():
    """身份端点：职业按 `secrets.profession`，阵营按 `secrets.camp`。"""
    payload = {"char_id": "other", "profession": "B", "camp": "c1"}

    # 别人看别人：两边都不可见 → 有值就是泄露
    violations = audit_identity_payload(
        payload, "me", secrets=SECRETS, target_camp="c2", viewer_camp="c1"
    )
    assert {item.rule for item in violations} == {"identity_visible", "camp_visible"}

    # 同阵营：阵营可见、职业不可见
    same = audit_identity_payload(
        {"char_id": "other", "profession": "", "camp": "c1"},
        "me",
        secrets=SECRETS,
        target_camp="c1",
        viewer_camp="c1",
    )
    assert same == []

    # 本人：都可见
    own = audit_identity_payload(
        payload, "other", secrets=SECRETS, target_camp="c1", viewer_camp="c1"
    )
    assert own == []


def test_log_audit_follows_the_thoughts_rule():
    """思维过程：规则说不给看就不能出现。"""
    payload = {"id": "other", "thought": "偷偷盘算"}

    assert audit_log_payload(payload, "me", thoughts_visible=lambda cid: False) != []
    assert audit_log_payload(payload, "me", thoughts_visible=lambda cid: True) == []
    # 没有思维字段的事件不受这条规则管
    assert audit_log_payload({"id": "other", "action": "说话"}, "me") == []


# ── ③ 接口层回归：共享快照里别人的私有状态必须为空 ─────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "leak.db")
    monkeypatch.setattr(app.state, "sessions", {})
    previous = app.state.auth_required
    app.state.auth_required = True
    auth_mod.LIMITER._fails.clear()
    conn = app_mod._new_conn()
    try:
        db.create_account(
            conn, username="alice", password_hash=auth_mod.hash_password("pw-1")
        )
    finally:
        conn.close()
    with TestClient(app) as c:
        assert c.post(
            "/api/login", json={"username": "alice", "password": "pw-1"}
        ).status_code == 200
        yield c
    app.state.auth_required = previous


def _werewolf(client: TestClient) -> dict:
    res = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "9"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_snapshot_never_exposes_others_private_state(client):
    """回归钉子：共享快照里**别人**的私有状态（资源余量 / 笔记）必须为空。

    本轮实测到的泄露就是这条：技能资源余量没进私有字段过滤，
    「两种药各一瓶的角色」被所有人看见 → 隐藏身份当场作废。
    """
    data = _werewolf(client)
    sid = data["session_id"]
    ids = [str(item["id"]) for item in data["characters"]]
    viewer = ids[0]

    rows = client.get(f"/api/sessions/{sid}?viewer={viewer}").json()["characters"]

    mine = next(r for r in rows if r["id"] == viewer)
    others = [r for r in rows if r["id"] != viewer]
    assert mine, "本人应当看得到自己"
    for row in others:
        assert not row.get("skill_charges"), (
            f"{row['id']} 的资源余量不该出现在别人的快照里"
        )
        assert not row.get("notes"), f"{row['id']} 的私有笔记不该出现在别人的快照里"


def test_the_witch_is_not_exposed_by_her_potions(client):
    """端到端：手上有两种药的那位，在**别人**的快照里看不出她是谁。"""
    data = _werewolf(client)
    sid = data["session_id"]
    ids = [str(item["id"]) for item in data["characters"]]

    # 上帝视角找出「手里有资源」的那位（真相只在这里拿）
    chars = client.get(f"/api/sessions/{sid}").json()["characters"]
    holder = next((str(r["id"]) for r in chars if r.get("skill_charges")), "")
    if not holder:
        pytest.skip("本局没有带技能资源的角色")

    viewer = next(cid for cid in ids if cid != holder)
    rows = client.get(f"/api/sessions/{sid}?viewer={viewer}").json()["characters"]
    target = next(r for r in rows if r["id"] == holder)

    assert target["profession"] == "", "隐藏身份不该出现在别人的快照里"
    assert not target.get("skill_charges"), "资源余量也不该出现在别人的快照里"
