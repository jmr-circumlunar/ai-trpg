"""S4-2：玩家视图的**单一服务端出口**（泄露扫描测试先行）。

三件事钉住：

1. **单一出口成立**：快照 / SSE / trace 都过 `filter_for_viewer()`；
2. **泄露扫描**：任何 viewer 都收不到越界字段（别人的身份 id、别人的私有笔记、
   别人的思维过程）；
3. **增量同步**：JSON Patch 只发差分（未过滤字段不会出现在 patch 里）。

规则来源仍是世界包单源（`secrets.profession` / `visibility.thoughts`）。
"""

from __future__ import annotations

import pytest

from trpg.api.views import filter_for_viewer, json_patch


# ── 1. 状态裁剪 ────────────────────────────────────────────

def _payload() -> dict:
    return {
        "session_id": "ses_x",
        "characters": [
            {
                "id": "chr_a",
                "name": "甲",
                "profession": "预言家",
                "path": "seer",
                "path_label": "预言家",
                "notes": ["我验了乙是好人"],
                "hp": 10,
            },
            {
                "id": "chr_b",
                "name": "乙",
                "profession": "猎人",
                "path": "hunter",
                "path_label": "猎人",
                "notes": ["乙很安静"],
                "hp": 8,
            },
        ],
    }


def test_observer_view_is_not_filtered():
    data = _payload()
    out = filter_for_viewer(None, data, secrets={"profession": "self"})
    assert out["characters"][1]["profession"] == "猎人"
    assert out["characters"][1]["notes"] == ["乙很安静"]


def test_others_identity_is_stripped_including_raw_path_id():
    """别人的身份字段要抹掉——**包括原始 id**（`path` 直接等于角色名）。"""
    out = filter_for_viewer(
        "chr_a", _payload(), secrets={"profession": "self"}
    )
    other = out["characters"][1]
    assert other["profession"] == ""
    assert other["path"] == "", "原始 path id 泄露 = 直接暴露角色名"
    assert other["path_label"] == ""


def test_self_identity_is_kept():
    out = filter_for_viewer(
        "chr_a", _payload(), secrets={"profession": "self"}
    )
    me = out["characters"][0]
    assert me["profession"] == "预言家"
    assert me["path"] == "seer"
    assert me["notes"] == ["我验了乙是好人"]


def test_others_private_notes_are_removed():
    out = filter_for_viewer(
        "chr_a", _payload(), secrets={"profession": "self"}
    )
    assert out["characters"][1]["notes"] == []


def test_public_fields_are_untouched():
    out = filter_for_viewer(
        "chr_a", _payload(), secrets={"profession": "self"}
    )
    other = out["characters"][1]
    assert other["name"] == "乙"
    assert other["hp"] == 8


def test_world_without_hidden_profession_keeps_everything():
    """`secrets.profession` 不是 `self` → 不裁身份（存量世界行为不变）。"""
    out = filter_for_viewer("chr_a", _payload(), secrets={"profession": "all"})
    assert out["characters"][1]["profession"] == "猎人"
    assert out["characters"][1]["path"] == "hunter"


def test_turn_payload_actors_are_filtered_too():
    """回合结果（`actors`）也要裁身份——否则职业又从回合结果漏出去。"""
    payload = {
        "turn": 3,
        "actors": [
            {"id": "chr_a", "name": "甲", "profession": "预言家"},
            {"id": "chr_b", "name": "乙", "profession": "猎人"},
        ],
    }
    out = filter_for_viewer("chr_a", payload, secrets={"profession": "self"})
    assert out["actors"][0]["profession"] == "预言家"
    assert out["actors"][1]["profession"] == ""
    assert out["turn"] == 3, "非角色字段不动"


# ── 2. 日志裁剪 ────────────────────────────────────────────

def test_log_thought_is_removed_when_not_visible():
    event = {"id": "chr_b", "kind": "actor", "text": "我投了甲", "thought": "甲肯定有问题"}
    out = filter_for_viewer(
        "chr_a", event, kind="log", thoughts_visible=lambda cid: cid == "chr_a"
    )
    assert "thought" not in out
    assert out["text"] == "我投了甲", "事件本身仍然公开"


def test_log_thought_is_kept_for_visible_viewer():
    event = {"id": "chr_a", "kind": "actor", "text": "我说", "thought": "我怀疑乙"}
    out = filter_for_viewer(
        "chr_a", event, kind="log", thoughts_visible=lambda cid: cid == "chr_a"
    )
    assert out["thought"] == "我怀疑乙"


def test_log_without_thought_is_untouched():
    event = {"id": "chr_b", "kind": "actor", "text": "我投了甲"}
    out = filter_for_viewer(
        "chr_a", event, kind="log", thoughts_visible=lambda cid: False
    )
    assert out == event


# ── 3. JSON Patch 差分 ─────────────────────────────────────

def test_json_patch_reports_only_changes():
    prev = {"a": 1, "b": {"c": 2}, "gone": True}
    cur = {"a": 1, "b": {"c": 3}, "added": "x"}
    patch = json_patch(prev, cur)
    ops = {(p["op"], p["path"]) for p in patch}
    assert ("replace", "/b/c") in ops
    assert ("add", "/added") in ops
    assert ("remove", "/gone") in ops
    assert ("replace", "/a") not in ops, "没变的不该出现在 patch 里"


def test_json_patch_never_carries_filtered_fields():
    """关键性质：**别人**被裁掉的字段根本不会出现在 patch 里。

    自己的字段（自己的身份）当然可以出现——那是本人有权知道的信息。
    """
    base = _payload()
    prev = filter_for_viewer("chr_a", base, secrets={"profession": "self"})
    changed = {
        **base,
        "characters": [
            {**base["characters"][0], "hp": 7},
            base["characters"][1],
        ],
    }
    cur = filter_for_viewer("chr_a", changed, secrets={"profession": "self"})
    patch = json_patch(prev, cur)
    text = str(patch)
    assert "hunter" not in text, "别人的身份 id 不该出现在增量里"
    assert "乙很安静" not in text, "别人的私有笔记不该出现在增量里"
    assert "/characters" in text and "hp" in text


def test_json_patch_is_empty_for_equal_payloads():
    assert json_patch({"a": 1}, {"a": 1}) == []


# ── 4. 全端点泄露扫描（单一出口）────────────────────────────

def test_all_client_exits_route_through_the_filter():
    """快照 / SSE / trace 三处都必须过 `filter_for_viewer`。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in (
        "trpg/api/deps.py",
        "trpg/api/routes/play.py",
        "trpg/api/routes/trace.py",
    ):
        src = (root / rel).read_text(encoding="utf-8")
        assert "filter_for_viewer" in src, f"{rel} 没有走统一出口"


def test_framework_has_no_world_faction_words():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "trpg" / "api" / "views.py"
    ).read_text(encoding="utf-8")
    for bad in ("狼", "村民", "悍跳", "屠边", "银水"):
        assert bad not in src, f"框架层不许出现世界词：{bad}"
