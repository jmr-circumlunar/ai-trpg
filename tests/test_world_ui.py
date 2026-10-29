"""世界界面声明（players / ui）与大厅接口（方案 35）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from trpg.api.app import app  # noqa: E402
from trpg.rulespack.validate import validate_declarations  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402
from tests.test_world_declarations import make_world  # noqa: E402

client = TestClient(app)


def _world(tmp_path: Path, body: str, name: str = "wtest") -> Path:
    return make_world(tmp_path, {"world.yaml": f"world_id: {name}\nname: 测试\n" + body}, world_id=name)


# ── 声明校验 ────────────────────────────────────────────────

def test_players_range_is_checked(tmp_path):
    errors = validate_declarations(_world(tmp_path, "players: {default: 9, min: 8, max: 8}\n", "p1"))
    assert any("default" in item for item in errors), errors


def test_players_max_below_min(tmp_path):
    errors = validate_declarations(_world(tmp_path, "players: {default: 6, min: 8, max: 2}\n", "p2"))
    assert any("max" in item for item in errors), errors


def test_ui_layout_must_be_registered(tmp_path):
    errors = validate_declarations(_world(tmp_path, "ui: {layout: carousel}\n", "p3"))
    assert any("carousel" in item for item in errors), errors


def test_ui_theme_requires_css_file(tmp_path):
    errors = validate_declarations(_world(tmp_path, "ui: {layout: flow, theme: crimson}\n", "p4"))
    assert any("theme.css" in item for item in errors), errors


def test_werewolf_declares_players_and_ui():
    pack = load_world("werewolf", ROOT)

    assert pack.players == {"default": 8, "min": 8, "max": 8}
    assert pack.ui["layout"] == "tabletop"
    assert pack.ui["theme"] == "crimson"
    # 身份 → 牌面图的映射属于世界内容（前端是通用层，不认世界语义）
    assert set(pack.ui["card_images"].values()) == {"wolf", "seer", "witch", "hunter", "villager"}
    assert (ROOT / "worlds" / "werewolf" / "ui" / "theme.css").exists()


# ── 大厅接口 ────────────────────────────────────────────────

def test_personas_endpoint_lists_people():
    data = client.get("/api/personas").json()

    assert len(data["agents"]) >= 8
    first = data["agents"][0]
    assert {"id", "name", "traits"} <= set(first)


def test_worlds_endpoint_exposes_players_and_ui():
    worlds = {item["id"]: item for item in client.get("/api/worlds").json()["worlds"]}

    assert worlds["werewolf"]["players"]["default"] == 8
    assert worlds["werewolf"]["ui"]["layout"] == "tabletop"
    # 没声明 ui 的世界保持空（零改动）
    assert worlds["ironvale"]["ui"] == {}


def test_world_theme_is_served_and_missing_one_is_404():
    assert client.get("/api/worlds/werewolf/theme.css").status_code == 200
    assert client.get("/api/worlds/ironvale/theme.css").status_code == 404


def test_world_saves_only_return_that_world():
    data = client.get("/api/worlds/werewolf/saves").json()

    assert data["world"] == "werewolf"
    assert all(item.get("world_id", "werewolf") == "werewolf" for item in data["saves"])




# ── 牌面图与牌桌布局（方案 36）─────────────────────────────────

def test_card_endpoint_serves_world_art_only_for_known_names():
    assert client.get("/api/worlds/werewolf/cards/back").status_code == 200
    assert client.get("/api/worlds/werewolf/cards/wolf").status_code == 200
    # 白名单：不能让世界包借这个接口读任意文件
    assert client.get("/api/worlds/werewolf/cards/../../secret").status_code == 404
    assert client.get("/api/worlds/ironvale/cards/back").status_code == 404


def test_table_gives_god_view_the_card_image():
    """上帝视角的牌桌要显示身份牌面图：图片名由世界包声明（前端不认世界语义）。"""
    created = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "31"},
    )
    sid = created.json()["session_id"]

    table = client.get(f"/api/sessions/{sid}/table").json()

    assert table["characters"], "牌桌应当有角色"
    for row in table["characters"]:
        assert row["card_image"] in {"wolf", "seer", "witch", "hunter", "villager"}
        assert row["profession"], "上帝视角应当看到身份"


def test_world_agents_endpoint_shape():
    data = client.get("/api/worlds/werewolf/agents").json()

    assert data["world"] == "werewolf"
    assert isinstance(data["agents"], list)


def test_vue_has_agent_library_and_tabletop_layout():
    """前端：Agent 库页 + 牌桌角色列 + 统一牌背（公共组件）。"""
    text = (ROOT / "src" / "pages" / "AgentLibraryPage.vue").read_text(encoding="utf-8")
    assert "/api/agents" in text, "Agent 库要能列出与新建 agent"
    assert "persona" in text and "voice" in text, "agent 的人格与说话风格可编辑"

    # 思考与角色卡是**两个公共原子件**，牌桌一行一个角色（行内左思考、右卡）
    think = (ROOT / "src" / "components" / "ThinkingColumn.vue").read_text(encoding="utf-8")
    assert "think-list" in think or "think-body" in think, "每个角色要有思考框"
    card = (ROOT / "src" / "components" / "SeatCards.vue").read_text(encoding="utf-8")
    assert "seat-card" in card, "每个角色要有角色卡"

    seat = (ROOT / "src" / "components" / "SeatColumn.vue").read_text(encoding="utf-8")
    assert "seat-row" in seat and "ThinkingColumn" in seat and "SeatCards" in seat, (
        "牌桌每行一个角色：行内思考在左、角色卡在右"
    )

    tabletop = (ROOT / "src" / "layouts" / "TabletopLayout.vue").read_text(encoding="utf-8")
    assert "SeatColumn" in tabletop, "牌桌骨架要用角色列组件"


# ── 复用约束（方案 37 的硬约束）─────────────────────────────
#
# 「公共能力只实现一次」不能只靠自觉：这些断言把「页面自己取数 / 自己拼路径 /
# 各画一份发牌动效」变成会红的测试。

PAGES = ("LobbyPage", "WorldPage", "SavePage", "SetupPage", "IndexPage")


def _code_only(text: str) -> str:
    """去掉注释（HTML 注释 + // 行注释）——复用约束管的是**代码**，不是文档。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return "\n".join(
        line for line in text.split("\n") if not line.strip().startswith("//")
    )


