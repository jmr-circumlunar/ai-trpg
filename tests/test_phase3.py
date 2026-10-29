"""三期：局外成长 / 世界账本 / 时间线推进 / 物品晋升 / 世界包自检。

覆盖知识库 `过程记录/方案/04-三期-局外成长插件化.md` 的落地形态：
- 点数是通用的，价目表是世界的；
- 世界账本：世界侧的跨局状态；
- 同世界连续多局：world_day 累加；
- 物品晋升：使用次数达阈值 → 人工确认 → 写回世界包。
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.rules.loader import Rules
from trpg.storage import db
from trpg.storage import ledger as world_ledger
from support.builders import make_card, make_state
from trpg.world import load_world
from trpg.equipment.promote import append_item_template, template_id_for

ROOT = Path(__file__).resolve().parents[1]
app_mod = importlib.import_module("trpg.api.deps")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "phase3.db")
    monkeypatch.setattr(app.state, "sessions", {})
    return TestClient(app)


# ── 时间线推进 ──────────────────────────────────────────────
def test_world_day_accumulates(api):
    a = api.post("/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "1"})
    b = api.post("/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "2"})
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["world_day"] == 1
    assert b.json()["world_day"] == 2


def test_world_day_shifts_start_clock(api):
    """第二局的开局日期应往后推一天（同世界连续）。"""
    a = api.post("/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "3"})
    b = api.post("/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "4"})
    assert b.json()["world_day"] == 2
    assert b.json()["clock"]["day"] == a.json()["clock"]["day"] + 1


# ── 世界账本 ────────────────────────────────────────────────
def test_ledger_records_ending(api):
    sid = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "5"}
    ).json()["session_id"]
    api.post(f"/api/sessions/{sid}/advance")
    api.post(f"/api/sessions/{sid}/end")

    conn = app_mod._new_conn()
    try:
        items = world_ledger.read(conn, "ironvale")
        assert items, "结算后应该写进世界账本"
        assert any(i["kind"] == "ending" for i in items)
    finally:
        conn.close()


def test_ledger_shows_up_in_next_session(api):
    sid = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "6"}
    ).json()["session_id"]
    api.post(f"/api/sessions/{sid}/advance")
    api.post(f"/api/sessions/{sid}/end")

    nxt = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "7"}
    )
    assert nxt.status_code == 200
    # 账本摘要要能读出来（它会被拼进新局的 GM 上下文）
    conn = app_mod._new_conn()
    try:
        text = world_ledger.summary(conn, "ironvale")
    finally:
        conn.close()
    assert isinstance(text, str)


# ── 物品晋升 ────────────────────────────────────────────────
def test_item_usage_bump_and_candidates(api):
    conn = app_mod._new_conn()
    try:
        db.bump_item_usage(conn, "ironvale", "ses_1", "半张烧焦的照片", [], 3)
        db.bump_item_usage(conn, "ironvale", "ses_1", "半张烧焦的照片", [], 4)
        db.bump_item_usage(conn, "ironvale", "ses_2", "半张烧焦的照片", [], 1)
        rows = db.item_usage_candidates(conn, "ironvale")
        item = next(r for r in rows if r["canonical"] == "半张烧焦的照片")
        assert item["uses"] == 3
        assert item["sessions"] == 2
    finally:
        conn.close()

    res = api.get("/api/items/candidates?world=ironvale")
    assert res.status_code == 200
    data = res.json()
    assert data["thresholds"]["prop_to_instance"] == 3
    assert any(c["canonical"] == "半张烧焦的照片" for c in data["candidates"])


def test_promote_appends_template_without_breaking_yaml(tmp_path):
    world_dir = tmp_path / "w"
    world_dir.mkdir()
    (world_dir / "items.yaml").write_text(
        "# 注释要保住\nitems:\n  - id: tpl_a\n    name: 甲\n    slot: tool\n    tier: 1\n",
        encoding="utf-8",
    )
    tid = template_id_for("旧打火机")
    append_item_template(
        world_dir, template_id=tid, name="旧打火机", slot="trinket", tier=1, tags=["light_source"]
    )
    text = (world_dir / "items.yaml").read_text(encoding="utf-8")
    assert "# 注释要保住" in text
    import yaml

    data = yaml.safe_load(text)
    assert len(data["items"]) == 2
    assert data["items"][1]["id"] == tid


def test_promote_endpoint_only_creates_pending_patch(api):
    """架构约束（02.9 数据层只读）：运行时 API **不改**世界包，只生成待确认补丁。"""
    items = ROOT / "worlds" / "ironvale" / "items.yaml"
    before = items.read_text(encoding="utf-8")

    res = api.post(
        "/api/items/promote",
        json={"world": "ironvale", "canonical": "测试用物", "tier": 1, "slot": "trinket"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["applied"] is False, "运行时不该直接生效"
    assert data["template_id"].startswith("tpl_")
    assert data["pending"].endswith(".yaml")

    assert items.read_text(encoding="utf-8") == before, "运行时不应该动世界包"

    pending = ROOT / data["pending"]
    assert pending.exists()
    pending.unlink()


def test_apply_promotions_tool_merges_into_world(tmp_path):
    """人工工具：把待确认补丁真正写进世界包（只追加、幂等）。"""
    from trpg.equipment.promote import append_item_template, build_patch, write_pending

    world_dir = tmp_path / "w"
    world_dir.mkdir()
    (world_dir / "items.yaml").write_text(
        "# 注释要保住\nitems:\n  - id: tpl_a\n    name: 甲\n    slot: tool\n    tier: 1\n",
        encoding="utf-8",
    )
    patch = build_patch("w", "旧打火机", slot="trinket", tier=1, tags=["light_source"])
    write_pending(tmp_path, patch)

    # 幂等：写两次只留一条
    for _ in range(2):
        append_item_template(
            world_dir,
            template_id=patch["template_id"],
            name="旧打火机",
            slot="trinket",
            tier=1,
            tags=["light_source"],
        )
    import yaml

    data = yaml.safe_load((world_dir / "items.yaml").read_text(encoding="utf-8"))
    assert len(data["items"]) == 2
    assert "# 注释要保住" in (world_dir / "items.yaml").read_text(encoding="utf-8")


# ── 世界包自检工具 ──────────────────────────────────────────
def test_check_world_tool_runs():
    proc = subprocess.run(
        [sys.executable, "tools/check_world.py", "ironvale"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "检查通过" in proc.stdout


# ── 局外成长接口 ────────────────────────────────────────────
def test_meta_endpoint_lists_shop(api):
    res = api.get("/api/meta?world=ironvale")
    assert res.status_code == 200
    data = res.json()
    assert data["currency"]["label"] == "铁印"
    assert data["spend"], "应该列出本世界可买项"
    assert all("cost" in item and "ok" in item for item in data["spend"])


def test_meta_spend_endpoint(api):
    ids = api.post("/api/characters/presets?world=ironvale&players=1&seed=8").json()["ids"]
    cid = ids[0]
    # 先给点：直接写库（单机阶段没有「赚点」界面）
    conn = app_mod._new_conn()
    try:
        from trpg.growth.wallet import Wallet

        Wallet(conn).add("point", 20)
    finally:
        conn.close()

    res = api.post(
        "/api/meta/spend",
        json={"world": "ironvale", "character_id": cid, "kind": "skill", "target": "撬锁"},
    )
    assert res.status_code == 200, res.text[:200]
    assert res.json()["ok"] is True


# ── 三期机制的「真链路」测试（补上之前只有单测覆盖的部分）────────
def test_ending_grants_points_end_to_end(api):
    """结算 → ending 事件 → 发点（世界包价目表决定金额）。"""
    sid = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "31"}
    ).json()["session_id"]
    api.post(f"/api/sessions/{sid}/advance")
    api.post(f"/api/sessions/{sid}/end")

    meta = api.get("/api/meta?world=ironvale").json()
    assert meta["balance"] == 2, "ironvale 的 ending 价目是 2 点"
    assert meta["ledger"] and meta["ledger"][0]["reason"].startswith("ending")


def test_death_event_is_recorded(api):
    """角色死亡 → 引擎发 death 事件 → 发点（与结算发点共用一条价目表）。"""
    from trpg.engine.loop import GameLoop
    from trpg.agents.mock import MockLLM
    from trpg.models import CharacterRuntime
    from trpg.models.enums import Position

    rules = Rules(ROOT)
    cards = {"chr_test01": make_card("chr_test01")}
    state = make_state(cards["chr_test01"])
    state.characters["chr_test01"] = CharacterRuntime(
        hp=0, sanity=5, position=Position.scene, dying_since=1
    )
    loop = GameLoop(rules, cards, state, MockLLM(3), seed=3, pack=load_world("ironvale", ROOT))
    loop._resolve_death("chr_test01", 3, None)
    assert "death" in loop.meta_events


def test_carry_injects_trait_into_session_cards(api):
    """开局时把允许携带的元成长注入本局角色卡。"""
    from trpg.growth.wallet import Wallet

    conn = app_mod._new_conn()
    try:
        Wallet(conn).add("trait:trait_scars", 2, world_id="ironvale")
    finally:
        conn.close()

    ids = api.post("/api/characters/presets?world=ironvale&players=1&seed=32").json()["ids"]
    res = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 1, "provider": "mock", "seed": "33", "character_ids": ids},
    )
    assert res.status_code == 200
    sid = res.json()["session_id"]
    conn = app_mod._new_conn()
    try:
        rows = conn.execute(
            "SELECT card_json FROM session_cards WHERE session_id = ?", (sid,)
        ).fetchall()
    finally:
        conn.close()
    import json

    card = json.loads(rows[0][0])
    scars = card["data"]["extensions"]["ai_trpg"].get("scars") or []
    assert any(sc.get("kind") == "trait_scars" for sc in scars), "携带的痕迹应该进本局的卡"


def test_legacy_block_includes_ledger(api):
    """账本里的事要进 GM 上下文（跨局可见）。"""
    from trpg.api.app import _legacy_block
    from trpg.world import load_world

    sid = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "34"}
    ).json()["session_id"]
    api.post(f"/api/sessions/{sid}/advance")
    api.post(f"/api/sessions/{sid}/end")

    conn = app_mod._new_conn()
    try:
        text = _legacy_block(conn, load_world("ironvale", ROOT))
    finally:
        conn.close()
    assert "已经发生过的事" in text


def test_item_usage_bump_on_check_self(api):
    """check_self 列出物品 → 记一次使用（晋升判据的埋点）。"""
    from trpg.agents.agent import AgentLLM
    from trpg.agents.spec import LLMConfig
    from trpg.storage.memory import EventStore

    card = make_card("chr_test01")
    card.ext.equipment = []
    conn = app_mod._new_conn()
    try:
        store = EventStore(conn, "ses_x")
        agent = AgentLLM(LLMConfig(ROOT), {"chr_test01": card}, events=store)
        state = make_state(card)
        state.characters["chr_test01"].inventory = ["半张烧焦的照片"]
        agent._tool_check_self("chr_test01", card, state, {}, 1)
        rows = conn.execute(
            "SELECT canonical FROM item_usage WHERE session_id = ?", ("ses_x",)
        ).fetchall()
    finally:
        conn.close()
    assert any(r[0] == "半张烧焦的照片" for r in rows)
