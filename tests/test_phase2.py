"""二期：人机混合 / 多时间线 / 跨局传承。

人机混合的关键契约（见知识库 `过程记录/方案/03-二期-人机混合与多时间线.md`）：
- 人类席位只在 DECLARE 阶段等人；
- 超时或显式 ghost → LLM 代打，整局不卡死；
- 人类提交与 LLM 输出共用 `PlayerAction.from_raw`。
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.engine.human import HumanSeat
from trpg.engine.loop import GameLoop
from trpg.game import build_state
from trpg.agents.mock import MockLLM
from trpg.models import PlayerAction
from trpg.rules.loader import Rules
from support.builders import make_card, make_state
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]
app_mod = importlib.import_module("trpg.api.deps")

HAS_LONGZU = (ROOT / "worlds" / "longzu" / "timeline.yaml").exists()


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "phase2.db")
    monkeypatch.setattr(app.state, "sessions", {})
    return TestClient(app)


# ── HumanSeat ───────────────────────────────────────────────
def test_seat_submit_then_wait():
    async def go():
        seat = HumanSeat(["chr_a"], timeout=2)
        task = asyncio.create_task(seat.wait_action("chr_a", "A", 1, ["队友先动"], ["观察"]))
        await asyncio.sleep(0.05)
        pending = seat.pending()
        assert pending["char_id"] == "chr_a"
        assert pending["skills"] == ["观察"]
        assert pending["remaining_ms"] > 0
        assert seat.submit("chr_a", {"action": "扑上去"})
        return await task

    out = asyncio.run(go())
    assert out["action"] == "扑上去"


def test_seat_timeout_returns_none():
    async def go():
        seat = HumanSeat(["chr_a"], timeout=0.05)
        return await seat.wait_action("chr_a", "A", 1, [], [])

    assert asyncio.run(go()) is None


def test_seat_submit_without_pending_is_false():
    seat = HumanSeat(["chr_a"], timeout=1)
    assert seat.submit("chr_a", {"action": "x"}) is False


def test_seat_ghost_payload_recognised():
    async def go():
        seat = HumanSeat(["chr_a"], timeout=1)
        task = asyncio.create_task(seat.wait_action("chr_a", "A", 1, [], []))
        await asyncio.sleep(0.02)
        seat.submit("chr_a", {"ghost": True})
        return await task, seat.log

    out, log = asyncio.run(go())
    assert out.get("ghost") is True
    assert any(item["kind"] == "ghost" for item in log)


# ── loop 集成：人类超时 → LLM 代打 ───────────────────────────
def test_loop_falls_back_when_human_silent():
    rules = Rules(ROOT)
    cards = {"chr_test01": make_card()}
    seat = HumanSeat(["chr_test01"], timeout=0.05)
    loop = GameLoop(rules, cards, make_state(cards["chr_test01"]), MockLLM(7), seed=7, human=seat)

    events: list[str] = []
    rec = asyncio.run(loop.step(progress=lambda ev, data=None: events.append(ev)))

    assert "human_wait" in events
    assert "human_done" in events
    assert rec.actions, "超时后应由 LLM 代打，而不是空行动"


# ── 人类提交与 LLM 共用同一份解析 ────────────────────────────
def test_from_raw_shared_parsing():
    human = PlayerAction.from_raw("chr_x", {"action": "砍它", "mode": "攻击", "intent": "all_in"})
    assert human.mode.value == "attack"
    assert human.intent.value == "all_in"
    llm_like = PlayerAction.from_raw("chr_x", {"action": "砍它", "mode": "attack"})
    assert llm_like.mode == human.mode


# ── 多时间线 ────────────────────────────────────────────────
@pytest.mark.skipif(not HAS_LONGZU, reason="longzu 世界包不公开")
def test_build_state_follows_timeline():
    pack = load_world("longzu", ROOT)
    cards = {"chr_p1": make_card("chr_p1")}
    state = build_state(pack, cards, timeline="academy_year1")

    assert state.world.timeline == "academy_year1"
    assert state.world.era == pack.timeline_era("academy_year1")
    assert state.clock.hour == 8  # 报到日 08:00
    assert pack.timeline_available("academy_year1") is True


@pytest.mark.skipif(not HAS_LONGZU, reason="longzu 世界包不公开")
def test_locked_timeline_rejected(api):
    res = api.post(
        "/api/sessions",
        json={"world": "longzu", "timeline": "academy_year2", "provider": "mock"},
    )
    assert res.status_code == 400
    assert "未解锁" in res.text


def test_worlds_expose_timelines(api):
    worlds = api.get("/api/worlds").json()["worlds"]
    ironvale = next(w for w in worlds if w["id"] == "ironvale")
    assert "timelines" in ironvale
    assert "default_timeline" in ironvale


# ── 跨局传承 ────────────────────────────────────────────────
def test_inherit_previous_ending(api):
    first = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "51"}
    ).json()
    sid = first["session_id"]
    api.post(f"/api/sessions/{sid}/advance")
    assert api.post(f"/api/sessions/{sid}/end").status_code == 200

    second = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "52", "inherit_from": sid},
    )
    assert second.status_code == 200
    flags = second.json()["party"]["flags"]
    assert any(str(f).startswith("legacy:") for f in flags)


# ── 人类席位接口 ────────────────────────────────────────────
def test_pending_is_null_without_human(api):
    sid = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "61"}
    ).json()["session_id"]
    data = api.get(f"/api/sessions/{sid}/pending").json()
    assert data["pending"] is None
    assert data["human_ids"] == []


def test_human_ids_validated(api):
    res = api.post(
        "/api/sessions",
        json={
            "world": "ironvale",
            "players": 2,
            "provider": "mock",
            "seed": "62",
            "human_ids": ["chr_nobody"],
        },
    )
    assert res.status_code == 400
    assert "不存在的角色" in res.text


def test_action_rejected_when_not_waiting(api):
    sid = api.post(
        "/api/sessions",
        json={
            "world": "ironvale",
            "players": 2,
            "provider": "mock",
            "seed": "63",
            "human_ids": ["chr_p1"],
        },
    ).json()["session_id"]
    res = api.post(f"/api/sessions/{sid}/action", json={"action": "打"})
    assert res.status_code == 409


# ── 端到端：advance 等人 → 人在等待期间提交 → 回合继续 ────────
def test_human_submit_during_advance(api):
    import threading
    import time

    sid = api.post(
        "/api/sessions",
        json={
            "world": "ironvale",
            "players": 2,
            "provider": "mock",
            "seed": "64",
            "human_ids": ["chr_p1"],
        },
    ).json()["session_id"]

    box: dict = {}

    def run_advance() -> None:
        try:
            with api.stream("GET", f"/api/sessions/{sid}/advance/stream") as res:
                box["body"] = "".join(res.iter_text())
        except Exception as exc:  # noqa: BLE001
            box["err"] = str(exc)

    worker = threading.Thread(target=run_advance, daemon=True)
    worker.start()

    pending = None
    for _ in range(100):
        pending = api.get(f"/api/sessions/{sid}/pending").json()["pending"]
        if pending:
            break
        time.sleep(0.05)

    assert pending is not None, "advance 期间应进入人类等待"
    assert pending["char_id"] == "chr_p1"

    submitted = api.post(
        f"/api/sessions/{sid}/action",
        json={"action": "压低身形，用短刀挑开它的管线", "skill": "器械", "mode": "攻击"},
    )
    assert submitted.status_code == 200

    worker.join(timeout=30)
    assert not worker.is_alive()
    assert "event: done" in box.get("body", "")
    assert "event: human_done" in box.get("body", "")


# ── 回归：同一库开第二局不能被锁（真实 bug：写库不提交会悬挂写锁）──
def test_second_session_same_db_is_not_locked(api):
    first = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "81"}
    )
    assert first.status_code == 200
    second = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "82"}
    )
    assert second.status_code == 200, second.text[:200]


def test_two_sessions_can_advance_in_sequence(api):
    a = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "83"}
    ).json()["session_id"]
    b = api.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "84"}
    ).json()["session_id"]
    assert api.post(f"/api/sessions/{a}/advance").status_code == 200
    assert api.post(f"/api/sessions/{b}/advance").status_code == 200
    assert api.post(f"/api/sessions/{a}/advance").status_code == 200


# ── 回归：角色卡不可跨世界使用（血统/身份/天赋都是世界包专属 id）──
def test_cross_world_character_rejected(api):
    ids = api.post("/api/characters/presets?world=longzu&players=2&seed=91").json()["ids"]
    res = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "character_ids": ids},
    )
    assert res.status_code == 400
    assert "别的世界包" in res.text


def test_same_world_character_ok(api):
    ids = api.post("/api/characters/presets?world=ironvale&players=2&seed=92").json()["ids"]
    res = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "character_ids": ids},
    )
    assert res.status_code == 200, res.text[:200]