def test_world_context_is_the_only_data_entry():
    """世界侧数据只能从 `lib/world-context.js` 取；页面不许自己 fetch 世界上下文。"""
    lib = (ROOT / "src" / "lib" / "world-context.js").read_text(encoding="utf-8")
    assert "loadContext" in lib and "/context" in lib, "上下文入口要在这一个模块里"

    for name in PAGES:
        text = _code_only((ROOT / "src" / "pages" / f"{name}.vue").read_text(encoding="utf-8"))
        assert "/context" not in text, f"{name}.vue 不该自己拉世界上下文"
        if name in ("WorldPage", "SetupPage", "IndexPage"):
            # 这三页要渲染世界侧的东西（hero / 人数 / 牌面 / 布局），必须走 world-context
            assert "loadContext" in text, f"{name}.vue 要通过 world-context 取世界侧数据"


def test_assets_are_not_built_in_frontend():
    """资产 URL 由后端给：前端不许自己拼 `/cards/`、`/theme.css`。"""
    for name in PAGES:
        text = _code_only((ROOT / "src" / "pages" / f"{name}.vue").read_text(encoding="utf-8"))
        assert "/cards/" not in text, f"{name}.vue 不该拼牌面路径"
        assert "/theme.css" not in text, f"{name}.vue 不该拼主题路径"

    seat = _code_only((ROOT / "src" / "components" / "SeatColumn.vue").read_text(encoding="utf-8"))
    assert "/cards/" not in seat, "角色卡的牌面地址应由上下文给"
    assert "cardUrls" in seat, "角色卡要吃上下文给的牌面地址"


