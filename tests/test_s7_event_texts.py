"""S7-1：事件文案表（角色 × 事件 + 变量占位 + 情境提醒）。

三条硬要求逐条钉住：
1. **缺省为空**：没有声明文案时什么都不注入（现有对局逐字节不变）；
2. **前后端同源**：后端注入与前端展示读的是**同一张表**（端点直接给这张表）；
3. **文案是内容不是框架常量**：文案在 yaml 里，代码不认识任何具体措辞。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trpg.agents.prompts.events import (
    event_hints,
    load_event_texts,
    render_text,
    text_for,
)

ROOT = Path(__file__).resolve().parents[1]


# ── 读取 ────────────────────────────────────────────────────
def test_rules_layer_fallback_exists():
    """框架层有兜底文案表（谁都能用）。"""
    texts = load_event_texts(ROOT, "werewolf")
    assert texts, "prompts/events.yaml 必须有兜底文案"
    for item in texts:
        assert item.get("id")
        assert item.get("roles"), "文案表要按角色给文案"


def test_unknown_world_gets_fallback_or_empty():
    """世界包没声明 → 走框架兜底；都没有 → 空表（不报错）。"""
    texts = load_event_texts(ROOT, "no-such-world")
    assert isinstance(texts, list)


def test_missing_root_returns_empty(tmp_path):
    assert load_event_texts(tmp_path, "any") == [], "没有任何表时必须是空表"


# ── 角色 × 事件 ─────────────────────────────────────────────
def test_text_per_role():
    texts = load_event_texts(ROOT, "werewolf")
    event_id = texts[0]["id"]
    player = text_for(texts, event_id, "player")
    npc = text_for(texts, event_id, "npc")
    assert player and npc
    assert player != npc, "不同角色应有不同措辞"


def test_unknown_role_or_event_gives_empty_string():
    texts = load_event_texts(ROOT, "werewolf")
    assert text_for(texts, texts[0]["id"], "no-such-role") == ""
    assert text_for(texts, "no-such-event", "player") == ""


# ── 变量占位 ────────────────────────────────────────────────
def test_render_fills_variables():
    assert render_text("你看到 {detail}。", {"detail": "一扇门"}) == "你看到 一扇门。"


def test_render_missing_variable_is_empty_not_error():
    assert render_text("你看到 {detail}。", {}) == "你看到 。"


def test_render_without_variables_returns_template_intact():
    assert render_text("没有变量") == "没有变量"


# ── 情境提醒 ────────────────────────────────────────────────
def test_hints_collected():
    texts = load_event_texts(ROOT, "werewolf")
    hints = event_hints(texts, [texts[0]["id"]])
    assert hints.strip(), "声明了 hints 就该取得到"


def test_hints_empty_when_nothing_declared():
    assert event_hints([], ["x"]) == "", "没有提醒时必须是空串（不注入空段落）"


def test_hints_filtered_by_event_id():
    texts = load_event_texts(ROOT, "werewolf")
    only = event_hints(texts, [texts[0]["id"]])
    every = event_hints(texts)
    assert only != every or len(texts) == 1


# ── 前后端同源（端点）──────────────────────────────────────
def test_endpoint_returns_same_table(client=None):
    from fastapi.testclient import TestClient

    from trpg.api.app import app

    with TestClient(app) as c:
        data = c.get("/api/worlds/werewolf/event-texts").json()
    assert "events" in data
    texts = load_event_texts(ROOT, "werewolf")
    assert len(data["events"]) == len(texts), "端点给的必须是同一张表"
    if texts:
        first = data["events"][0]
        assert first["id"] == texts[0]["id"]
        assert set(first["roles"]) == set(texts[0]["roles"])


def test_prompt_text_is_not_hardcoded_in_code():
    """文案是**内容**：框架代码里不该出现具体措辞。"""
    src = (ROOT / "trpg" / "agents" / "prompts" / "events.py").read_text(encoding="utf-8")
    for phrase in ("注意到", "动静", "瞥了一眼"):
        assert phrase not in src, f"具体措辞不该写进代码：{phrase}"


# ── GM prompt 注入（缺省零变化）────────────────────────────
def _state():
    from trpg.models.state import GameState, Scene, WorldRef

    return GameState(
        session_id="ses_x",
        world=WorldRef(world_id="w", era="e"),
        scene=Scene(location_id="loc_1", region="r1"),
    )


def test_gm_prompt_has_no_hints_block_by_default():
    """缺省不注入 → 现有对局 prompt 逐字节不变。"""
    from trpg.agents.prompts.gm import gm_scene_user

    text = gm_scene_user(_state(), {}, 1)
    assert "情境提醒" not in text, "没有提醒时不该出现该段"


def test_gm_prompt_injects_hints_when_given():
    from trpg.agents.prompts.gm import gm_scene_user

    text = gm_scene_user(_state(), {}, 1, event_hints="- 只描述已成立的事实")
    assert "情境提醒" in text
    assert "只描述已成立的事实" in text
