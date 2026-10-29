"""S0-1 回归：G9 实测的三个「不报错、只是不生效」的前端 bug。

三个 bug 的共同点是**没有异常、没有报错，只是行为不发生**：

1. SSE 结束后「叙事生成中…」永不复位 → 玩家以为卡死；
2. 复盘链接缺 `?sid=` → 点进去是「缺少 ?sid=」错误页；
3. 刷新后主时间线清零（角色状态还在、事件全没了）。

前端没有 JS 测试运行时，所以这里分两层钉死：

- **源码断言**：复位逻辑与链接构造必须在源码里（被删掉立刻红）；
- **行为断言**：回填端点的真实返回（空局为空、跑过一回合后有报幕与行动）。

回填**只读**且**不越权**：私密心声仍按 `visibility.thoughts` 裁剪，
回填不能成为绕可见性的后门。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@pytest.fixture
def client(tmp_path, monkeypatch):
    app_mod = importlib.import_module("trpg.api.deps")
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "s0.db")
    monkeypatch.setattr(app.state, "sessions", {})
    with TestClient(app) as c:
        yield c


def _src_text() -> str:
    parts = []
    for path in sorted(SRC.rglob("*")):
        if path.is_file() and path.suffix in {".vue", ".js"}:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _create(client: TestClient, world: str = "ironvale", players: int = 2, seed: str = "88") -> str:
    res = client.post(
        "/api/sessions",
        json={"world": world, "players": players, "provider": "mock", "seed": seed},
    )
    assert res.status_code == 200, res.text
    return res.json()["session_id"]


# ── bug 2：复盘链接必须带 sid ──────────────────────────────


def test_trace_link_carries_sid():
    """链接构造收口在 `lib/route.js::tracePath`，页面不许再写死 `/trace`。"""
    route = (SRC / "lib" / "route.js").read_text(encoding="utf-8")
    assert "export function tracePath" in route, "route.js 必须导出 tracePath（链接构造只留一处）"
    assert "?sid=" in route, "tracePath 必须把 sid 拼进 query"

    page = (SRC / "pages" / "IndexPage.vue").read_text(encoding="utf-8")
    assert ':href="traceHref"' in page, "复盘链接必须绑定计算出的地址（带 sid）"
    assert 'href="/trace"' not in page, "复盘链接不能再写死 /trace（会丢 sid）"


# ── bug 1：SSE 结束后必须复位「生成中」 ────────────────────


def test_stream_state_resets_on_every_exit_path():
    page = (SRC / "pages" / "IndexPage.vue").read_text(encoding="utf-8")
    assert "const streaming = ref(false)" in page, "「生成中」必须是独立状态，不能混在 timing 文案里"
    # 复位必须写在 finally 里：正常 done / error 事件 / 网络断线三条路都要走到
    assert "streaming.value = false" in page
    assert "finally {" in page
    assert 'id="streaming"' in page, "生成中要有可见标志，结束后随之消失"


# ── bug 3：刷新后时间线回填 ────────────────────────────────


def test_history_endpoint_exists():
    assert "/api/sessions/{sid}/history" in set(app.openapi()["paths"])


def test_history_uses_rule_window(client):
    """窗口大小来自 `rules/ruleset.yaml: ui.history_turns*`，不写死在代码里。"""
    sid = _create(client)
    data = client.get(f"/api/sessions/{sid}/history").json()
    ruleset = (ROOT / "rules" / "ruleset.yaml").read_text(encoding="utf-8")
    assert "history_turns:" in ruleset and "history_turns_max:" in ruleset
    assert data["window"] == 3, "默认窗口应取 ruleset 的 ui.history_turns"
    assert client.get(f"/api/sessions/{sid}/history?turns=999").json()["window"] <= 10


def test_history_empty_before_any_turn(client):
    sid = _create(client)
    data = client.get(f"/api/sessions/{sid}/history").json()
    assert data["timeline"] == [], "还没打过回合的局不该编造历史"
    assert data["from_db"] is False


def test_history_backfills_after_a_turn(client):
    sid = _create(client)
    assert client.post(f"/api/sessions/{sid}/advance").status_code == 200

    data = client.get(f"/api/sessions/{sid}/history").json()
    kinds = [item["kind"] for item in data["timeline"]]
    assert data["from_db"] is True
    assert kinds[0] == "banner", "每一回合从报幕开始（时间线的段首）"
    assert "actor" in kinds, "回填必须包含「谁做了什么」"

    # 幂等：连查两次结果完全一致（只读端点，重跑不产生副作用）
    again = client.get(f"/api/sessions/{sid}/history").json()
    assert again["timeline"] == data["timeline"]


def test_history_keeps_thoughts_off_for_participants(client):
    """`visibility.thoughts: god` 的世界里，回填不能把别人的盘算漏给参与者。"""
    sid = _create(client, world="werewolf", players=8, seed="99")
    assert client.post(f"/api/sessions/{sid}/advance").status_code == 200

    snapshot = client.get(f"/api/sessions/{sid}/table").json()
    viewer = snapshot["characters"][0]
    data = client.get(f"/api/sessions/{sid}/history?viewer={viewer['id']}").json()

    leaked = [
        item
        for item in data["timeline"]
        if item.get("thought") and item.get("who") != viewer["name"]
    ]
    assert not leaked, f"回填泄露了别人的心声：{leaked[:1]}"