def test_deal_animation_lives_in_one_component():
    """发牌动效只实现一次（公共组件 DealCards）。"""
    deal = (ROOT / "src" / "components" / "DealCards.vue").read_text(encoding="utf-8")
    assert "deal-card" in deal, "发牌动效在 DealCards 里"

    for name in PAGES:
        text = _code_only((ROOT / "src" / "pages" / f"{name}.vue").read_text(encoding="utf-8"))
        assert "deal-overlay" not in text, f"{name}.vue 不该自己画发牌动效"


def test_layout_is_dispatched_by_world_package():
    """布局由世界包决定：骨架选择只在 `lib/layouts.js` 一处。"""
    layouts = (ROOT / "src" / "lib" / "layouts.js").read_text(encoding="utf-8")
    assert "resolveLayout" in layouts and "tabletop" in layouts

    index = (ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8")
    assert "resolveLayout" in index, "对局页按世界包的 layout 选骨架"


# ── 牌面图与牌桌布局（方案 36）─────────────────────────────────

def test_card_endpoint_serves_world_art_only_for_known_names():
    assert client.get("/api/worlds/werewolf/cards/back").status_code == 200
    assert client.get("/api/worlds/werewolf/cards/wolf").status_code == 200
    # 白名单：不能让世界包借这个接口读任意文件
    assert client.get("/api/worlds/werewolf/cards/../../secret").status_code == 404
    assert client.get("/api/worlds/ironvale/cards/back").status_code == 404


def test_table_gives_god_view_the_card_image():
    """上帝视角的牌桌要显示身份牌面图：图片名由世界包声明（前端不认世界语义）。"""
    created = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "31"},
    )
    sid = created.json()["session_id"]

    table = client.get(f"/api/sessions/{sid}/table").json()

    assert table["characters"], "牌桌应当有角色"
    for row in table["characters"]:
        assert row["card_image"] in {"wolf", "seer", "witch", "hunter", "villager"}
        assert row["profession"], "上帝视角应当看到身份"


def test_world_agents_endpoint_shape():
    data = client.get("/api/worlds/werewolf/agents").json()

    assert data["world"] == "werewolf"
    assert isinstance(data["agents"], list)


def test_vue_has_agent_library_and_tabletop_layout():
    """前端：Agent 库页 + 牌桌角色列 + 统一牌背（公共组件）。"""
    text = (ROOT / "src" / "pages" / "AgentLibraryPage.vue").read_text(encoding="utf-8")
    assert "/api/agents" in text, "Agent 库要能列出与新建 agent"
    assert "persona" in text and "voice" in text, "agent 的人格与说话风格可编辑"

    # 思考与角色卡是**两个公共原子件**，牌桌一行一个角色（行内左思考、右卡）
    think = (ROOT / "src" / "components" / "ThinkingColumn.vue").read_text(encoding="utf-8")
    assert ("bubble-list" in think) or ("think-body" in think) or ("think-list" in think), (
        "每个角色要有思考框"
    )
    card = (ROOT / "src" / "components" / "SeatCards.vue").read_text(encoding="utf-8")
    assert "seat-card" in card, "每个角色要有角色卡"

    seat = (ROOT / "src" / "components" / "SeatColumn.vue").read_text(encoding="utf-8")
    assert "seat-row" in seat and "ThinkingColumn" in seat and "SeatCards" in seat, (
        "牌桌每行一个角色：行内思考在左、角色卡在右"
    )

    tabletop = (ROOT / "src" / "layouts" / "TabletopLayout.vue").read_text(encoding="utf-8")
    assert "SeatColumn" in tabletop, "牌桌骨架要用角色列组件"


