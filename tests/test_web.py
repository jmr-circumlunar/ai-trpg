"""web 层：SSE 流式推进、trace 数据、三个页面的可访问性。

SSE 的定位（见 `02.5-接口与前端`）：本项目 LLM 输出是 JSON，token 级流式没有意义，
所以推的是**阶段事件**（场景 / 每个角色 / 叙事 / 提交），用来消掉"30 秒没有反馈"。
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.models.state import is_eliminated

app_mod = importlib.import_module("trpg.api.deps")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "web.db")
    monkeypatch.setattr(app.state, "sessions", {})
    return TestClient(app)


def _new_session(api, seed: str = "31") -> str:
    created = api.post(
        "/api/sessions",
        json={"world": "ironvale", "players": 2, "provider": "mock", "seed": seed},
    )
    assert created.status_code == 200
    return created.json()["session_id"]


# ── SSE ─────────────────────────────────────────────────────
def test_stream_emits_stage_events(api):
    sid = _new_session(api)
    with api.stream("GET", f"/api/sessions/{sid}/advance/stream") as res:
        assert res.status_code == 200
        body = "".join(res.iter_text())

    for event in ("stage", "scene", "actor", "narrate", "commit", "done"):
        assert f"event: {event}" in body, f"缺少 {event} 事件"
    assert '"turn": 1' in body


def test_stream_unknown_session_404(api):
    res = api.get("/api/sessions/nope/advance/stream")
    assert res.status_code == 404


def test_stream_advances_state(api):
    sid = _new_session(api, seed="32")
    with api.stream("GET", f"/api/sessions/{sid}/advance/stream") as res:
        list(res.iter_text())
    snap = api.get(f"/api/sessions/{sid}").json()
    assert snap["turns"] == 1
    assert snap["version"] >= 1


# ── 组内商议与遗言（B1 / B2 · C21）──────────────────────────
#
# 这两类事件后端一直在发，但**从没人断言过**——前端漏接也就没人发现。
# 裁剪语义同样在这里守住：`council` 只发给该看的人，不是「发了不显示」。


def _werewolf(api, seed: str = "77") -> str:
    created = api.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": seed},
    )
    assert created.status_code == 200, created.text
    return created.json()["session_id"]


def _events(api, sid: str, viewer: str | None = None) -> list[tuple[str, dict]]:
    """推进一回合，返回 [(事件名, data)]。`viewer` 为空 = 上帝视角。"""
    url = f"/api/sessions/{sid}/advance/stream"
    if viewer is not None:
        url += f"?viewer={viewer}"
    out: list[tuple[str, dict]] = []
    with api.stream("GET", url) as res:
        assert res.status_code == 200
        event = None
        for line in res.iter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: ") and event:
                out.append((event, json.loads(line[6:])))
                event = None
    return out


def _named(events, name: str) -> list[dict]:
    return [data for event, data in events if event == name]


def _camps(sid: str) -> dict[str, str]:
    session = app.state.sessions[sid]
    return {
        cid: str(getattr(rt, "camp", "") or "")
        for cid, rt in session.loop.state.characters.items()
    }


def test_stream_emits_council_and_eulogy(api):
    """组内商议与出局遗言必须出现在 SSE 里，且载荷字段齐全。"""
    sid = _werewolf(api)
    events = _events(api, sid)

    council = _named(events, "council")
    assert council, "第一夜应当有组内商议事件"
    for data in council:
        assert {"group", "name", "text", "audience"} <= set(data)
        assert data["group"] and data["name"] and data["text"]
        # 空列表等于「不限」，那就退化成全量推送了，必须非空
        assert data["audience"], "audience 不能为空"

    eulogy = _named(events, "eulogy")
    assert eulogy, "第一夜有出局就该有遗言事件"
    for data in eulogy:
        assert {"id", "name", "text"} <= set(data)
        assert data["id"] and data["text"]

    # 遗言同时落进 `done` 回执的 deaths（前端归档以它为准，160 字）
    done = _named(events, "done")[0]
    assert any(d.get("last_words") for d in done["deaths"])


def test_council_is_cropped_by_viewer(api):
    """同一局开局、三种视角各跑一回合：上帝看得见，组内看得见，组外**收不到**。

    每个视角都要**新开一局**：连跑同一局会让旁观者在夜里出局，
    出局者按观察者处理（那时能看见是对的，见下一个用例），就测不出裁剪了。
    """
    for kind, expect in (("god", True), ("insider", True), ("outsider", False)):
        sid = _werewolf(api)
        camps = _camps(sid)
        if kind == "god":
            viewer = None
        elif kind == "insider":
            viewer = next(cid for cid, camp in camps.items() if camp == "wolf")
        else:
            viewer = next(cid for cid, camp in camps.items() if camp != "wolf")

        got = bool(_named(_events(api, sid, viewer), "council"))
        assert got is expect, f"{kind} 视角（viewer={viewer}）的商议可见性不对"


def test_dead_viewer_sees_council_again(api):
    """出局者回到观察者视角：复盘时能看全部商议（不是泄密，是死后复盘的正当需求）。"""
    sid = _werewolf(api, seed="78")
    _events(api, sid)  # 先跑一回合，制造出局者

    camps = _camps(sid)
    dead = [
        cid
        for cid, rt in app.state.sessions[sid].loop.state.characters.items()
        if is_eliminated(rt) and camps.get(cid) != "wolf"
    ]
    assert dead, "第一夜应当有组外（村民一方）出局者"

    assert _named(_events(api, sid, dead[0]), "council"), "出局者应看到商议"


# ── trace ───────────────────────────────────────────────────
def test_trace_has_dice_and_director(api):
    sid = _new_session(api, seed="33")
    api.post(f"/api/sessions/{sid}/advance")
    trace = api.get(f"/api/sessions/{sid}/trace").json()

    assert trace["dice"], "骰子明细必须能从库里读出来"
    assert trace["dice"][0]["actor_id"]
    assert trace["dice"][0]["rng_state"]
    assert trace["turns"][0]["loot"] == []
    assert trace["director"], "导演日志要能看到（固定事件 / 幕次决策）"
    assert "recall" in trace


# ── 页面 ────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/", "/characters", "/trace", "/gm"])
def test_pages_render(api, path):
    res = api.get(path)
    assert res.status_code == 200
    assert "<html" in res.text.lower()


def test_worlds_expose_origins_endpoint(api):
    res = api.get("/api/worlds/ironvale/origins")
    assert res.status_code == 200
    data = res.json()
    assert data["origins"] == []  # ironvale 是单层职业世界
    assert data["paths"] == []


def test_skill_events_are_private(api):
    """夜间技能（查验 / 用药 / 毒杀）不能全员可见——那等于当众公布神职身份。

    开枪与自爆是公开事件，不在裁剪范围；这里只看第一夜（全是私密技能）。
    """
    for kind, expect in (("god", True), ("outsider", False)):
        sid = _werewolf(api)
        camps = _camps(sid)
        # 旁观者必须挑「没有技能的人」（村民）：神职自己就是技能的 audience
        cards = app.state.sessions[sid].loop.cards
        viewer = (
            None
            if kind == "god"
            else next(
                cid
                for cid, camp in camps.items()
                if camp != "wolf"
                and str(getattr(cards[cid].ext, "profession", "")) == "村民"
            )
        )

        casts = _named(_events(api, sid, viewer), "skill")
        assert bool(casts) is expect, f"{kind} 视角的技能事件可见性不对"
        for data in casts:
            assert {"id", "name", "label", "effect"} <= set(data)


def test_receipt_does_not_leak_skill_state(api):
    """回执里不能出现 `healed` / `saved`：能反推「谁被救了」就等于公开女巫行动。"""
    sid = _werewolf(api)

    done = _named(_events(api, sid), "done")[0]

    assert "healed" not in done["vote_result"]
    assert "saved" not in done["vote_result"]


def test_night_actions_are_private(api):
    """夜里「谁动了手」本身就是信息（等于标出阵营）。

    `visibility: group` 的阶段动作只发给本阶段参与者：村民视角拿不到狼的夜间行动，
    上帝视角照常全见。白天公开阶段的行动不受影响。
    """
    god_sid = _werewolf(api, seed="606")
    camps = _camps(god_sid)
    cards = app.state.sessions[god_sid].loop.cards
    wolves = {cid for cid, camp in camps.items() if camp == "wolf"}
    god_acts = _named(_events(api, god_sid), "actor")
    night_acts = [item for item in god_acts if item.get("audience")]
    assert len(night_acts) == len(wolves), "夜间每个狼各一条行动事件"
    assert all(set(item["audience"]) == wolves for item in night_acts)

    out_sid = _werewolf(api, seed="606")
    out_camps = _camps(out_sid)
    out_cards = app.state.sessions[out_sid].loop.cards
    outsider = next(
        cid
        for cid, camp in out_camps.items()
        if camp != "wolf" and str(getattr(out_cards[cid].ext, "profession", "")) == "村民"
    )
    out_acts = _named(_events(api, out_sid, outsider), "actor")

    assert len(out_acts) == len(god_acts) - len(night_acts), "村民只该收到公开阶段的行动"
    assert all("audience" not in item for item in out_acts)


def test_night_actions_are_private(api):
    """夜里「谁动了手」本身就是信息（等于标出阵营）。

    `visibility: group` 的阶段动作只发给本阶段参与者：村民视角拿不到狼的夜间行动，
    上帝视角照常全见。白天公开阶段的行动不受影响。
    """
    god_sid = _werewolf(api, seed="606")
    camps = _camps(god_sid)
    cards = app.state.sessions[god_sid].loop.cards
    wolves = {cid for cid, camp in camps.items() if camp == "wolf"}
    god_acts = _named(_events(api, god_sid), "actor")
    night_acts = [item for item in god_acts if item.get("audience")]
    assert len(night_acts) == len(wolves), "夜间每个狼各一条行动事件"
    assert all(set(item["audience"]) == wolves for item in night_acts)

    out_sid = _werewolf(api, seed="606")
    out_camps = _camps(out_sid)
    out_cards = app.state.sessions[out_sid].loop.cards
    outsider = next(
        cid
        for cid, camp in out_camps.items()
        if camp != "wolf" and str(getattr(out_cards[cid].ext, "profession", "")) == "村民"
    )
    out_acts = _named(_events(api, out_sid, outsider), "actor")

    assert len(out_acts) == len(god_acts) - len(night_acts), "村民只该收到公开阶段的行动"
    assert all("audience" not in item for item in out_acts)
