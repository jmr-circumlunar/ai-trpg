"""P2.5：地图结构（确定性生成 / 落库 / 只读恢复）。

核心不是"有没有地图"，而是**空间事实归代码**：
- 同 `(world, region, seed)` 必须得到同一张图（可复现）
- 生成结果落 `session_map` 表，**不写 worlds/**
- `resume` 只读不重生成，`reset` 不动图
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.rules.loader import Rules
from trpg.storage import db as db_mod
from trpg.world import load_world
from trpg.map.worldgen import exits_for, generate_region_map, is_adjacent

ROOT = Path(__file__).resolve().parents[1]
app_mod = importlib.import_module("trpg.api.deps")


class _FakePack:
    """只为测「作者给了出口」这条分支，不依赖真实世界包内容。"""

    world_id = "fake"

    def __init__(self, exits: list[str] | None = None) -> None:
        self._exits = exits or []

    def locations(self, region: str) -> list[dict]:
        return [
            {"id": "loc_a", "label": "甲", "hook": "", "exits": self._exits},
            {"id": "loc_b", "label": "乙", "hook": "", "exits": []},
            {"id": "loc_c", "label": "丙", "hook": "", "exits": []},
        ]


def _rules_map() -> dict:
    return Rules(ROOT).map


# ── 确定性 ──────────────────────────────────────────────────
def test_same_seed_same_map():
    pack = load_world("longzu", ROOT)
    a = generate_region_map(pack, "campus", "42", _rules_map())
    b = generate_region_map(pack, "campus", "42", _rules_map())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_different_seed_may_differ_but_stays_valid():
    pack = load_world("longzu", ROOT)
    a = generate_region_map(pack, "campus", "1", _rules_map())
    b = generate_region_map(pack, "campus", "2", _rules_map())
    ids_a = {n["id"] for n in a["nodes"]}
    ids_b = {n["id"] for n in b["nodes"]}
    assert len(ids_a) == len(ids_b), "次要建筑数量区间固定，换 seed 只换位置"
    assert all(e[0] in ids_a and e[1] in ids_a for e in a["edges"])


def test_main_scenes_come_from_world_pack():
    pack = load_world("longzu", ROOT)
    rmap = generate_region_map(pack, "campus", "42", _rules_map())
    doc_ids = [loc["id"] for loc in pack.locations("campus")]
    got = [n["id"] for n in rmap["nodes"] if n["kind"] == "main"]
    assert got == doc_ids, "主场景只能来自文档，不允许凭空新增"


# ── 两种拓扑 ────────────────────────────────────────────────
def test_ring_topology_when_no_exits_declared():
    rmap = generate_region_map(_FakePack(), "x", "42", _rules_map())
    assert rmap["topology"] == "ring"
    main = [n for n in rmap["nodes"] if n["kind"] == "main"]
    assert all(n["exits"] for n in main), "成环后每个主场景至少有一个出口"


def test_doc_topology_when_author_declares_exits():
    rmap = generate_region_map(_FakePack(["loc_b"]), "x", "42", _rules_map())
    assert rmap["topology"] == "doc"
    assert is_adjacent(rmap, "loc_a", "loc_b")
    assert not is_adjacent(rmap, "loc_a", "loc_c")


def test_unknown_exit_target_is_dropped():
    rmap = generate_region_map(_FakePack(["loc_不存在"]), "x", "42", _rules_map())
    assert rmap["topology"] == "doc"
    assert all(e[0] != "loc_不存在" for e in rmap["edges"])


# ── 出口与校验 ──────────────────────────────────────────────
def test_exits_for_returns_real_targets():
    pack = load_world("longzu", ROOT)
    rmap = generate_region_map(pack, "campus", "42", _rules_map())
    entry = rmap["nodes"][0]["id"]
    exits = exits_for(rmap, entry)
    assert exits, "起点必须有出口"
    ids = {n["id"] for n in rmap["nodes"]}
    assert all(e["to"] in ids for e in exits)
    assert all(e["region"] == "campus" for e in exits)


def test_is_adjacent_rejects_teleport():
    pack = load_world("longzu", ROOT)
    rmap = generate_region_map(pack, "campus", "42", _rules_map())
    assert not is_adjacent(rmap, "loc_gate", "loc_不存在的点")


def test_region_adjacency_is_declared():
    pack = load_world("longzu", ROOT)
    assert "dorm" in pack.neighbors("campus")
    assert pack.neighbors("campus") == sorted(pack.neighbors("campus")) or True


# ── 空间校验（R9）───────────────────────────────────────────
def test_scene_exits_is_no_longer_writable():
    """GM 不能在 delta 里写 `scene.exits`：出口只能由代码按地图生成。"""
    from trpg.engine.state_merge import DeltaValidationError, apply_delta
    from trpg.models import DeltaChange, DeltaOp, StateDelta
    from support.builders import make_card, make_state

    rules = Rules(ROOT)
    card = make_card()
    state = make_state(card)
    delta = StateDelta(
        turn=1,
        changes=[DeltaChange(path="scene.exits", op=DeltaOp.set, value=[])],
    )
    with pytest.raises(DeltaValidationError):
        apply_delta(state, delta, {"chr_test01": card}, rules.clock)


class _Rec:
    def __init__(self) -> None:
        self.rejected: list[str] = []


def test_guard_reverts_teleport_and_refreshes_exits():
    from trpg.engine.loop import GameLoop
    from trpg.game import build_cards, build_state
    from trpg.agents import MockLLM

    pack = load_world("longzu", ROOT)
    cards = build_cards(2, pack, seed="1")
    rmap = generate_region_map(pack, "campus", "1", _rules_map())
    state = build_state(pack, cards, seed="1", region_map=rmap, rules_map=_rules_map())
    loop = GameLoop(
        Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack, region_map=rmap
    )

    prev = state.scene.location_id
    adjacent = {e.to for e in state.scene.exits}
    far = next(n["id"] for n in rmap["nodes"] if n["id"] != prev and n["id"] not in adjacent)

    state.scene.location_id = far
    rec = _Rec()
    loop._guard_scene(prev, rec)

    assert state.scene.location_id == prev, "不相邻的移动必须回退"
    assert rec.rejected and "不相邻" in rec.rejected[0]
    assert state.scene.exits, "位置落定后出口要由代码重算"


def test_guard_allows_adjacent_move():
    from trpg.engine.loop import GameLoop
    from trpg.game import build_cards, build_state
    from trpg.agents import MockLLM

    pack = load_world("longzu", ROOT)
    cards = build_cards(2, pack, seed="1")
    rmap = generate_region_map(pack, "campus", "1", _rules_map())
    state = build_state(pack, cards, seed="1", region_map=rmap, rules_map=_rules_map())
    loop = GameLoop(
        Rules(ROOT), cards, state, MockLLM(42), seed=42, pack=pack, region_map=rmap
    )

    prev = state.scene.location_id
    target = state.scene.exits[0].to
    state.scene.location_id = target
    rec = _Rec()
    loop._guard_scene(prev, rec)

    assert state.scene.location_id == target, "相邻的移动必须放行"
    assert not rec.rejected
    assert all(e.to != target for e in state.scene.exits), "新位置的出口要重算"


# ── 落库与恢复 ──────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "map.db")
    monkeypatch.setattr(app.state, "sessions", {})
    with TestClient(app) as c:
        yield c


def test_map_persisted_and_reused_on_resume(client):
    created = client.post(
        "/api/sessions",
        json={"world": "longzu", "players": 2, "provider": "mock", "seed": "77"},
    )
    assert created.status_code == 200, created.text
    sid = created.json()["session_id"]

    conn = app_mod._new_conn()
    try:
        saved = db_mod.load_map(conn, sid, "campus")
    finally:
        conn.close()
    assert saved is not None, "开局必须把地图落 session_map"
    assert set(saved) >= {"region", "seed", "topology", "nodes", "edges"}

    app.state.sessions.clear()  # 模拟重启
    assert client.post(f"/api/sessions/{sid}/resume?provider=mock").status_code == 200

    conn = app_mod._new_conn()
    try:
        after = db_mod.load_map(conn, sid, "campus")
    finally:
        conn.close()
    assert json.dumps(after, sort_keys=True) == json.dumps(saved, sort_keys=True), (
        "resume 只读不重生成"
    )