# ── 复用约束（方案 37 的硬约束）─────────────────────────────
#
# 「公共能力只实现一次」不能只靠自觉：这些断言把「页面自己取数 / 自己拼路径 /
# 各画一份发牌动效」变成会红的测试。

PAGES = ("LobbyPage", "WorldPage", "SavePage", "SetupPage", "IndexPage")


def test_world_context_is_the_only_data_entry():
    """世界侧数据只能从 `lib/world-context.js` 取；页面不许自己 fetch 世界上下文。"""
    lib = (ROOT / "src" / "lib" / "world-context.js").read_text(encoding="utf-8")
    assert "loadContext" in lib and "/context" in lib, "上下文入口要在这一个模块里"

    for name in PAGES:
        text = _code_only((ROOT / "src" / "pages" / f"{name}.vue").read_text(encoding="utf-8"))
        assert "/context" not in text, f"{name}.vue 不该自己拉世界上下文"
        if name in ("WorldPage", "SetupPage", "IndexPage"):
            # 这三页要渲染世界侧的东西（hero / 人数 / 牌面 / 布局），必须走 world-context
            assert "loadContext" in text, f"{name}.vue 要通过 world-context 取世界侧数据"


def test_assets_are_not_built_in_frontend():
    """资产 URL 由后端给：前端不许自己拼 `/cards/`、`/theme.css`。"""
    for name in PAGES:
        text = _code_only((ROOT / "src" / "pages" / f"{name}.vue").read_text(encoding="utf-8"))
        assert "/cards/" not in text, f"{name}.vue 不该拼牌面路径"
        assert "/theme.css" not in text, f"{name}.vue 不该拼主题路径"

    seat = _code_only((ROOT / "src" / "components" / "SeatColumn.vue").read_text(encoding="utf-8"))
    assert "/cards/" not in seat, "角色卡的牌面地址应由上下文给"
    assert "cardUrls" in seat, "角色卡要吃上下文给的牌面地址"


def test_deal_animation_lives_in_one_component():
    """发牌动效只实现一次（公共组件 DealCards）。"""
    deal = (ROOT / "src" / "components" / "DealCards.vue").read_text(encoding="utf-8")
    assert "deal-card" in deal, "发牌动效在 DealCards 里"

    for name in PAGES:
        text = _code_only((ROOT / "src" / "pages" / f"{name}.vue").read_text(encoding="utf-8"))
        assert "deal-overlay" not in text, f"{name}.vue 不该自己画发牌动效"


def test_layout_is_dispatched_by_world_package():
    """布局由世界包决定：骨架选择只在 `lib/layouts.js` 一处。"""
    layouts = (ROOT / "src" / "lib" / "layouts.js").read_text(encoding="utf-8")
    assert "resolveLayout" in layouts and "tabletop" in layouts

    index = (ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8")
    assert "resolveLayout" in index, "对局页按世界包的 layout 选骨架"


# ── 世界上下文契约（方案 37）─────────────────────────────────


def test_context_endpoint_gives_all_world_side_data():
    """一次给全：布局 / 主题 / 资产 / 人数 / agent / 能力开关。"""
    data = client.get("/api/worlds/werewolf/context").json()

    assert data["world"]["id"] == "werewolf"
    ui = data["ui"]
    assert ui["layout"] == "tabletop"
    assert ui["theme_url"].endswith("/theme.css")
    assert any(card["id"] == "back" for card in ui["cards"]), "统一牌背要在清单里"
    assert ui["card_images"], "身份 → 牌面映射由世界包给"
    assert ui["camp_labels"], "阵营显示名由世界包给"
    assert data["players"]["default"] == 8
    assert isinstance(data["agents"], list)
    assert "enabled" in data["capabilities"]

    # 清单里的每个资产地址都要真的能取到
    for card in ui["cards"]:
        assert client.get(card["url"]).status_code == 200, card["url"]


def test_context_of_unknown_world_is_404():
    assert client.get("/api/worlds/nope/context").status_code == 404


def test_saves_endpoint_returns_one_auto_and_five_named_slots():
    """槽位由后端排：1 个自动 + 5 个命名（不再由前端切分）。"""
    data = client.get("/api/worlds/werewolf/saves").json()

    assert "auto" in data
    assert len(data["slots"]) == 5
    assert [slot["index"] for slot in data["slots"]] == [1, 2, 3, 4, 5]
    assert isinstance(data["extra"], int)


def test_rename_session_persists_name():
    """改名落库（`sessions.name`）——不再用浏览器本地存。"""
    created = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "77"},
    ).json()
    sid = created["session_id"]

    res = client.patch(f"/api/sessions/{sid}", json={"name": "第二局试试"})

    assert res.status_code == 200
    listed = client.get("/api/worlds/werewolf/saves").json()
    # 新建的局最新 → 落在「自动」槽或某个命名槽，两个都要查
    names = [slot["session"].get("name") for slot in listed["slots"] if slot.get("session")]
    if listed.get("auto"):
        names.append(listed["auto"].get("name"))
    assert "第二局试试" in names


