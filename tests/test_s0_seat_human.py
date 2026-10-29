"""S4-3（纯声明层 + 前端契约）：席位声明、真人席位切换、真人面板三块。

**这一份不 import API 层** —— 并发实例正在改 `trpg/api/routes/worlds.py`，
仓库当前可能处于不可导入的中间态；把纯逻辑与前端契约拆开，
即使 API 暂时不可用，这部分断言仍然能跑（API 侧见 `test_s0_seat_human_api.py`）。

三块面板：**当前可见信息** + **带理由的建议** + **自己的操作按钮**；
其中「建议」**不许代替人决策** —— 只填输入框，不自动提交。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


# ── 席位声明 ────────────────────────────────────────────────
def test_seat_config_falls_back_to_rules_layer():
    from trpg.rules.seats import load_seat_config

    cfg = load_seat_config(ROOT, "werewolf")
    assert cfg, "规则层必须有席位声明兜底（rules/seats.yaml）"


def test_seat_declarations_expand_by_index():
    from trpg.rules.seats import seat_declarations

    seats = seat_declarations(ROOT, "werewolf", 3)
    assert len(seats) == 3
    assert [s["index"] for s in seats] == [1, 2, 3]
    assert all(s["label"] for s in seats), "每席都要有 label"
    assert seats[0]["human"] is True, "声明里的默认真人席位要生效"
    assert "{index}" not in seats[1]["label"], "label 模板要替换成实际号位"


def test_human_seat_ids_maps_seat_number_to_character():
    from trpg.rules.seats import human_seat_ids

    cards = {"chr_a": object(), "chr_b": object(), "chr_c": object()}
    assert human_seat_ids(cards, [1, 3]) == ["chr_a", "chr_c"]
    assert human_seat_ids(cards, [9]) == [], "越界号位要跳过（宁可少一个真人席位）"
    assert human_seat_ids(cards, [0, -1]) == []


def test_seat_names_are_neutral():
    """席位声明不许含世界语义（框架层不该知道阵营/身份）。"""
    from trpg.rules.seats import seat_declarations

    banned = ["werewolf", "wolf", "village", "seer", "witch", "hunter", "狼", "村民", "预言"]
    for seat in seat_declarations(ROOT, "werewolf", 8):
        text = f"{seat['label']} {seat.get('agent', '')}".lower()
        for word in banned:
            assert word.lower() not in text, f"席位声明含世界语义 {word}: {seat}"


# ── 前端 ────────────────────────────────────────────────────
def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_room_page_seat_can_be_switched_to_human():
    page = _src("pages/SetupPage.vue")
    assert "toggleHuman" in page, "房间页要能切换「换成本人」"
    assert "human_seats" in page, "建局要提交真人号位"
    button = page.split("换成本人")[0].rsplit("<button", 1)[-1]
    assert "disabled" not in button, "「换成本人」按钮不能再是 disabled 灰显"


def test_human_panel_has_three_blocks():
    panel = _src("components/HumanPanel.vue")
    assert "hp-visible" in panel, "面板要有「当前可见信息」块"
    assert "hp-suggestions" in panel, "面板要有「带理由的建议」块"
    assert "hp-action" in panel and "hp-submit" in panel, "面板要有自己的操作按钮"


def test_suggestion_does_not_decide_for_player():
    """建议**只填输入框**，绝不自动提交 —— 建议不代替人决策。"""
    panel = _src("components/HumanPanel.vue")
    body = panel.split("function applySuggestion")[1].split("}")[0]
    assert "action.value" in body, "建议应把依据填进行动输入框"
    assert "submit" not in body.lower(), "建议不得触发提交"
    assert 'emit("submit")' not in body, "建议不得触发提交事件"


def test_existing_contract_ids_untouched():
    """面板的契约 id 一个都不能改（改之前先看 tests/test_web_ui.py）。"""
    panel = _src("components/HumanPanel.vue")
    for eid in (
        "human-panel", "hp-who", "hp-countdown", "hp-state", "hp-briefs", "hp-enemies",
        "hp-thought", "hp-action", "hp-skill", "hp-intent", "hp-mode", "hp-target",
        "hp-submit", "hp-ghost", "hp-hint",
    ):
        assert f'id="{eid}"' in panel, f"契约 id #{eid} 丢了"


def test_pending_carries_visible_and_suggestions_source():
    """后端 pending 要带可见信息与带理由的建议（源码断言）。"""
    play = (ROOT / "trpg" / "api" / "routes" / "play.py").read_text(encoding="utf-8")
    assert '"visible"' in play, "pending 要带 visible"
    assert '"suggestions"' in play, "pending 要带 suggestions"
    assert '"reason"' in play, "建议要带依据 reason"
