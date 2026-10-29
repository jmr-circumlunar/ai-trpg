"""B42/B43/B44 一个开关四级联动。

一个开关必须在四处同时生效，否则就是假开关：
① 白名单（本文件 B42）② 工具可见性（B43）③ 前端能力下发（B44）④ 阶段装配（B45 落地后测）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents.prompts.pc import agent_protocol  # noqa: E402
from trpg.engine.state_merge import _path_allowed  # noqa: E402
from trpg.rulespack import assemble  # noqa: E402
from trpg.rulespack.assemble import tool_set  # noqa: E402


@pytest.fixture()
def client():
    from trpg.api import app

    return TestClient(app)


# ── B42 delta 白名单按能力裁剪（C50） ───────────────────────


def test_without_caps_matches_current_behaviour():
    """不给 caps 时行为与改造前完全一致（存量调用点不受影响）。"""
    assert _path_allowed("enemies")
    assert _path_allowed("characters.chr_1.inventory")
    assert _path_allowed("scene.location_id")


def test_full_caps_allow_everything_current():
    caps = assemble(None)

    assert _path_allowed("enemies", enabled=caps.enabled)
    assert _path_allowed("characters.chr_1.hp", enabled=caps.enabled)
    assert _path_allowed("characters.chr_1.inventory", enabled=caps.enabled)
    assert _path_allowed("characters.chr_1.position", enabled=caps.enabled)
    assert _path_allowed("characters.chr_1.path", enabled=caps.enabled)
    assert _path_allowed("scene.location_id", enabled=caps.enabled)


def test_disabled_combat_blocks_enemies_and_hp():
    caps = assemble({"combat": {"enabled": False}})

    assert not _path_allowed("enemies", enabled=caps.enabled)
    assert not _path_allowed("characters.chr_1.hp", enabled=caps.enabled)
    assert _path_allowed("characters.chr_1.sanity", enabled=caps.enabled), "理智不属于战斗"


def test_disabled_equipment_blocks_inventory():
    caps = assemble(
        {"equipment": {"enabled": False}, "combat": {"enabled": False}}
    )

    assert not _path_allowed("characters.chr_1.inventory", enabled=caps.enabled)
    assert _path_allowed("characters.chr_1.gold", enabled=caps.enabled)


def test_disabled_map_blocks_location_but_not_description():
    caps = assemble({"map": {"enabled": False}})

    assert not _path_allowed("scene.location_id", enabled=caps.enabled)
    assert _path_allowed("scene.description", enabled=caps.enabled)


def test_disabled_identity_and_death_block_their_fields():
    caps = assemble({"identity": {"enabled": False}, "death": {"enabled": False}})

    assert not _path_allowed("characters.chr_1.path", enabled=caps.enabled)
    assert not _path_allowed("characters.chr_1.position", enabled=caps.enabled)


def test_base_fields_always_writable():
    caps = assemble(
        {
            "combat": {"enabled": False},
            "equipment": {"enabled": False},
            "map": {"enabled": False},
            "identity": {"enabled": False},
            "death": {"enabled": False},
            "long_memory": {"enabled": False},
            "meta_growth": {"enabled": False},
            "events": {"enabled": False},
            "immersion": {"enabled": False},
        }
    )

    for path in ("party.gold", "tension", "stage", "quests", "scene.description"):
        assert _path_allowed(path, enabled=caps.enabled), path


# ── B43 工具可见性（C51） ───────────────────────────────────


def test_tool_set_with_full_caps():
    """全开时依赖都满足；`say` 另需世界开了交流（`requires_comms`）。

    这条断言以前是 `tool_set(caps) == {..., "say"}`——那正是漏洞：
    `tool_set` 不知道 comms 规则，在没有交流的世界里也返回 `say`，
    与 prompt 里展示的工具清单不一致。现在默认按「没有交流」处理。
    """
    caps = assemble(None)

    assert tool_set(caps, "one_way") == {"recall", "look", "check_self", "say"}
    assert "say" not in tool_set(caps), "不传 comms 时保守地不给 say"
    assert "say" not in tool_set(caps, "none")


def test_recall_hidden_when_long_memory_disabled():
    caps = assemble({"long_memory": {"enabled": False}})

    assert "recall" not in tool_set(caps)
    assert "look" in tool_set(caps)


def test_agent_protocol_lists_only_visible_tools():
    text = agent_protocol(("look",))

    assert "look" in text
    assert "recall" not in text


def test_agent_protocol_defaults_to_all_tools():
    text = agent_protocol()

    assert "recall" in text
    assert "look" in text
    assert "check_self" in text


# ── B44 capabilities 下发 + 前端区块（C52） ─────────────────


def test_static_blocks_js_is_served(client):
    response = client.get("/static/blocks.js")

    assert response.status_code == 200
    assert "applyCapabilities" in response.text


def test_index_page_references_blocks_js():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "/static/blocks.js" in html, "页面必须真的引用 blocks.js，否则能力过滤是死代码"


def test_config_endpoint_is_registered():
    from trpg.api import app

    paths = app.openapi()["paths"]

    assert any(path.endswith("/config") for path in paths), (
        "缺少 /api/sessions/{sid}/config：前端拿不到能力清单"
    )