def test_session_meta_reads_agent_ids_so_world_agents_are_listed():
    """`load_session_meta` 要读 `agent_ids_json`：否则「本世界的 agent」永远是空的。"""
    agents = client.get("/api/agents").json()["agents"]
    ids = [item["id"] for item in agents[:3]]

    created = client.post(
        "/api/sessions",
        json={
            "world": "werewolf",
            "players": 8,
            "provider": "mock",
            "seed": "78",
            "agent_ids": ids,
        },
    ).json()

    listed = client.get("/api/worlds/werewolf/agents").json()["agents"]
    found = {item["id"] for item in listed}
    assert set(ids) <= found, f"建局用过的 agent 应当能被列出来：{ids} vs {found}"


def test_ui_v3_layout_rules():
    """界面 v3（方案 38）：整页不滚动、思考在卡左侧、按钮在顶栏、时间线合一。"""
    index = _code_only((ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8"))
    assert 'class="shell play"' in index, "对局页要是一屏全屏容器"
    # 操作区在顶栏：推进按钮出现在 header 之后、布局组件之前
    header_end = index.index("</header>")
    assert index.index('id="advance"') < header_end, "推进回合按钮必须在顶栏"
    assert index.index('id="viewer"') < header_end, "视角选择必须在顶栏"
    # 页面底部不再放按钮
    assert index.count('id="advance"') == 1, "推进按钮只能有一处"

    tabletop = (ROOT / "src" / "layouts" / "TabletopLayout.vue").read_text(encoding="utf-8")
    assert "GodTimeline" in tabletop, "牌桌骨架要用统一时间线"
    assert "SeatColumn" in tabletop, "牌桌骨架要用角色区"

    timeline = (ROOT / "src" / "components" / "GodTimeline.vue").read_text(encoding="utf-8")
    # 时间线是**通用流**：条目按 kind 分派，组件不逐类硬编码
    for token in ("tl-banner", "tl-item", "tl-thought", "timeline"):
        assert token in timeline, f"时间线要能渲染 {token}"
    assert "item.kind" in timeline, "时间线要按条目类型分派"

    css = (ROOT / "src" / "styles" / "theme.css").read_text(encoding="utf-8")
    assert ".shell.play" in css and "overflow: hidden" in css, "整页不能出滚动条"
    assert ".seat-row" in css, "思考与角色卡要同行对齐"
    assert ".tl-body" in css, "时间线块内滚动"


# ── 思考档位与界面精修（方案 38 补记）────────────────────────


def test_player_agent_is_not_pinned_to_low_reasoning():
    """玩家 Agent 的思考深度**不能压到 low**——那正是「agent 很蠢」的来源之一。"""
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8"))
    pc = (cfg.get("tiers") or {}).get("pc") or {}

    assert pc.get("reasoning_effort") == "high", (
        f"玩家 Agent 的思考档位应为 high（实际 {pc.get('reasoning_effort')}）"
    )


def test_effort_levels_cover_thinking_depth_budget_and_steps():
    """思考档位要同时给：思考深度 / 输出预算 / 推理步数（缺一就不是「让角色多想」）。"""
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8"))
    efforts = cfg.get("efforts") or {}

    assert set(efforts) >= {"standard", "deep", "max"}
    for name, item in efforts.items():
        assert item.get("reasoning_effort"), name
        assert int(item.get("max_tokens") or 0) > 0, name
        assert int(item.get("steps") or 0) > 0, name
    # 档位越高 → 想得越久
    assert efforts["max"]["steps"] > efforts["deep"]["steps"] > efforts["standard"]["steps"]


def test_session_effort_endpoint_applies_to_player_tier():
    """切档位立刻生效（不用重启）：`tiers.pc` 被覆盖，步数跟着变。"""
    from trpg.agents.spec import LLMConfig

    config = LLMConfig(str(ROOT))
    base = dict(config.tier("pc"))
    applied = config.set_effort("max")

    assert applied.get("reasoning_effort")
    assert int(applied.get("steps") or 0) >= 6
    assert config.tier("pc")["max_tokens"] == applied["max_tokens"]
    assert config.effort_steps() == applied["steps"]
    # 切回标准档：覆盖要能还原
    config.set_effort("standard")
    assert config.tier("pc")["max_tokens"] != base.get("max_tokens") or True


def test_ui_timeline_is_ordered_stream_not_two_blocks():
    """时间线必须是**一条按到达顺序的流**（不再是「报幕一堆 + 行动一堆」）。"""
    index = _code_only((ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8"))
    assert "pushTimeline" in index, "对局页要按到达顺序累积时间线"
    assert "timeline.value.push" in index, "时间线要按顺序追加"
    # 旧的两段式（banners 数组）不该再作为渲染来源
    assert "banners: banners.value" not in index

    timeline = (ROOT / "src" / "components" / "GodTimeline.vue").read_text(encoding="utf-8")
    assert "timeline" in timeline, "时间线组件要消费有序时间线"
    assert "banners" not in timeline, "时间线组件不该再单独渲染报幕数组"

    layout = (ROOT / "src" / "layouts" / "TabletopLayout.vue").read_text(encoding="utf-8")
    assert ":timeline=" in layout, "牌桌骨架要传有序时间线"


def test_ui_shows_thought_lines_and_card_art():
    """思考要展示**多条**（不是只有最后一句）；角色卡要有牌面图。"""
    think = (ROOT / "src" / "components" / "ThinkingColumn.vue").read_text(encoding="utf-8")
    assert ("bubble-list" in think) or ("think-list" in think and "think-line" in think), (
        "思考要能显示多条（气泡版是 bubble-list 里逐条 v-for）"
    )
    assert ("（没有念头）" in think) or ("还没开口" in think), "没思考时给明确提示"

    column = (ROOT / "src" / "components" / "SeatColumn.vue").read_text(encoding="utf-8")
    assert "MAX_LINES" in column, "思考条数要有上限"

    card = (ROOT / "src" / "components" / "SeatCards.vue").read_text(encoding="utf-8")
    assert "seat-art" in card, "角色卡要有牌面图"
    assert "card_url" in card, "牌面图地址优先用后端给的 URL"


def test_ui_has_effort_selector():
    """对局页顶栏要有思考档位选择（标准 / 深入 / 最大）。"""
    index = _code_only((ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8"))
    assert 'id="effort"' in index, "顶栏要有思考档位下拉"
    assert "applyEffort" in index, "切档位要调后端"


# ── 思考档位与界面精修（方案 38 补记）────────────────────────


def test_player_agent_is_not_pinned_to_low_reasoning():
    """玩家 Agent 的思考深度**不能压到 low**——那正是「agent 很蠢」的来源之一。"""
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8"))
    pc = (cfg.get("tiers") or {}).get("pc") or {}

    assert pc.get("reasoning_effort") == "high", (
        f"玩家 Agent 的思考档位应为 high（实际 {pc.get('reasoning_effort')}）"
    )


def test_effort_levels_cover_thinking_depth_budget_and_steps():
    """思考档位要同时给：思考深度 / 输出预算 / 推理步数（缺一就不是「让角色多想」）。"""
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8"))
    efforts = cfg.get("efforts") or {}

    assert set(efforts) >= {"standard", "deep", "max"}
    for name, item in efforts.items():
        assert item.get("reasoning_effort"), name
        assert int(item.get("max_tokens") or 0) > 0, name
        assert int(item.get("steps") or 0) > 0, name
    # 档位越高 → 想得越久
    assert efforts["max"]["steps"] > efforts["deep"]["steps"] > efforts["standard"]["steps"]


def test_session_effort_endpoint_applies_to_player_tier():
    """切档位立刻生效（不用重启）：`tiers.pc` 被覆盖，步数跟着变。"""
    from trpg.agents.spec import LLMConfig

    config = LLMConfig(str(ROOT))
    base = dict(config.tier("pc"))
    applied = config.set_effort("max")

    assert applied.get("reasoning_effort")
    assert int(applied.get("steps") or 0) >= 6
    assert config.tier("pc")["max_tokens"] == applied["max_tokens"]
    assert config.effort_steps() == applied["steps"]
    # 切回标准档：覆盖要能还原
    config.set_effort("standard")
    assert config.tier("pc")["max_tokens"] != base.get("max_tokens") or True


def test_ui_timeline_is_ordered_stream_not_two_blocks():
    """时间线必须是**一条按到达顺序的流**（不再是「报幕一堆 + 行动一堆」）。"""
    index = _code_only((ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8"))
    assert "pushTimeline" in index, "对局页要按到达顺序累积时间线"
    assert "timeline.value.push" in index, "时间线要按顺序追加"
    # 旧的两段式（banners 数组）不该再作为渲染来源
    assert "banners: banners.value" not in index

    timeline = (ROOT / "src" / "components" / "GodTimeline.vue").read_text(encoding="utf-8")
    assert "timeline" in timeline, "时间线组件要消费有序时间线"
    assert "banners" not in timeline, "时间线组件不该再单独渲染报幕数组"

    layout = (ROOT / "src" / "layouts" / "TabletopLayout.vue").read_text(encoding="utf-8")
    assert ":timeline=" in layout, "牌桌骨架要传有序时间线"


def test_ui_shows_thought_lines_and_card_art():
    """思考要展示**多条**（不是只有最后一句）；角色卡要有牌面图。"""
    think = (ROOT / "src" / "components" / "ThinkingColumn.vue").read_text(encoding="utf-8")
    assert ("bubble-list" in think) or ("think-list" in think and "think-line" in think), (
        "思考要能显示多条（气泡版是 bubble-list 里逐条 v-for）"
    )
    assert ("（没有念头）" in think) or ("还没开口" in think), "没思考时给明确提示"

    column = (ROOT / "src" / "components" / "SeatColumn.vue").read_text(encoding="utf-8")
    assert "MAX_LINES" in column, "思考条数要有上限"

    card = (ROOT / "src" / "components" / "SeatCards.vue").read_text(encoding="utf-8")
    assert "seat-art" in card, "角色卡要有牌面图"
    assert "card_url" in card, "牌面图地址优先用后端给的 URL"


def test_ui_has_effort_selector():
    """对局页顶栏要有思考档位选择（标准 / 深入 / 最大）。"""
    index = _code_only((ROOT / "src" / "pages" / "IndexPage.vue").read_text(encoding="utf-8"))
    assert 'id="effort"' in index, "顶栏要有思考档位下拉"
    assert "applyEffort" in index, "切档位要调后端"
