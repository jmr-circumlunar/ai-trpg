"""前端与后端的契约。

前端没有 JS 测试运行时，但**接口层最容易悄悄漂**（改了一个字段名、删了一个端点，
页面就静默失效）。所以这里把「页面用到的 id / 端点 / 字段」钉成断言：
前端改了而后端没跟上（或反过来），这里立刻红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.api.app import app
from trpg.engine.human import HumanSeat

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import importlib

    app_mod = importlib.import_module("trpg.api.deps")
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "ui.db")
    monkeypatch.setattr(app.state, "sessions", {})
    return TestClient(app)

# 前端必须提供的元素（id）
INDEX_IDS = [
    "human-panel", "hp-who", "hp-countdown", "hp-state", "hp-briefs", "hp-enemies",
    "hp-thought", "hp-action", "hp-skill", "hp-intent", "hp-mode", "hp-target",
    "hp-submit", "hp-ghost", "hp-hint",
]
GM_IDS = ["timeline", "timeline-hint", "human-slots", "inherit"]

# 前端必须调用的后端端点（模板形式）
FRONTEND_ENDPOINTS = [
    "/api/sessions",
    "/api/sessions/{sid}/advance",
    "/api/sessions/{sid}/advance/stream",
    "/api/sessions/{sid}/action",
    "/api/sessions/{sid}/pending",
    "/api/sessions/{sid}/trace",
    # 刷新后的时间线回填（S0-1 / G9 bug 3）
    "/api/sessions/{sid}/history",
    "/api/worlds",
]

# 前端读到的 pending 字段
# `kind` / `candidates` 是投票链路新增的：前端据此区分「轮到你行动」与「轮到你投票」
PENDING_FIELDS = {
    "char_id", "name", "turn", "briefs", "skills", "timeout_ms", "remaining_ms",
    "kind", "candidates",
}

# 人类提交用到的字段（后端可以多，但多出来的必须可选）
ACTION_FIELDS = {"char_id", "action", "skill", "intent", "mode", "target", "ghost"}
OPTIONAL_ACTION_FIELDS = {"thought", "approach"}


def _routes() -> set[str]:
    # FastAPI 新版本对 include_router 走惰性注册，app.routes 里拿不到具体路径；
    # 用 OpenAPI schema 取得「模板形式」的完整端点表（与前端写法一致）。
    return set(app.openapi()["paths"])


def _page(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ── Vue3 前端（B51）────────────────────────────────────────
#
# 迁移到 Vue 后，元素 id 契约不能丢。这里对**源码**再断言一遍：
# 旧静态页继续被上面的测试守着（它是没装 Node 时的回退路径），
# 新前端被下面的测试守着，两边都不许悄悄改 id。
VUE_SRC = ROOT / "src"


def _vue_text() -> str:
    parts = []
    for path in sorted(VUE_SRC.rglob("*")):
        if path.is_file() and path.suffix in {".vue", ".js", ".html"}:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.mark.parametrize("eid", INDEX_IDS)
def test_index_ids_survive_in_vue_source(eid):
    assert f'id="{eid}"' in _vue_text(), f"Vue 源码里丢了 #{eid}"


@pytest.mark.parametrize("eid", GM_IDS)
def test_gm_ids_survive_in_vue_source(eid):
    assert f'id="{eid}"' in _vue_text(), f"Vue 源码里丢了 #{eid}"


def test_vue_uses_capabilities_for_conditional_blocks():
    """能力开关用 `v-if` **不渲染**，而不是事后隐藏。

    隐藏的元素仍会被读屏软件念出来；不渲染才是真的没有。
    """
    text = _vue_text()
    assert "caps.has(" in text
    assert "caps.has('combat')" in text
    assert "caps.has('human_seat')" in text


# 后端推了、前端没有 case，就等于「功能已实现但用户看不见」（B1 / B2）。
# 前端没有 JS 测试运行时，只能把「这几个事件必须有分支」钉在源码上。
SSE_EVENTS_WITH_UI = ("council", "eulogy", "intervention", "skill")


@pytest.mark.parametrize("event", SSE_EVENTS_WITH_UI)
def test_vue_handles_sse_event(event):
    assert f'case "{event}"' in _vue_text(), f"Vue 源码没有处理 {event} 事件"


def test_turnlog_renders_beats_and_deaths():
    """回合区块要能看到：组内商议、出局名单（含遗言）、回合内的实时动态。"""
    text = (VUE_SRC / "components" / "TurnLog.vue").read_text(encoding="utf-8")
    assert "turn.events" in text, "回合区块要显示组内商议"
    assert "turn.deaths" in text, "回合区块要显示本回合出局名单"
    assert "liveEvents" in text, "回合内要能实时显示商议 / 遗言 / 介入"
    assert "lastWords" in text, "出局名单里要带上遗言"


def test_theme_provides_accessibility_basics():
    """样式系统至少要有：可见焦点环、动效偏好、间距刻度。"""
    theme = (VUE_SRC / "styles" / "theme.css").read_text(encoding="utf-8")
    assert ":focus-visible" in theme
    assert "prefers-reduced-motion" in theme
    assert "--sp-" in theme


def test_pages_serve_even_without_build_output():
    """没装 Node / 没构建产物时，页面仍然要出得来（回退旧静态页）。"""
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/gm").status_code == 200
    assert client.get("/characters").status_code == 200


def test_built_page_is_served_with_its_assets():
    """构建产物在时，页面走 Vue 版，而且引用的 JS 真的能取到。

    这是 G3 遗留风险 2 的落地：`/static` 路由若不支持子目录，
    `assets/xxx.js` 会 404，页面**看起来正常但功能全废**。
    """
    dist = STATIC / "dist" / "index.html"
    if not dist.exists():
        pytest.skip("未构建（CI 不装 Node，走回退路径）")

    client = TestClient(app)
    html = client.get("/").text
    assert 'id="app"' in html

    match = re.search(r'src="(/static/dist/assets/[^"]+\.js)"', html)
    assert match, "产物页面应引用构建出的 JS"
    assert client.get(match.group(1)).status_code == 200


# ── 元素存在 ────────────────────────────────────────────────
@pytest.mark.parametrize("eid", INDEX_IDS)
def test_index_has_human_panel_element(eid):
    assert f'id="{eid}"' in _page("index.html")


@pytest.mark.parametrize("eid", GM_IDS)
def test_gm_has_phase2_element(eid):
    assert f'id="{eid}"' in _page("gm.html")


# ── 端点一致 ────────────────────────────────────────────────
@pytest.mark.parametrize("endpoint", FRONTEND_ENDPOINTS)
def test_frontend_endpoint_exists_in_backend(endpoint):
    assert endpoint in _routes(), f"后端缺端点 {endpoint}"


def test_frontend_only_calls_known_endpoints():
    """页面里出现的 /api/ 路径，必须能在后端路由里找到对应模板。"""
    routes = _routes()
    for page in ("index.html", "gm.html", "trace.html", "characters.html"):
        html = _page(page)
        for m in re.finditer(r"(/api/[A-Za-z0-9_\-/\.]*(?:\$\{[^}]+\})?/[A-Za-z0-9_\-\.]*)", html):
            raw = m.group(1)
            norm = re.sub(r"\$\{[^}]+\}", "X", raw).rstrip("/")
            hit = any(
                r == norm
                or (norm.endswith("X") and r.startswith(norm[:-1]))
                or norm.startswith(r)
                for r in routes
            )
            assert hit, f"{page} 调用了后端不存在的端点：{raw}"


# ── 字段一致 ────────────────────────────────────────────────
def test_pending_fields_match_frontend():
    """HumanSeat.pending() 提供的字段 = 前端会读的字段。"""
    import asyncio

    async def go():
        seat = HumanSeat(["chr_a"], timeout=5)
        task = asyncio.create_task(seat.wait_action("chr_a", "A", 3, ["b"], ["观察"]))
        await asyncio.sleep(0.02)
        keys = set(seat.pending().keys())
        task.cancel()
        return keys

    assert asyncio.run(go()) == PENDING_FIELDS


def test_action_request_fields_match_frontend():
    from trpg.api.app import HumanActionReq

    fields = set(HumanActionReq.model_fields)
    assert ACTION_FIELDS <= fields, "前端用到的字段后端没有"
    assert fields - ACTION_FIELDS <= OPTIONAL_ACTION_FIELDS, "后端多出来的字段必须是可选的"
    for name in OPTIONAL_ACTION_FIELDS:
        if name in fields:
            assert HumanActionReq.model_fields[name].default is None or isinstance(
                HumanActionReq.model_fields[name].default, str
            ), f"{name} 应当是可选字段"


def test_snapshot_exposes_what_panel_reads(api_client):
    """人类面板要读：角色 skills / controller，以及敌人 id / hp / hp_max。"""
    data = api_client.get("/api/sessions").json()
    assert "sessions" in data


@pytest.mark.skipif(not (ROOT / "worlds" / "ironvale").exists(), reason="ironvale 缺失")
def test_snapshot_character_fields(api_client):
    created = api_client.post(
        "/api/sessions", json={"world": "ironvale", "players": 2, "provider": "mock", "seed": "71"}
    ).json()
    char = created["characters"][0]
    for key in ("id", "name", "hp", "hp_max", "sanity", "skills", "controller", "path_label"):
        assert key in char, f"snapshot 缺 {key}"
    assert isinstance(char["skills"], dict)
    assert char["controller"] in ("llm", "human")
    assert "pending" in created


# ── 技能事件与私有信息（A2 补齐：不能再「推了没人看」）─────────

def test_ui_shows_skill_beats_and_private_panels():
    """技能发动要有出口；本人私有笔记与资源要有出口（否则神职等于瞎打）。"""
    log = (ROOT / "src" / "components" / "TurnLog.vue").read_text(encoding="utf-8")
    assert "turn.skills" in log, "回合区块要显示技能发动"
    panels = (ROOT / "src" / "components" / "InfoPanels.vue").read_text(encoding="utf-8")
    assert "notes" in panels, "面板要显示本人已确认的信息（查验结果）"
    assert "skill_charges" in panels, "面板要显示技能资源余量（药瓶）"

